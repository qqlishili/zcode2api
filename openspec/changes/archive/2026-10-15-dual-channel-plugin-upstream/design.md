# Design: 双通道上游请求架构

## 1. 事实基础

### 1.1 实测事实（含可复现脚本）

所有判定均可由 `scripts/diagnostics/` 下的脚本复现（见该目录 README）。

| 事实 | 验证脚本 |
|---|---|
| 手写请求全部 3012 | 2026-10-14 现场，跨账号/跨节点/跨客户端/跨代码版本 |
| 官方 `dress` 直连成功（HTTP 200） | `probe_host_rpc.mjs`、`probe_dress_effect.mjs` |
| `dress` 是必需条件（无则 3012） | `probe_dress_effect.mjs` |
| 代理节点已被 ESA 拉黑（走代理 3012） | `probe_dress_effect.mjs` |
| host.js 可独立运行（init + fetch 打通） | `probe_host_rpc.mjs` |
| 槽位 5 种命名形态全部可用 | `probe_slot_naming.mjs` |
| 改 auth.json **必须重启**才生效 | `probe_auth_reload.mjs` |
| 上游单账号并发上限 = 3 | `probe_concurrency_limit.mjs` |
| 16 槽位 init = 70 ms | `probe_init_cost.mjs` |
| `-Trial` 走活动额度，响应 model 被归一 | `probe_trial_model.mjs` |
| `usage` 是实时数据且结构已知 | `probe_usage_shape.mjs` |
| **6 个模型逐个实发**：`GLM-5.2`/`GLM-5-Turbo` → `3006 model not allowed` | 2026-10-15 逐个 POST `/v1/messages` |
| **`windows[].models` 是账号级**（4 个账号分 3 组） | `usage_shadow` 实测快照 |
| **探针判据**：`3012` 必须判不可达（否则 127 个坏节点被误判为可用） | `test_egress_probe.py` 判据表 |
| **门禁效果**：`proxy` 模式重启后立即发请求 → 200 且零风控 | 端到端烟测 |

### 1.2 官方判据（源码原文）

```javascript
// magpie-plugins/packages/zcode/index.mjs:1310
// zcode.z.ai serves the Start Plan only to what looks like ZCode's own
// request, and turns others away with 405 "request has been blocked due to
// unusual activity" (code 3012). A Start Plan request goes as the plugin
// the plan serves sends it: its headers (startHeaders) and its body (dress).
// The texts are prompt.mjs.
```

---

## 2. 架构

### 2.1 总览

```
                    ┌───────────────────────────────────────────┐
                    │  zcode2api（Python, :18088）               │
   客户端 ─HTTP──►  │                                           │
                    │  ┌─────────────────────────────────────┐  │
                    │  │ 账号池 / 出口池 / captcha / 额度管理  │  │
                    │  └─────────────┬───────────────────────┘  │
                    │        ┌───────┴────────┐                 │
                    │        ▼                ▼                 │
                    │  【通道 A native】  【通道 B plugin】       │
                    │   app/channels/     app/channels/         │
                    │     native.py         plugin_channel.py   │
                    │   （httpx 手写）     （JSON-Lines RPC）     │
                    └──────────────────────┬────────────────────┘
                                           │ stdin/stdout
                                           ▼
                            ┌──────────────────────────────────┐
                            │ bun vendor/magpie_host.js         │
                            │ （常驻单进程，插件宿主）           │
                            └──────────────┬───────────────────┘
                                           │ import()
                                           ▼
                            ┌──────────────────────────────────┐
                            │ vendor/magpie-plugins/           │
                            │   packages/zcode（git submodule） │
                            │ startHeaders + dress + 额度逻辑   │
                            └──────────────┬───────────────────┘
                                           │ HTTPS（经出口）
                                           ▼
                                      zcode.z.ai (ESA)
```

### 2.2 通道抽象契约

```python
class UpstreamChannel(Protocol):
    async def send(
        self,
        account: Account,
        url: str,
        headers: dict[str, str],
        body: bytes,
        proxy: str | None,      # 出口：代理 URL，或 None（直连）
    ) -> httpx.Response: ...

    async def aclose(self) -> None: ...
```

