# gateway.py「发上游请求 → 失败 → 换账号重试」控制流梳理

分析目标：`<repo>/app/routes/gateway.py`（1718 行，只读分析，未修改任何文件）。
所有行号以该文件为准。

---

## 1. 重试循环的结构

### 1.1 常量定义

**L69-L70**
```python
MAX_CAPTCHA_RETRIES = 3
MAX_ACCOUNT_ATTEMPTS = 5
```
一句话：两个上限常量，前者限「验证码同账号刷新次数」，后者限「真正进入 `_try_account` 的账号次数」。

### 1.2 外层账号循环：`_dispatch`

**L793-L816**
```python
async def _dispatch(req_id, body, incoming_headers, port, provider):
    """多账号会话粘性亲和 + 轮询故障转移调度：_Upstream（成功）或 JSONResponse（错误）。

    单账号并发限制：选号后若该账号在飞请求已达上限（store.account_concurrency，
    0 = 不限），跳过换下一个账号——不排队（流式请求可占槽位数分钟，排队会
    放大延迟甚至吊死客户端）。满号跳过不计入 MAX_ACCOUNT_ATTEMPTS（只计真正
    进入 _try_account 的次数）。全满/无号 → 503。
    """
    tried: set[str] = set()
    observations: dict = {}
    failures: dict = {}
    captcha_failures: dict[str, CaptchaSolveError] = {}
    limit = _limit()
    attempts = 0
    ...
    while attempts < MAX_ACCOUNT_ATTEMPTS:
```

一句话：`_dispatch`（L793-L888）是唯一的账号级重试循环，`MAX_ACCOUNT_ATTEMPTS` 只作为 `while` 条件被消费一次。

### 1.3 `attempts` 的累加与「满号不计次」的实现

**L844-L865**
```python
        tried.add(account.id)
        if limit > 0 and _inflight.get(account.id, 0) >= limit:
            failures[account.id] = _AccountFailure("local_concurrency_limit", "local_scheduler")
            logs.warn(req_id, f"账号 {account.name} 并发已满（{_inflight.get(account.id, 0)}/{limit}），切换下一个")
            continue
        needs_captcha = provider == "zai" and account.uses_plan_channel()

        slot_box: list[str | None] = [None]
        if limit > 0:
            _inflight[account.id] = _inflight.get(account.id, 0) + 1
            slot_box[0] = account.id
        try:
            result = await _try_account(
                req_id, account, body, incoming_headers, port, needs_captcha, slot_box,
                captcha_failures=captcha_failures,
            )
        except BaseException:
            ...
        if isinstance(result, _AccountFailure):
            attempts += int(result.attempted)
```

一句话：`tried` 保证同请求内账号不重复；并发满时 `continue` 不计入 `attempts`；`attempts += int(result.attempted)` 由 `_AccountFailure.attempted` 决定是否计次（验证码获取失败时 `attempted=False`，即不计次）。

### 1.4 循环出口与 503

**L866-L888**
```python
        attempts += 1
        if isinstance(result, _Upstream):
            _bind_sticky_account(affinity_key, account.id)
            held = slot_box[0]
            slot_box[0] = None
            if held is not None:
                result.on_close = _make_slot_releaser(held)
            return result
        if slot_box[0] is not None:
            _release_slot(slot_box[0])
            slot_box[0] = None
        if isinstance(result, JSONResponse) and result.status_code < 400:
            _bind_sticky_account(affinity_key, account.id)
        return result

    error, diagnostics = _unavailability(req_id, provider, req_model, observations,
                                        failures, attempts >= MAX_ACCOUNT_ATTEMPTS)
    ...
    return JSONResponse({"error": error}, status_code=503, headers=headers)
```

一句话：成功（`_Upstream`）或「可直接回传客户端的 4xx JSONResponse」立即 `return` 结束循环；只有 `_AccountFailure` 才真正回环继续；循环自然耗尽后聚合诊断并返 503。

---

## 2.「换下一个账号」的机制

### 2.1 `next_account(...)` 的真实定义（闭包，不是方法）

**L1373-L1380**
```python
        def next_account(code, origin="upstream", stage="upstream"):
            return _AccountFailure(code, origin, stage, raw_http_status=raw_status,
                                   effective_status=effective_status, business_code=business_code,
                                   deadline=max(d for d in (deadline, wait_deadline) if d is not None)
                                   if origin in ("upstream", "transport") and (deadline is not None or wait_deadline is not None) else None,
                                   state_code=account.selection_exclusion(), history=list(history),
                                   history_truncated=history_truncated, captcha=captcha_details,
                                   attempted=attempted)
```

一句话：`next_account` 是 `_try_account_admitted` 内定义的**纯构造器**——它自己不换号，只打包一个 `_AccountFailure` 证据对象返回给调用方；真正的「换下一个账号」发生在 `_dispatch` 收到 `_AccountFailure` 后的 `continue`（L865）。