**契约的硬性要求**：

1. **只发一次** —— 返回 `httpx.Response` 或抛 `httpx.HTTPError`；**不换号、不重试**。
   重试/换账号/冷却一律留在 `gateway.py`（`next_account` 闭包 + `_dispatch` 外层 while 是既有调度核心）。
2. **返回的 `httpx.Response` 必须同时支持两种消费方式**：
   - `await resp.aread()`（非流式路径）
   - `resp.aiter_bytes()`（流式路径，`gateway.py:_Upstream.to_streaming` 依赖它）
   `native` 现有实现已满足；`plugin` 通道必须同样满足（见 2.3）。
3. **不得引入 gateway 不认识的新错误类型** —— 否则"gateway 零改动"契约破裂。

### 2.3 `plugin` 通道的流式包装（httpx 官方 API）

```python
class _PluginStream(httpx.AsyncByteStream):
    """host.js 的 head/chunk 事件流 → httpx 可消费的字节流。"""

    def __init__(self, host: "PluginHost", fetch_id: int) -> None:
        self._host = host
        self._id = fetch_id

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for chunk in self._host.iter_chunks(self._id):
            yield chunk
            await self._host.credit(self._id, len(chunk))

    async def aclose(self) -> None:
        await self._host.abort(self._id)


resp = httpx.Response(
    status_code=head["status"],
    headers=head["headers"],
    stream=_PluginStream(host, fid),
    request=httpx.Request("POST", url),
)
```

**依据**：
- `httpx.AsyncByteStream` 是 httpx 0.28.1 的**公开导出**
- `httpx.Response.__init__` 的 `stream` 参数正是为此设计
- magpie 的 Go 侧用同一思路（`internal/plugin/host.go:1576` 自定义 `body.Read`）

#### ⚠️ `credit` 回补的已知偏差（**据官方注释推断，未实测**）

官方注释原文：

> `{method: "credit", params: {id, n}}` gives a fetch back the **n charged bytes its reader took**
> （the payload it read, and **a frame's overhead once the frame is done**）

**即窗口计费 = payload 字节 + 每帧的固定开销**。Go 侧分两次回补：

```go
b.c.credit(n)         // 本次读走的 payload 字节
b.c.credit(b.frame)   // 上一帧已完成 → 归还该帧开销
```

**本实现只按 `len(chunk)` 回补 payload，不含帧开销** → 每次少还一点，
**长响应（1M 上下文）可能因窗口逐渐耗尽而停滞**。

**注**：本条**只据官方注释与 Go 侧代码推断，尚未实测**（未构造出长响应停滞的场景）。
保留在文档中的理由是：若将来出现长响应停滞，这是第一个应当复查的地方。

**缓解措施（必须实现）**：
- `window` 设为 **8 MB**（远大于单帧开销累积），并在实现注释中标注此偏差
- 若实测出现长响应停滞，改为**按帧解析以精确归还开销**，或**周期性重置 fetch**

> 这条偏差**写进代码注释 + 本条**，不允许只在设计文档里提。

### 2.4 host.js 常驻宿主

```python
class PluginHost:
    async def start(self, timeout: float = 5.0) -> dict: ...
    async def fetch(self, *, provider, account, model, npm, url, method,
                    headers, body: bytes, proxy: str | None,
                    window: int = 8 << 20) -> int: ...
    async def iter_chunks(self, fetch_id: int) -> AsyncIterator[bytes]: ...
    async def credit(self, fetch_id: int, n: int) -> None: ...
    async def abort(self, fetch_id: int) -> None: ...
    async def usage(self, provider: str, account: str) -> dict: ...
    async def restart(self) -> None: ...
    def is_alive(self) -> bool: ...      # proc.returncode is None
    async def aclose(self) -> None: ...  # 关 stdin → 等退出 → SIGKILL 兜底
```

**init 参数（实测正确形态）**：

```python
{
    "authPath": str(plugin_auth_path),
    "modelsDevPath": "",
    "piPath": "",
    "directory": str(work_dir),
    "config": {}, "prefer": {}, "options": {},
    "plugins": [{
        "spec": "@magpie-community/opencode-zcode-auth",
        "target": str(vendor_zcode_dir),
    }],
}
```

**fetch 参数（必填，缺 `model`/`npm` 会得 400/3001）**：

| 字段 | 值 |
|---|---|
| `provider` | `"zcode"` |
| `account` | `"zcode#h<sha1[:10]>"` |
| `model` | 客户端原始 model（含 `-Trial`） |
| `npm` | `"@ai-sdk/anthropic"` |
| `url` | `https://zcode.z.ai/api/v1/zcode-plan/anthropic/v1/messages` |
| `method` | `"POST"` |
| `headers` | `{"content-type": "application/json", "anthropic-version": "2023-06-01"}` |
| `body` | **字节串** |
| `window` | `8 << 20` |
| `proxy` | 代理 URL；**直连必须显式传 `"direct"`** |

**RPC 事件路由**：
- 请求 `{id, method, params}`（`id` 自增）→ 响应 `{id, result}` / `{id, error}`
- 流式：`{id, event:"head"}` → `{id, event:"chunk", data:base64}` → `{id, result:null}`
- 无 `id` 的 host 主动事件：`{event:"log"/"toast"/"auth"}` → 归日志
- **未知 `event` 宽松忽略**（向前兼容）

### 2.5 槽位与凭证

**槽位命名（纯函数）**：

```python
def slot_of(account_id: str) -> str:
    return "zcode#h" + hashlib.sha1(account_id.encode()).hexdigest()[:10]
```

**为什么不直接用 account.id**：id 形态不统一（实测有 UUID、`名字-随机hex`、含中文），且长度不固定。
短哈希**定长 10 位纯 ASCII**，纯函数无状态。

**为什么不用序号（`zcode#0`）**：账号增删会导致序号重排 → **路由到错误账号**（隐蔽且危险）。

**为什么不用首槽别名（裸 `zcode`）**：实测"不传 `account` 走首槽"，而首槽取决于 auth.json 顺序 → 同样会漂移。
**全部显式传 `account`。**

#### ⚠️ 槽位名的可读性要求（必须配套）

槽位是哈希，**日志里只有哈希运维无法定位账号**。因此：

- **凡在日志/异常/evidence 中提及槽位，必须同时带 `account.name`**；
- 建议实现 `slot_label(account) -> "zcode#h01387fa952(国际-xk)"` 供日志使用；
- **禁止**只打槽位哈希而不带账号名。

**理由**：本方案的槽位为"定长 + 无状态"付出了可读性代价，必须在日志层补回。

**凭证文件**：

```json
{
  "zcode#h<sha1>": {
    "type": "oauth",
    "access": "zcode",
    "refresh": "{\"jwt\":\"<JWT>\",\"device\":\"<device_mid>\",\"site\":\"zai\",\"base\":\"https://zcode.z.ai/api/v1/zcode-plan/anthropic\"}"
  }
}
```

- 路径 `data/plugin-auth.json`，**权限 600**，**原子替换**（`os.replace`）
- **定位：从 `accounts.db` 派生的缓存**（权威源仍是 `accounts.db`）
- **写入时机**：启动全量写；账号增删 / JWT 变化时**全量重写 + 重启 host**
  （实测：改文件不重启 **不生效**，见 `probe_auth_reload.mjs`）
- `device` = `account.fingerprint.device_mid`（**两通道同设备**，避免"同账号多设备"风控特征）

### 2.6 出口（公共层 + 门禁）

**候选**由 `client_pool` 给出（通道无关的公共层）：账号绑定的端口，被隔离时在**同区**
环形避让，同区无可用则返回 `None`（不跨区）。

**是否采用**由**出口门禁**决定（`plugin_channel.send`，见 §2.6.1）：
```
EGRESS_MODE=direct → 强制直连（诊断用）
EGRESS_MODE=proxy  → 只走「探针已确认可服务」的端口；未确认则直连兜底
```