### 2.2 参数语义

| 参数 | 含义 | 取值 |
|---|---|---|
| `code` | 换号原因码 | 见第 3 节全部枚举 |
| `origin` | 失败来源域 | `upstream`（默认）/ `local_scheduler` / `transport` / `network_routing` |
| `stage` | 失败阶段 | `upstream`（默认）/ `credential` / `captcha` / `captcha_reacquire` / `retry_reacquire` / `connect` / `read` / `region` |

一句话：三元组 `(code, origin, stage)` 只用于日志与诊断聚合，不影响调度分支。

### 2.3 证据对象 `_AccountFailure`

**L926-L944**
```python
class _AccountFailure:
    """换号证据，不携带账号身份或上游原文。"""

    code: str | None
    origin: str
    stage: str = "dispatch"
    at: float = field(default_factory=time.time)
    raw_http_status: int | None = None
    effective_status: int | None = None
    business_code: str | None = None
    deadline: float | None = None
    state_code: str | None = None
    history: list = field(default_factory=list)
    history_truncated: int = 0
    captcha: dict = field(default_factory=dict)
    attempted: bool = True
```

一句话：`attempted` 字段是唯一影响循环计数的字段（L865 读它）。

### 2.4 全部调用点（28 处）

| 行号 | 调用 | 分类 |
|---|---|---|
| 1417 | `next_account("local_concurrency_limit", "local_scheduler", "captcha_reacquire")` | 换号 |
| 1420 | `next_account("captcha_error", "local_scheduler", "captcha")` | 换号 |
| 1423 | `next_account("local_concurrency_limit", "local_scheduler", "captcha_reacquire")` | 换号 |
| 1434 | `next_account("credential_invalid", "local_scheduler", "credential")` | 换号 |
| 1439 | `next_account("region_offline", "network_routing", "region")` | 换号 |
| 1454 | `next_account("upstream_transport_error", "transport", "connect")` | 换号 |
| 1480 | `next_account("upstream_transport_error", "transport", "read")` | 换号 |
| 1513 | `next_account("captcha_retry_exhausted")` | 换号 |
| 1541 | `next_account("account_risk_blocked")` | 换号 |
| 1551 | `next_account("quota_exhausted")` | 换号 |
| 1562 | `next_account("credential_invalid")` | 换号 |
| 1574 | `next_account("credential_invalid")` | 换号 |
| 1594 | `next_account("local_concurrency_limit", "local_scheduler", "retry_reacquire")` | 换号 |
| 1612 | `next_account(reason_code)` | 换号 |
| 1628 | `next_account("local_concurrency_limit", "local_scheduler", "retry_reacquire")` | 换号 |
| 1642 | `next_account("upstream_server_error")` | 换号 |

另有一处非换号调用 `next_account(code)`（L1384，位于 `remember()` 内），仅用于把历史事件记入 `history`，返回值不 return。

一句话：16 处 `return next_account(...)` 全部是「放弃当前账号、交给 `_dispatch` 选下一个」，**没有任何一处**是原地重试（原地重试走 `continue`）。

### 2.5 换号的实际执行点

**L860-L865**
```python
        if isinstance(result, _AccountFailure):
            attempts += int(result.attempted)
            failures[account.id] = result
            if slot_box[0] is not None:
                _release_slot(slot_box[0])
                slot_box[0] = None
            continue
```

一句话：释放并发槽 → 记录失败 → `continue` 回到 `while` 顶部重新选号。

### 2.6 选号函数

**L816-L834**
```python
        account = None
        if not tried and prefer_sticky:
            account = _get_sticky_account(provider, affinity_key, tried, limit, model=req_model)
        if account is None:
            healthy_regs = (
                account_client_pool.get_healthy_regions()
                if hasattr(account_client_pool, "get_healthy_regions")
                else None
            )
            account = store.select(
                provider,
                skip_ids=tried,
                model=req_model,
                avoid_id=avoid_id,
                observations=observations,
                healthy_regions=healthy_regs,
            )
        if account is None:
            break
```

一句话：先尝试会话粘性账号（仅首次），否则 `store.select(skip_ids=tried)` 轮询；`tried` 集合即是「换下一个」的去重依据。

---

## 3. 哪些错误会触发换账号（完整分支表）

### 3.1 分流前置：业务错误码归一化

**L1466-L1469**
```python
                text = preloaded_bytes.decode("utf-8", "ignore")
                status_code, _biz_code, is_concurrency_limit = _classify_business_error(
                    status_code, resp, text,
                )
                business_code = _biz_code
```

一句话：HTTP 200 包装的业务错误 JSON 会在这里被改写成有效状态码（3012→405、3007→403、额度→402、1006→401、并发→429、5xx 类→500），后续所有分支都吃改写后的 `status_code`。

### 3.2 分支总表（按代码顺序，互斥短路）