#### 2.6.1 为什么要门禁（实测踩到的坑）

断路器只记录「**用过之后**」的失败。进程内为空时它**信任所有端口**，于是服务重启后的
首批请求会挨个撞上已被拉黑的绑定端口，把账号打成风控禁用。实测日志：

```
05:31:05  账号 xxx 命中风控 HTTP 405      ← 请求（重启后 12s）
05:31:10  egress 出口探测完成：1/32 可达  ← 探针这时才跑完
```

**探针的价值正是「在请求之前」把端口判成可用/不可用** —— 但只把结果写进断路器、
不用在请求路径上，就等于白探。门禁补的就是这一步。

**直连兜底为什么是对的**：直连实测稳定 200，代价只是暴露 VPS 真实 IP —— 作为
「不确定时的兜底」而非默认，比「拿被拉黑的端口去撞并污染账号状态」好得多。

#### 2.6.2 探针（asyncio 后台任务，与 captcha.refill_loop 同模式）

| 项 | 实现 |
|---|---|
| 周期 | `ZCODE_EGRESS_PROBE_INTERVAL`（默认 600 s）；启动延迟 `EGRESS_PROBE_START_DELAY`（默认 5 s） |
| **候选范围** | **账号实际使用的大区里的端口**（实测 32 个；全局池 127 个） |
| **判据** | 见 §2.6.3 |
| 账号 | 轮换（避免单账号频率特征） |
| 并发 | 计入账号并发上限（与用户请求共享） |
| 结果 | 写回 `client_pool.record_proxy_result()`（复用既有三态断路器）+ DB meta |

**范围为什么按大区**：出口按大区绑定，且隔离时只在**同区**环形避让（`resolve_proxy`）。
探未使用的大区纯属无用流量 —— 实测把一轮从 ~3 分钟压到 ~33 秒，且不损失覆盖面。
这也就是方案里「只扫活节点白名单」的真实含义：白名单不是「所有可用节点」，而是
「账号真正会走到的那些节点」。

#### 2.6.3 判据：回答的是「该出口能否服务对话请求」

```
归一后 200            → 可达
归一后 402 / 403      → 可达（出口通了，障碍在账号侧：额度耗尽 / 验证码挑战）
归一后 405 或码 3012  → 不可达（该出口已被上游拉黑）
5xx / 网络层失败      → 不可达
```

**刻意不照搬 `scripts/mihomo_auto_probe.py` 的 `node_reachable`**：那个函数回答
「**链路是否通**」，把 405/3012 也算通（对节点发现是对的 —— 节点确实到达了 ESA）。
探针要判断节点**能否用于对话**，3012 必须判不可达。首次实现时照搬了它，实测
**127 个已被拉黑的代理被全部误判为「可达」（显示 7/8 可达的假象）**，修正后才显示
真实分布（1/32）。

**为什么不是「必须 200 且 body 含 type=message」**：额度耗尽（归一 402）、验证码挑战
（归一 403）同样证明出口链路端到端打通（连接建立、上游受理、鉴权通过）。用「必须 200」
会把「好出口 + 空账号」误判成坏出口 —— 实测 `:21200` / `:21202` 正是这种情况，
用额度充足的账号复验后它们确实返回 200。

### 2.7 额度旁路对照（零决策影响）

```python
# 随现有账号轮询节奏触发（ZCODE_QUOTA_REFRESH_INTERVAL）
plugin_usage = await plugin_host.usage(provider="zcode", account=slot)
store.set_setting("plugin_usage_shadow", json.dumps(plugin_usage))
# ↑ 只落库供比对，quota.py 的判定逻辑完全不变
```

实测 `usage` 返回结构见 `probe_usage_shape.mjs`：
```json
{
  "plan": "ZCode Start Plan",
  "until": "2026-10-18T15:59:59.000Z",
  "error": "",
  "windows": [
    {"name": "GLM-5.3", "display": "3358 / 3000000", "limit": 3000000,
     "resetsAt": "2026-10-15T15:59:59.000Z", "span": 86400,
     "models": ["glm-5.3", "GLM-5.3-Trial"]},
    {"name": "GLM-5.3-Flash", "display": "64200 / 5000000", "limit": 5000000, ...}
  ]
}
```