| # | 行号 | 条件 | 动作 | 账号状态 |
|---|---|---|---|---|
| 1 | L1477 | `status_code >= 400` | 进入错误分支总入口 | — |
| 2 | L1490-L1515 | 验证码挑战（3 形态，**仅在 `needs_captcha` 时检测**） | `captcha_retries+1`；<3 次 → `continue` 同账号重建；≥3 次 → `next_account("captcha_retry_exhausted")` | 不改状态 |
| 3 | L1519-L1541 | `_is_risk_control()` = HTTP 405 或 code 3012 或 `unusual activity` | `ban_for_risk()` → DISABLED；有 Key 回退则 `continue` 换通道，否则 `next_account("account_risk_blocked")` | **DISABLED** |
| 4 | L1543-L1551 | `_is_exhausted()` = HTTP 402 / 额度业务码 / quota 关键词 | `_mark(EXHAUSTED)`（若已是 INVALID/DISABLED 则不洗）；`next_account("quota_exhausted")` | **EXHAUSTED** |
| 5 | L1553-L1562 | `status_code == 401` | `_mark(INVALID)`；有回退则 `continue`，否则 `next_account("credential_invalid")` | **INVALID** |
| 6 | L1564-L1574 | `status_code == 403`（已排除验证码形态） | `_mark(INVALID)`；有回退则 `continue`，否则 `next_account("credential_invalid")` | **INVALID** |
| 7 | L1576-L1594 | `status_code == 429` 且**非**并发类且 `retries_429 < 5` | `_park_slot` → `remember()` → `_sleep(wait)` → `_reacquire_slot()` 成功则 `continue` | 保持 ACTIVE |
| 8 | L1595-L1602 | 同上，但等待后并发已满 | `next_account("local_concurrency_limit", "local_scheduler", "retry_reacquire")` | 保持 ACTIVE |
| 9 | L1603-L1610 | 429 且（并发类 或 重试耗尽）且有 Key 回退 | `force_fallback=True; retries_429=0; continue` | 保持 ACTIVE |
| 10 | L1611-L1618 | 429 且无回退 | `next_account(reason_code)` | 保持 ACTIVE |
| 11 | L1620-L1632 | `status_code >= 500` 且 `retries_5xx < 3` | `_park_slot` → `remember()` → `_sleep(5)` → `_reacquire_slot()` 成功则 `continue` | 保持 ACTIVE |
| 12 | L1633-L1634 | 同上，等待后并发已满 | `next_account("local_concurrency_limit", "local_scheduler", "retry_reacquire")` | — |
| 13 | L1635-L1642 | 5xx 重试耗尽 | 非 INVALID/DISABLED → `COOLING` + 300s；然后 `next_account("upstream_server_error")` | **COOLING** |
| 14 | L1644-L1650 | 400 且 thinking 签名不兼容且未剥离过 | `attempt_body = repaired; continue`（原地重试） | 保持 |
| 15 | L1652-L1666 | 其它 4xx | `fail_count+1`；**直接 `return JSONResponse(..., status_code=status_code)`** | 保持 |

一句话：只有 3/4/5/6/8/10/12/13 会真正换账号，其余是原地重试（2/7/9/11/14）或直接回传客户端（15）。

### 3.3 关键判定函数

**L186-L198 `_is_exhausted`（429 优先级最高）**
```python
def _is_exhausted(status_code: int, text: str) -> bool:
    # 429 是频控信号，优先于一切 body 关键词：429 body 带额度文案时
    # （api.z.ai 实测形态）必须走频控重试，不得判成额度耗尽踢号。
    if status_code == 429:
        return False
    if status_code in constants.EXHAUST_HTTP_STATUSES:
        return True
    code, _ = _extract_business_code(text)
    if code in constants.EXHAUST_BUSINESS_CODES:
        return True
    low = text.lower()
    return any(k in low for k in _EXHAUST_KEYWORDS)
```

**L200-L212 `_is_risk_control`**
```python
def _is_risk_control(status_code: int, text: str) -> bool:
    """风控信号判定（3012「unusual activity」/ messages 端点 405）。

    与验证码挑战互斥：调用点已先排除 challenge 形态。命中即账号级风控，
    直接禁用 Plan 通道（或切 API Key 回退），不做自动退避。
    """
    if status_code in constants.RISK_CONTROL_HTTP_STATUSES:
        return True
    code, _ = _extract_business_code(text)
    if code == "3012":
        return True
    low = text.lower()
    return any(m.lower() in low for m in constants.RISK_CONTROL_MARKERS)
```

一句话：`_is_risk_control` 的 HTTP 判据只有 `(405,)`（constants.py L150），3012 走业务码或 body marker。

### 3.4 超时（无显式 timeout 分支）