**边界 1（已实测）**：`windows[].models` 是**账号级**，不是 plan 级。2026-10-15 实测
4 个可读账号分成 3 组：同为 `ZCode Global Build`，一个账号只有 `GLM-5.3-Flash` 桶，
另一个还有 `GLM-5.3` 桶。**所以「取某一个账号的 models」会漏** —— `/v1/models` 因此
取**并集**（见 §5.4）。

**边界 2（已实测）**：`usage` 把普通与 `-Trial` 消耗**计入同一窗口**，无法细分到池。
**接受**（对照目的是总量一致性）。

**边界 3（已实测）**：额度耗尽的账号会返回非空 `error`
（`this account has no GLM Coding Plan, and ZCode's Start Plan has ended or was never
started`）。统计「读取成功」时**只在 `error` 非空时判失败** —— 正常返回里 `error` 是
**空字符串**，按「键是否存在」判断会把成功账号记成失败（首次上线日志显示
`0/13 账号读取成功`，而实际有 4 个正常）。

### 2.8 生命周期（`main.py` lifespan）

```
启动顺序：
  ... 既有 ...
  captcha_manager.start()
  sentinel.start()
  plugin_host.start()            ← 新增（同步等 init，超时 5 s）
  就绪标记

关闭顺序（反向）：
  account_client_pool.stop_coordinator()   ← 先停请求
  plugin_host.aclose()                      ← 再停宿主
  captcha_manager.close()
```

**就绪探测**：每次请求检查 `plugin_host.is_alive()`，不健康即走 `native`。**不引入心跳定时器**。

### 2.9 选路

```python
channel_key = settings.REQUEST_CHANNEL          # "native" | "plugin"
if channel_key == "plugin" and plugin_host.is_alive():
    ch = plugin_channel
else:
    ch = native_channel                          # 兜底/降级
return await ch.send(account, url, headers, body, proxy)
```

**单请求只走一条**（并行会让上游看到双倍流量，加重风控）。

---

### 2.10 插件能力边界（复核结论）

接入插件时必须知道它**能回答什么、不能回答什么**。以下结论来自源码与实测，写在这里
以免后来的读者把插件当作它并不承担的权威。

#### ① `providers.models` **不是**本网关的套餐模型清单

插件里那份模型表是**读到上游配置之前的兜底**：

```javascript
// magpie-plugins/packages/zcode/index.mjs:35-42
// ZCode's models before its config is read; the Start Plan has all but GLM-5.3.
const MODELS = [ GLM-5.3, GLM-5.3-Flash, GLM-5.2, GLM-5-Turbo ]
const START_MODELS = MODELS.filter((m) => m.id !== "GLM-5.3")
```

**权威来源是 `modelsOf(cfg, planID(base))`** —— 它读的是 **ZCode 客户端自己的
`config.json`**（`providerConfigRules.providerRules[providerId].builtinModelIds`）。
本 VPS **没有安装 ZCode 客户端** → `cfg` 为空 → **永远走兜底表**。

**实测**：兜底表里的 `GLM-5.2` / `GLM-5-Turbo` 发给上游得到
`400 {"code":3006,"msg":"model not allowed"}`；而注释声称「Start Plan has all but
GLM-5.3」的 `GLM-5.3` **实测可用** —— **注释与实测矛盾，兜底表不可信**。

#### ② `usage` 才是可用的实测口径

`usage` 的 `windows[].models` 来自上游 `billing/balance`，是**账号实际拥有的桶**。
它是账号级的（见 §2.7 边界 1），因此汇总时取并集。

#### ③ `-Trial` 的真实语义（源码级）

```javascript
// index.mjs:1550-1566
let m = modelOf(orig)
if (m.toLowerCase().endsWith("-trial")) {
  const raw = giftModel(gift, m)              // 剥掉 -Trial 得原始名
  const available = raw && giftUsable(s, gift, raw)
  if (!available) return 429/400              // 桶不可用直接拒
  orig = JSON.stringify({ ...JSON.parse(orig), model: raw })
  forcedGift = true
}
start = alone || forcedGift                    // ← 决定走哪条路
```