**L1445-L1454**
```python
            except httpx.HTTPError as err:
                account_client_pool.end_request(client, probe, success=False)
                account.record_result(False, f"连接失败: {err}")
                # 废 JWT / 风控禁用走 Key 回退失败时不得洗成 cooling，否则冷却结束会重开 Plan
                if account.status in (Status.INVALID, Status.DISABLED):
                    store.update_account(account)
                else:
                    _mark(account, Status.COOLING, f"连接失败: {err}")
                logs.warn(req_id, f"账号 {account.name} 连接失败，切换下一个")
                return next_account("upstream_transport_error", "transport", "connect")
```

**L1470-L1480（读响应超时）**
```python
                except httpx.HTTPError as err:
                    account_client_pool.end_request(client, probe, success=False)
                    await cm.__aexit__(None, None, None)
                    account.record_result(False, f"读取响应失败: {err}")
                    if account.status in (Status.INVALID, Status.DISABLED):
                        store.update_account(account)
                    else:
                        _mark(account, Status.COOLING, f"读取响应失败: {err}")
                    logs.warn(req_id, f"账号 {account.name} 读取响应失败，切换下一个")
                    return next_account("upstream_transport_error", "transport", "read")
```

一句话：超时（`httpx.ConnectTimeout`/`ReadTimeout` 均属 `httpx.HTTPError`）走 `upstream_transport_error`，账号置 **COOLING** 后换号；注意 `read=None`（L48 共享客户端 / client_pool L425），即**读超时实际不触发**，只有连接超时 30s 会走这里。流式传输中断不走换号——此时响应头已返回客户端，`_UpstreamStreamError` 只能往流里补一个 error 帧（L1298-L1304）。

---

## 4. 发请求的那一行

### 4.1 客户端获取（三态分支）

**L1330-L1341**
```python
        try:
            if _get_shared_client is not _default_get_shared_client:
                client = _get_shared_client()
            else:
                operation = await account_client_pool.acquire_operation(account)
                client = operation.client
        except RegionOfflineError:
            return _AccountFailure("region_offline", "network_routing", "region")
```

一句话：生产路径用 `account_client_pool.acquire_operation(account)` 取账号绑定客户端（测试通过 patch `_get_shared_client` 换成共享客户端）。

### 4.2 实际发出上游 POST 的行

**L1440-L1444**
```python
            attempted = True
            try:
                cm = client.stream("POST", url, headers=headers, content=payload)
                resp = await cm.__aenter__()
                cm_closed = False
```

一句话：**L1442 是唯一的真实上游请求行**，用 `httpx.AsyncClient.stream(...)` + 手动 `__aenter__`，从而能在不读 body 的情况下先拿到 `resp.status_code`／响应头，再决定是读全量还是透传流。

### 4.3 URL / 头 / payload 的构造

**L1425-L1432**
```python
            try:
                credential = (account.mode, account.jwt_token, account.api_key)
                url, headers, payload = build_request(account, attempt_body, verify_param,
                                                      incoming_headers, verify_region,
                                                      force_fallback=force_fallback)
            except RuntimeError as err:
                account.record_result(False, f"凭证无效: {err}")
                _mark(account, Status.INVALID, str(err))
                logs.warn(req_id, f"账号 {account.name} 凭证无效，切换下一个")
                return next_account("credential_invalid", "local_scheduler", "credential")
```

一句话：`build_request`（app/agent.py L59）按 provider 与 Plan/Key 通道选 URL 与鉴权头，body 前先 `deepcopy`（agent.py L74）以防跨账号污染。

### 4.4 代理是怎么传进去的

代理**不经过 HTTP 调用参数**，而是绑在 client 实例上，在创建 client 时注入。

**app/client_pool.py L420-L431**
```python
        actual = proxy if proxy is not None else self.resolve_proxy(account)
        if (not self.proxy_eligible(actual) or
                (not actual and (getattr(account, "assigned_region", None) or self._registry.get("slots")))):
            raise RegionOfflineError("账号大区无可用健康出口，禁止直连降级")
        req_timeout = timeout if timeout is not None else httpx.Timeout(
            connect=30.0, read=None, write=120.0, pool=30.0)
        client = httpx.AsyncClient(proxy=actual, timeout=req_timeout, trust_env=False,
                                  limits=httpx.Limits(max_keepalive_connections=10,
                                                      max_connections=20, keepalive_expiry=120.0))
        self._clients[account_id] = (client, time.time())
        self._client_proxies[client] = actual
        self._client_identities[client] = self.proxy_identity(actual)
        return client
```

一句话：`httpx.AsyncClient(proxy=actual, ...)` 在 client 构造时固定代理；`gateway.py` 侧用 `account_client_pool.client_proxy(client)`（L1398）反查回来只用于日志与验证码。

### 4.5 出口健康探针（在发请求前）

**L1437-L1439**
```python
            try:
                probe = account_client_pool.begin_request(client)
            except RegionOfflineError:
                return next_account("region_offline", "network_routing", "region")
```

一句话：`begin_request` 会检查代理熔断器三态，处于隔离期则直接换号（`region_offline`），不发请求。

---

## 5. 响应处理（从 `httpx.Response` 到返回客户端）

### 5.1 状态码与 content-type 分流

**L1458-L1466**
```python
            status_code = resp.status_code
            received_at = time.time()
            raw_status = status_code
            content_type = (resp.headers.get("content-type") or "").lower()
            preloaded_bytes: bytes | None = None
            cm_closed = False
            text = ""
            is_concurrency_limit = False

            if status_code >= 400 or "event-stream" not in content_type:
```

一句话：**只有 HTTP < 400 且 content-type 含 `event-stream` 才走真流式**；其余（含 200 但非 SSE）一律先 `await resp.aread()` 全量读下来（L1469-L1470），这样才可能做业务码解析。

### 5.2 全量读取分支

**L1468-L1488**
```python
            if status_code >= 400 or "event-stream" not in content_type:
                try:
                    preloaded_bytes = await resp.aread()
                except httpx.HTTPError as err:
                    ...
                    return next_account("upstream_transport_error", "transport", "read")
                except BaseException:
                    account_client_pool.end_request(client, probe)
                    await cm.__aexit__(None, None, None)
                    raise
                try:
                    await cm.__aexit__(None, None, None)
                except BaseException:
                    account_client_pool.end_request(client, probe)
                    raise
                cm_closed = True
                text = preloaded_bytes.decode("utf-8", "ignore")
```

一句话：非流式响应读完后立即 `cm.__aexit__` 关闭连接（`cm_closed=True`），响应体以 `preloaded_bytes` 随 `_Upstream` 传给调用方，避免二次读流。

### 5.3 成功分支：结果封装

**L1668-L1694**
```python
            # 成功：记录用量并把打开的上游流交给调用方
            account.use_count += 1
            account.last_used_at = time.time()
            is_stream = bool(body.get("stream")) and "event-stream" in content_type
            if not is_stream:
                account_client_pool.end_request(client, probe, success=True)
                account.record_result(True, f"HTTP 200 · {model_name} · {time.time() - attempt_t0:.1f}s")
            # API Key 回退成功不得把废 JWT / 风控禁用洗成 active，也不得清风控计数
            if account.status not in (Status.INVALID, Status.DISABLED):
                account.risk_strikes = 0
                account.last_error = None
                account.cooling_until = None
                if account.status in (Status.COOLING, Status.EXHAUSTED):
                    account.status = Status.ACTIVE
            store.update_account(account)
            _spawn_bg(_safe_refresh(account))

            result = _Upstream(resp, cm, client, t_first=time.time() - attempt_t0,
                              account_name=account.name, mode=account.mode,
                              preloaded_bytes=preloaded_bytes, cm_closed=cm_closed,
                              on_result=_make_result_recorder(account, credential) if is_stream else None,
                              on_network_result=(lambda success: account_client_pool.end_request(client, probe, success=success))
                              if is_stream else None)
            handed_off = True
            return result
```

一句话：成功时 `_Upstream` 封装响应（流式时把 `on_result`/`on_network_result` 回调挂上去，由消费方在流结束时结算账号状态），`handed_off=True` 使 `finally`（L1696-L1698）不关闭连接。

### 5.4 三个端点各自的「`_Upstream` → 客户端」分叉

**(a) `/v1/messages`（L352-L401）——原样透传**
**L394-L400**
```python
    if isinstance(result, _Upstream):
        # dispatch 返回与流式生成器启动之间的取消窗口：兜底关闭释放并发槽位
        try:
            return result.to_streaming(req_id)
        except asyncio.CancelledError:
            await result.close()
            raise
    return result
```

**(b) `/v1/chat/completions`（L402-L466）——格式转换**
**L447-L466**
```python
    model = str(body.get("model") or "")
    if payload.get("stream"):
        try:
            return _openai_stream_response(result, model, req_id)
        except asyncio.CancelledError:
            await result.close()
            raise

    try:
        raw = await result.aread()
        logs.req_ok(req_id)
    except asyncio.CancelledError:
        reqlog.finish_error(req_id, "客户端断开", status=499, t_first=result.t_first)
        raise
    except Exception as err:  # noqa: BLE001
        logs.req_err(req_id, f"读取上游响应失败: {err}")
        reqlog.finish_error(req_id, f"读取上游响应失败: {err}", status=502)
        return JSONResponse({"error": {"message": f"读取上游响应失败: {err}", "type": "upstream_error"}}, status_code=502)
    finally:
        await result.close()
    data = _safe_json(raw.decode("utf-8", "ignore"))
    if not isinstance(data, dict) or data.get("type") != "message":
        reqlog.finish_error(req_id, "上游响应格式异常", status=502, t_first=result.t_first)
        return JSONResponse({"error": {"message": "上游响应格式异常", "type": "upstream_error"}}, status_code=502)
    usage = data.get("usage") or {}
    reqlog.finish_ok(req_id, t_first=result.t_first, status=result.resp.status_code,
                     **reqlog.extract_usage(usage))
    return JSONResponse(anthropic_to_openai(data, model))
```