而 `alone` 来自 `plansOf(s)`：

```javascript
// index.mjs:206-208
if (isTeam(s) || !s.jwt) return { start: false, gift: null }
if (!s.key) return { start: true,  gift: null }   // ← 我们的账号走这支
```

**本网关的派生凭证只有 `jwt`、没有 `key`** → `!s.key` → **`start: true`**。

**结论**：
- **传不传 `-Trial`，`start` 都是 `true`** → 都走 `START_BASE` + JWT + `startHeaders` + `dress`
- 两者**唯一实质差异**：`-Trial` 会在**发送前**检查该桶是否可用，不可用直接返回
  429/400，**不浪费一次上游请求**
- 所以「`-Trial` 强制走活动额度」的说法不准确 —— **无 key 账号本来就走活动额度**

#### ④ 本网关未实现、且已知的 Go 侧机制

magpie 的 `internal/plugin/host.go` 有几个本实现没有的机制。复核后判定：

| Go 侧机制 | 作用 | 我们的判定 |
|---|---|---|
| `retire()` + `retireWait = 10min` | 旧宿主拖完存量请求再停 | **并入 P7**（"账号变化重启"时一起做；当前 `restart()` 从未被调用） |
| `maxWireLine = 32MB` | 单行上限 | **低风险**：插件侧 `MAX_FRAME = 64KB`，base64 后 ~87KB，远低于上限 |
| `headCost` / `HEAD_MAX = 64KB` | head 体积核算 | **不需要**：插件侧在写出前已经拒绝超限 head |
| 停顿看护（消费者僵死） | 防消费者既不读也不关 | **不需要**：`_PluginStream.aclose` 已发 `abort` |

---

## 3. vendor 层

```
zcode2api/
├── .gitmodules
└── vendor/
    ├── magpie-plugins/          ← git submodule（3.2 MB，只用 packages/zcode）
    └── magpie_host.js           ← 复制单文件（89 KB）+ 来源标注
```

**host.js 文件头标注**（必须）：
```javascript
// vendored from yetone/magpie
// path: internal/plugin/host.js
// commit: <40 位 sha>
// fetched_at: <ISO 日期>
// 更新方式：人工确认（协议规格；替换前需跑 plugin 通道回归）
```

**复用边界**：只用 `packages/zcode`。

---

## 4. 更新机制

| 组件 | 频率 | 方式 |
|---|---|---|
| `magpie-plugins`（插件） | 每日 | **自动**：`git fetch` → 有新提交则 `submodule update --remote` → 重启 host → smoke test → 失败回滚 |
| `magpie_host.js` | 每周 | **仅告警**：比对上游 sha256，有差异走 Bark 通知 + 管理页标记，**不自动替换** |

---

## 5. 已评估并否决的清理项

以下三项原计划随本方案一并"破坏性清理"，**逐项复核后全部否决**。保留在此是为了让
未来的读者知道它们**已经评估过、且有实测依据**，避免重复提案。

### 5.1 否决：删除「切 API Key 回退」分支

**原计划**：`has_apikey_fallback()` 恒 `False` → 删除 401/403/429/风控 四处回退分支。

**否决依据**：
1. **`return False` 是有意设计**，不是遗漏。`models.py:122` 的 docstring 原文：
   > 纯免费赠送池模式下，OAuth 附属生成的 zcode-api-key 在 api.z.ai **恒为 0 余额
   > （HTTP 429 code 1113）**，启用回退会导致**故障号滞留轮询池并制造无效报错**，
   > 故**固定关闭**。
2. **不是"四处"，是五处 `if` + 一处活的条件判断**：`gateway.py:1412/1528/1557/1569/1597`
   是调用点，而 `models.py:167` 的
   `if self.status in (Status.INVALID, Status.DISABLED) and not self.has_apikey_fallback():`
   是**活着且语义正确**的判断 —— 删函数就要改它，改动面超出"清理"。