一句话：`stream=True` → `_openai_stream_response`；非流式 → `aread()` 后校验 `type == "message"`，再 `anthropic_to_openai` 转成 JSONResponse。

**(c) `/v1/responses`（L519-L589）——同样分叉，非流式走 `anthropic_to_responses`**
**L561-L589** 与 (b) 结构逐字对称，末端为：
```python
    return JSONResponse(anthropic_to_responses(data, model, effort=effort))
```

一句话：三端点统一在 `_dispatch` 之后分叉，唯一的差别是流式包装器与序列化函数。

### 5.5 最终错误出口

**L1652-L1666（其它 4xx 直接回传）**
```python
                account.fail_count += 1
                account.record_result(False, f"HTTP {status_code}: {text[:120]}".replace("\n", " "))
                store.update_account(account)
                logs.req_err(req_id, f"上游错误 HTTP {status_code}（账号 {account.name}）")
                body_log = text if len(text) <= 4000 else text[:4000] + f"...(共 {len(text)} 字节，疑似 WAF 页)"
                logs.warn(req_id, f"上游 {status_code} 完整响应体: {body_log}")
                reqlog.finish_error(req_id, f"HTTP {status_code}: {text[:120]}".replace("\n", " "),
                                    status=status_code, t_first=time.time() - attempt_t0)
                return JSONResponse(
                    _safe_json(text) or {"error": {"message": text[:500], "type": "upstream_error"}},
                    status_code=status_code,
                )
```

**L884-L888（账号耗尽）**
```python
    reqlog.finish_error(req_id, "无可用账号 / 额度均已耗尽 / 并发已满", status=503, diagnostics=diagnostics)
    headers = {"X-Request-Id": req_id}
    if error["retry_after"] is not None:
        headers["Retry-After"] = str(error["retry_after"])
    return JSONResponse({"error": error}, status_code=503, headers=headers)
```

一句话：4xx 原样透传（含上游 body），账号耗尽则聚合 503 + `Retry-After`。

---

## 6. 3012 / 风控判定的位置

### 6.1 关键字出现位置（gateway.py 内共 6 处）

| 行号 | 上下文 | 说明 |
|---|---|---|
| L122 | `"""提取上游 JSON 响应中的业务错误码（如 1005/1006/1302/3002/3007/3008/3012）。"""` | 文档串 |
| L209 | `if code == "3012":` | `_is_risk_control` 内，业务码判据 |
| L235 | `if code == "3012" or _is_risk_control(status_code, text):` | `_classify_business_error` 内，归一为 405 |
| L1520 | `account.record_result(False, f"风控封禁 HTTP {status_code}（3012/unusual activity）")` | 风控分支 |
| L1523 | `f"风控封禁 (3012/unusual activity) HTTP {status_code}，"` | 面向运营的 last_error 文案 |
| L739 | `防止单账号被永久钉死过热触发 3012 风控。` | `_get_sticky_account` docstring（防过热设计） |

相关常量在 `app/constants.py` **L148-L155**：
```python
# ── 风控信号（2026-09-05 3012 事件实证）────────────────────────────────────────
# 3012「unusual activity」= 上游风控（HTTP 405 或包在 200/400 JSON 承载），不在官方公开错误码表；
# 高频请求触发，官方政策定性为临时限制（限流/冻结，3 次以上违规才封号）。
# 处置：禁用 Plan 通道（或切 API Key 回退），人工确认恢复后手动启用。
RISK_CONTROL_HTTP_STATUSES = (405,)
RISK_CONTROL_MARKERS = (
    '"code":3012', '"code": 3012', '"code":"3012"', '"code": "3012"',
    "unusual activity",
)
```

### 6.2 命中后做什么

**L1517-L1541**
```python
                # 风控（3012「unusual activity」/ 405）：真封禁 → 禁用账号，人工恢复。
                # 必须先于 exhausted/其它错误判定，且不再重试（避免对封禁账号持续施压）。
                if _is_risk_control(status_code, text):
                    account.record_result(False, f"风控封禁 HTTP {status_code}（3012/unusual activity）")
                    account.ban_for_risk()
                    account.last_error = (
                        f"风控封禁 (3012/unusual activity) HTTP {status_code}，"
                        f"确认恢复后请在后台手动启用（第 {account.risk_strikes} 次）"
                    )
                    store.update_account(account)
                    if needs_captcha and account.has_apikey_fallback():
                        logs.warn(
                            req_id,
                            f"账号 {account.name} 命中风控 HTTP {status_code}，已禁用 Plan 通道"
                            f"（累计第 {account.risk_strikes} 次），切 API Key 回退",
                        )
                        needs_captcha = False
                        force_fallback = True
                        continue
                    logs.warn(
                        req_id,
                        f"账号 {account.name} 命中风控 HTTP {status_code}，已禁用"
                        f"（累计第 {account.risk_strikes} 次），切换下一个",
                    )
                    return next_account("account_risk_blocked")
```

一句话：**是**——禁用账号（`ban_for_risk` → `Status.DISABLED`）**且**换号；若账号有 API Key 回退能力则先原地 `continue` 切通道，否则 `account_risk_blocked` 换下一个。风控分支**先于** `_is_exhausted`/401/403/429/5xx 判定，因此 405 不会被下面的分支截胡。

**app/models.py L111-L120**
```python
    def ban_for_risk(self) -> None:
        """命中真风控（3012/405「unusual activity」）：禁用账号，UI 展示封禁文案。

        风控由人工确认恢复后在后台手动启用（set_enabled）——不做自动退避恢复，
        避免对真封禁的账号持续产生上游流量。Plan 通道停用；同账号若有
        API Key 仍可走回退通道（is_selectable / uses_plan_channel 分离）。
        """
        self.risk_strikes += 1
        self.status = Status.DISABLED
        self.cooling_until = None
```

一句话：无自动恢复，只能后台手动启用；`risk_strikes` 累加计数，仅在成功且账号非 INVALID/DISABLED 时清零（L1677）。

### 6.3 风控计数对客户端出口熔断的影响

**L1500-L1503**
```python
                challenge = _detect_captcha_challenge(resp, text) if needs_captcha else None
                waf = bool(challenge) or _is_risk_control(status_code, text)
                account_client_pool.end_request(client, probe, success=not waf, is_waf_block=waf)
```

一句话：风控命中会作为 `is_waf_block=True` 反馈给出口熔断器（client_pool L96-L131），可能隔离该代理。

### 6.4 当前实测行为要点

`has_apikey_fallback()` 在 `app/models.py` **L122-L128** **恒返回 `False`**：
```python
    def has_apikey_fallback(self) -> bool:
        """同账号是否持有可走 api.z.ai 的 API Key（JWT 死后的对话回退）。

        纯免费赠送池模式下，OAuth 附属生成的 zcode-api-key 在 api.z.ai 恒为 0 余额
        （HTTP 429 code 1113），启用回退会导致故障号滞留轮询池并制造无效报错，故固定关闭。
        """
        return False
```

一句话：因此 L1527 的「切 API Key 回退」分支**在当前配置下恒不可达**，3012 必然落到 `next_account("account_risk_blocked")`；同理 401/403/429 的 Key 回退分支（L1556/L1568/L1603）也是死代码。

---

## 7. 流式路径

### 7.1 是否流式的判定

**L1670**
```python
            is_stream = bool(body.get("stream")) and "event-stream" in content_type
```

一句话：客户端要 `stream=True` **且**上游 content-type 含 `event-stream`，二者同时成立才算流式；否则即便客户端要流式也走全量读取路径。

### 7.2 `/v1/messages`：原样透传

**L1265-L1325 `_Upstream.to_streaming`（关键行）**
```python
    def to_streaming(self, req_id: str) -> StreamingResponse:
        """原样透传（/v1/messages 直通路径），旁路提取原始输入、输出及缓存用量。"""
        up = self

        async def _body_iter():
            usage = reqlog.extract_usage(None)
            pending_chunk = None
            stream_error = None
            completed = False
            try:
                if up.preloaded_bytes is not None:
                    usage = _extract_usage_from_json_bytes(up.preloaded_bytes)
                    yield up.preloaded_bytes
                else:
                    line_buf = bytearray()
                    async for chunk in up.resp.aiter_bytes():
                        ...
                        yield chunk
```

**L1320-L1324（构造 `StreamingResponse`）**
```python
        return StreamingResponse(_body_iter(), status_code=up.resp.status_code,
                                 media_type=up.resp.headers.get("content-type", "application/json"),
                                 headers={"Cache-Control": "no-cache"})
```

一句话：用 `StreamingResponse` 包一个逐块 `yield` 的异步生成器，从 `resp.aiter_bytes()` 原样透传原始 SSE 字节，`status_code`/`media_type` 直接沿用上游响应头。

### 7.3 SSE 解析与错误检测（透传路径）

**L1284-L1290（旁路解析每一行）**
```python
                            try:
                                evt = _extract_sse_line_event(raw_line)
                                _check_stream_error(evt)
                                completed = completed or isinstance(evt, dict) and evt.get("type") == "message_stop"
                                usage = _extract_event_usage(evt, usage)
                            except _UpstreamStreamError as err:
                                stream_error = err
```

**L1293-L1295（错误帧的空行必须透传完再抛）**
```python
                            # 错误帧的空行也要透传，SSE 客户端收到完整帧才会处理。
                            if stream_error is not None and not raw_line.strip():
                                raise stream_error
```

**L1301-L1303（流式不完整检测）**
```python
                    if "event-stream" in up.resp.headers.get("content-type", "").lower() and not completed:
                        raise _UpstreamStreamIncomplete()
```