**保留**：函数与其调用点原样不动。若将来上游恢复附带 Key 的余额，改这一处 `return` 即可。

### 5.2 否决：`/v1/models` 从插件动态获取

**原计划**：改成读取插件 `providers.models`（当时看到它返回 6 个模型，以为"更全"）。

**否决依据（实测）**：

| 实测 | 结果 |
|---|---|
| 逐个模型发真实请求 | `GLM-5.2` / `GLM-5-Turbo` → **HTTP 400 `3006 model not allowed`** |
| 查插件源码 | `providers.models` 在 `cfg` 为空时**回退到硬编码 `MODELS`**（`index.mjs:36-42`） |
| 查注释 | `index.mjs:35`：*ZCode's models **before its config is read*** —— 它是**读不到配置时的兜底猜测** |

即：**插件列出 6 个 ≠ 本网关注册的套餐能用 6 个**。照搬会**把不可用的模型列给客户端**，
比硬编码更误导；而 `/v1/models` 还**从未被调用过**（`journalctl` 全时段命中 0 条）。

**改用的方案**：从 `usage_shadow` 的实测读数推导 —— 见 §5.4。

### 5.3 否决：精简 `MODEL_NAME_MAP`

**原计划**：只保留 `codex-auto-review`，其余改为"在动态模型表里做大小写无关匹配"。

**否决依据**：该表是**客户端兼容层**（把小写别名、`codex-auto-review` 这类客户端专有名
归一到上游官方名），与"模型清单是否动态"无关。实测客户端提交的正是官方名
（`GLM-5.3-Flash` 108 次），它是**归一化表而非清单**，没有精简的动机。

### 5.4 采纳：`/v1/models` 由实测读数推导

**做法**：`usage_shadow.available_models()` 取**所有启用且 JWT 有效账号**在插件 `usage`
里报出的 `windows[].models` **并集**，剥离 `-Trial` 别名，经 `MODEL_NAME_MAP` 归一为
官方写法。读数缺失时回退 `constants.AVAILABLE_MODELS`。

**为什么取并集**：实测同一 plan 下不同账号的桶不同 —— `ZCode Global Build` 的一个账号
只有 `GLM-5.3-Flash` 桶，另一个还有 `GLM-5.3` 桶。取单账号会漏。

**为什么剥离 `-Trial`**：它是同一桶的另一个名字，不是独立模型。`magpie-plugins` 的
`giftID` 注释原文：*the picker reads `GLM-5.3-Trial` while the loader strips it back
to the raw name*。

**实测结果**：`['GLM-5.3-Flash', 'GLM-5.3']` —— 与硬编码**数值一致**，但**会随账号与活动
变化自动跟随**，不必改代码。

---

## 6. 明确不改（边界纪律）

以下**本次不得改动**（防止"顺手重构"）：

| 模块 | 约束 |
|---|---|
| `app/quota.py` | **判定逻辑完全不动**（套餐识别 / 桶剩余 / 耗尽判定均保持现状）。插件 `usage` 只做旁路对照 |
| `app/captcha.py` | **不动**（预解池、求解器调度、`refill_loop` 均保持现状） |
| `app/client_pool.py` 的哈希路由 | **只新增"出口公共化"接口**（供两通道取同一 proxy），**不改一致性哈希与粘性绑定的既有行为** |
| `app/routes/gateway.py` 的调度核心 | `_dispatch` 外层 while / `next_account` 闭包 / 16 处错误分支的**语义**不动（只删除不可达分支，见 §5） |
| `read=None` | **保持不动**（有意的长思考流式设计），仅记录为已知限制 |
| 18089（`ref-zcode2api`） | **不属本项目**；本方案不改动它，也不引用它的行为作为需求 |

---