一句话：透传同时旁路扫 SSE 事件以提取用量、检测上游 error 帧与「SSE 流未收到 message_stop 就结束」的不完整流；两者都在流内部处理，**不会触发换号**（响应头已发给客户端）。

### 7.4 `/v1/chat/completions`：转换为 OpenAI chunk

**L468-L517 `_openai_stream_response`**
```python
def _openai_stream_response(up: _Upstream, model: str, req_id: str) -> StreamingResponse:
    """把上游 Anthropic SSE 事件流转换为 OpenAI chunk 流。"""
    conv = StreamConverter(model)
    async def _iter():
        usage = reqlog.extract_usage(None)
        completed = False
        try:
            yield conv.start()
            async for line in up.resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if not data_str:
                    continue
                evt = _safe_json(data_str)
                if isinstance(evt, dict):
                    _check_stream_error(evt)
                    completed = completed or evt.get("type") == "message_stop"
                    usage = _extract_event_usage(evt, usage)
                    for out in conv.feed(evt):
                        yield out
            if not completed and "event-stream" in up.resp.headers.get("content-type", "").lower():
                raise _UpstreamStreamIncomplete()
            yield conv.done()
```

**L515-L517**
```python
    return StreamingResponse(_iter(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})
```

一句话：按行读 `aiter_lines()`，经 `StreamConverter` 逐事件转成 OpenAI chunk 后 `yield`，媒体类型固定 `text/event-stream`。

### 7.5 `/v1/responses`：转换为 Responses SSE

**L591-L661 `_responses_stream_response`**，末端 **L661-L662**：
```python
    return StreamingResponse(_iter(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})
```

一句话：结构与 (b) 一致，转换器换成 `ResponsesStreamConverter`。

### 7.6 流式下的资源与账号结算

- 流式时 `account_client_pool.end_request` 与 `account.record_result` **不在此刻执行**，而是以闭包形式挂到 `_Upstream`（L1686-L1690），由流结束/异常时调用；
- 并发槽位释放挂在 `result.on_close`（L869-L871，`_make_slot_releaser`）；
- 所有流式生成器 `finally` 都调 `await up.close()`（L1311、L512、L657），`_Upstream._close`（L1245-L1263）负责 `cm.__aexit__` → `resp.aclose()` → `on_close()` → 释放 `operation`。

一句话：流式路径下账号状态与槽位释放是**延迟到流结束**结算的，因此长流会长时间占用并发额度。

---

## 控制流摘要（伪代码）

```text
_dispatch(req):                                   # L793, while attempts < MAX_ACCOUNT_ATTEMPTS(5)  L816
  while attempts < 5:                             # L816
    account = sticky() or store.select(skip_ids=tried)   # L816-L834
    if account is None: break                     # L835
    tried.add(account.id)                         # L844
    if inflight(account) >= limit: continue        # 满号跳过，不计 attempts  L845-L850
    result = _try_account(account)                # L857   ← 单账号内层循环，见下
    if isinstance(result, _AccountFailure):        # L860
      attempts += result.attempted; continue      # L861-L865  ← 真正的“换下一个”
    return result                                 # _Upstream(成功) 或 4xx JSONResponse  L866-L877
  return 503 aggregated diagnostics               # L879-L888

_try_account_admitted(account, client):           # L1352
  while True:                                     # L1390  ← 单账号内层原地重试循环
    url, headers, payload = build_request(...)     # L1427
    cm = client.stream("POST", url, ...)           # L1442  ← 唯一上游请求行（代理绑在 client 上）
    resp = await cm.__aenter__()
    if status>=400 or not SSE: text = await resp.aread(); classify_business_error()   # L1469-L1469
    if status < 400:
       mark active; return _Upstream(resp, cm, ...)   # L1668-L1694  → 外层 return → 端点包 StreamingResponse/JSON
    # ── 错误分支（顺序即优先级）──
    if captcha_challenge:  retry<3 ? continue : return next_account("captcha_retry_exhausted")  # L1492/1513
    if risk_control(405|3012|unusual activity): ban_for_risk()→DISABLED; return next_account("account_risk_blocked")  # L1519/1541
    if exhausted(402|quota*):  EXHAUSTED;     return next_account("quota_exhausted")       # L1543/1551
    if 401:  INVALID;         return next_account("credential_invalid")                   # L1553/1562
    if 403:  INVALID;         return next_account("credential_invalid")                   # L1564/1574
    if 429:  retry<5 ? sleep+continue :      return next_account(reason_code)              # L1576/1612
    if 5xx:  retry<3 ? sleep+continue : COOLING 300s; return next_account("upstream_server_error")  # L1620/1642
    if 400 thinking_signature: strip; continue                                            # L1644-L1649
    else: return JSONResponse(upstream body, status)   # 不换号，直接回传客户端  # L1664
```