## 7. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| 插件内部 API 变更 | 适配失效 | 只依赖 `init`/`fetch`/`usage` 三个公开 RPC；版本变化时回归测试会红 |
| host.js 协议变更 | RPC 格式变化 | 人工确认更新；解析层对未知 event 宽松忽略 |
| Bun 缺失/损坏 | plugin 通道不可用 | 启动预检 → 不可用则**强制降级 native** + 告警 |
| host 进程卡死 | RPC 超时 | 每次请求 `is_alive()` 探测；连续 3 次失败标记不健康，冷却 5 min |
| 上游并发上限（=3） | 429/3008 | 保持 `ACCOUNT_CONCURRENCY=2`；扫描请求计入同一计数 |
| 代理节点全黑 | plugin 通道也失败 | 直连兜底（已实测 200） |
| 插件更新引入回归 | 线上故障 | 更新后 smoke test；失败自动回滚 submodule |
| `credit` 少还帧开销 | 长响应可能停滞 | `window` 设 8 MB；实现注释标注偏差；实测停滞则按帧精确归还 |
| JWT 明文落盘 | 泄露 | 权限 600；日志只允许前 8 字符 |

---

## 8. 验证计划

### 8.1 单元测试

| 测试 | 覆盖 |
|---|---|
| `test_plugin_host_rpc.py` | init / fetch / 并发 / 未知 event / aclose 无残留进程 |
| `test_channel_contract.py` | 两通道返回同构 `httpx.Response`，且 `aread()` 与 `aiter_bytes()` 都可用 |
| `test_plugin_stream.py` | `_PluginStream` 逐块 yield + credit 调用 |
| `test_slot_naming.py` | `slot_of()` 定长 10 位、纯 ASCII、确定性 |
| `test_plugin_auth_inject.py` | 权限 600；日志不含完整 JWT；日志含账号名（不只哈希） |
| `test_channel_selection.py` | 默认 native；plugin 不健康 → 降级；配置值 |
| `test_exit_probe.py` | 判据（200 + `"type":"message"`）/ 冷却 / 轮换 |
| `test_usage_shadow.py` | 异常不影响主流程；不新增表；不新增定时器 |
| `test_vendor_sync.py` | 无新版本无副作用；更新失败能回滚；host.js 差异仅告警 |

### 8.2 集成验证

1. Bun 缺失 → 启动不崩，降级 native
2. `plugin` 通道直连 → 真发一次，断言 200 且响应含 `"model":"glm-5.3-flash"`
2b. 出口探针 → 逐出口探测，断言**判据**：`200`/`402`/`403` 判可达，`405`/码 `3012` 判不可达
2c. 出口门禁 → `EGRESS_MODE=proxy` 且探针未确认任何出口时，请求必须**直连兜底**且零风控日志
3. 同账号两通道 → 走同一出口、同一 device
4. 账号 A/B 的槽位互不可见
5. 重启 host → 70 ms 内就绪
6. 杀掉 host → 下次请求自动走 native

### 8.3 生产烟测

```
1. ZCODE_REQUEST_CHANNEL=native 重启 → 发请求 → 200（行为同改动前）
2. 切 plugin 重启 → 发请求 → 200 且响应 model 正确
3. 检查请求监控：通道字段显示 plugin
4. 检查 plugin-auth.json 权限 600
5. 切回 native → 复验可回退
```

---

## 9. 分阶段交付

| 阶段 | 内容 | 独立验收 |
|---|---|---|
| **P1** | vendor 层 + Bun 预检 | `git submodule status` + `bun --version` |
| **P2** | `channels/base.py` + `native.py`（搬移现有逻辑） | 全量单测通过且**零行为差异** |
| **P3** | `plugin_channel.py`（host 客户端 + 流式包装） | 单测 + 手动 fetch 200 |
| **P4** | 配置项 + 选路 + lifespan 挂载 | 切换通道发请求 |
| **P5** | 出口扫描 + 白名单 | 扫描报告 |
| **P6** | `usage` 旁路对照 | DB meta 有数据 |
| **P7** | 更新机制 + 版本监控 | 定时任务日志 |
| **P8** | 清账（三项破坏性清理复核后**全部否决**；改为 `/v1/models` 由实测读数推导） | 全量回归 |
| **P9** | 生产部署 + 烟测 | 端到端 200 |
