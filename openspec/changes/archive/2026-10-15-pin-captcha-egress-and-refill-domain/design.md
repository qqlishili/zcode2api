# Design

## 1. 证据（2026-10-13，107.174.244.167 / racknerd-38eb587）

### 1.1 端口压力对照（`/tmp/bench_ports.py`，项目自带 `solver.js`，串行 + 10s 间隔）

| 端口 | 是否被生产补货压 | 成功 | 单次耗时 |
| --- | --- | --- | --- |
| 21301（JP 首端口 = `get_healthy_proxies()` 结果） | 是 | 1/3 | 14.0 / 17.6 / 18.8s，`sdk_failed`，`param_chars=null` |
| 21303（JP 第三端口，无人压） | 否 | 3/3 | 6.9 / 6.9 / 6.8s，`param_chars=280` |
| 21203（TW 第三端口，无人压） | 否 | 2/2 | 6.5 / 6.3s |

单端口连打 5 次（`/tmp/bench_solver.py`）：1/5 成功，wall 76.3s，吞吐 0.79 枚/min。

推论：**求解成功率与耗时是"每端口产率"的函数，不是 CPU 或额度的函数**。低压端口 ≈ 6.5–6.9s、≈100% 成功 ⇒ 单槽产能 8.5–9 枚/min；被压端口 1/3 成功、14–19s ⇒ 有效产能 ≈1.4 枚/min。

### 1.2 运行态计量

- 请求监控（内存，25.3h，117 条）：108×200、6×499「客户端断开」，**无 5xx**。
- `t_first`：p50 **11.8s**、p90 **19.1s**、max 40.1s；>15s 占 28%。
- 池命中判据（`t_first<3s`）：juvduv 4.9%、jatnea 13.8%、juelaj 11.5%、laozuf 11.1% ⇒ **约 90% 请求走现解**。
- journald 单小时：captcha 事件 1511 条（`queue` 1219 / `run` 292），`solve_ms` p50 6.9s、p90 15.8s、max 39.5s；失败 `sdk_failed` 58 + `solver_budget_exhausted` 16（≈run 的 25%）、`param_too_short` 重试 12；`source` refill:foreground = 1520:31；`queue_wait_ms>0` 14 次、max 26.1s。
- 资源：1 vCPU、load 1.38–2.24；内存 1967MB（可用 652MB），swap 已用 842MB；11 个 node 进程，单 `solver.js` RSS ≈216MB。
- 账号：15 个 zai JWT —— 3 active（JP×2 / TW×1）、11 exhausted、1 cooling；`risk_strikes` 全 0（**没有风控封禁**，排除"人机校验=封号"）。
- 断路器：21202 / 21302 / 21401 反复"半开探针失败，重新熔断隔离 600s"。

### 1.3 键空间错配（代码复刻比对）

补货目标（`get_healthy_proxies()`）：`HK 21100 / TW 21200 / JP 21301 / SG 21400 / US 21500 / KR 21600 / EU 21700 / OTHER 21800`
请求出口（`resolve_proxy()`）：`md5(account_id) % 该区健康端口`

| 账号 | 大区 | 实际出口 | 补货目标 | 命中可能 |
| --- | --- | --- | --- | --- |
| juvduv@jkkks.com | JP | 21301 | 21301 | ✓（但该端口被压坏） |
| jatnea@jkkks.com | JP | 21303 | 21301 | ✗ 永不可能 |
| juelaj@stxtc.com | TW | 21203 | 21200 | ✗ 永不可能 |

端口熔断时 `len(healthy_proxies)` 变小 ⇒ 同一账号出口改变 ⇒ 已预热 token 因 `identity` 不符被 `popleft` 丢弃（`captcha.py:434`），且上游会看到同一账号换 IP。

## 2. 第一性原理推导

设：端口 `p` 的可服务账号到达率为 `λ_p`（枚/min），token 寿命 `TTL`（min），单端口产率上限 `S_p`（枚/min，随端口压力下降）。

1. **命中条件**：请求到达时该端口必须存在未过期 token ⇒ 稳态库存 `N_p ≥ λ_p × TTL`。
2. **可持续条件**：`N_p / TTL ≤ S_p`，即 `λ_p ≤ S_p`。
3. 实测 21301：`S_p ≈ 1.4`，需求 `N_p/TTL = 2/1.25 = 1.6 > 1.4` ⇒ **不可持续**，这正是"池恒空 + 求解器恒忙"的成因。
4. 若预热域收窄到真实使用的 3 个端口、水位取 1：需求 = `3 × 1/1.25 = 2.4` 枚/min，而 3 个端口都处于低压 ⇒ `S ≈ 8.5` 枚/min ⇒ 占用 28%，有 3.5 倍余量。
5. 水位 1 并不牺牲命中：到达间隔 p50 28s < TTL 75s，且消费即触发补货（8s 内补齐）；只有"补货在飞时又来请求"才现解。

结论：**把稀缺产能从"8 个大区各自的 1 个端口"重新分配到"3 个真实使用的端口"，并把水位从 2 降到 1，就同时消除了浪费与压力**。提高并发（1→2）在此并非必要，反而会抢占 SSE 的事件循环；因此保持 1 槽，把 40s 预算降到 25s 以释放被白占的槽时间。

## 3. 设计决策

### D1 稳定出口绑定（替代哈希健康集合）

```
base = md5(account_id) % len(region_proxies)      # 分母 = 注册表全量端口，稳定
for k in 0..len(region_proxies)-1:
    p = region_proxies[(base + k) % len(region_proxies)]
    if not quarantined(p) and eligible(p): return p
return None                                        # 同区无合格出口 → 栅栏（RegionOfflineError）
```
- 稳态下永远返回同一端口（满足 `test_region_routing` / `test_client_pool` 的"恒定"断言）。
- 熔断期按固定顺序 ring failover；解除后自动回到原端口（**不产生永久漂移**）。
- 保序使用注册表 `regions` 列表顺序（该顺序由发布流程决定，跨刷新稳定）。

### D2 预热域 = 可服务账号绑定端口集合

新增 `AccountClientPool.serviceable_ports(accounts)`：
- 只取 `account.uses_plan_channel()` 且 `account.is_selectable()` 的账号（与 `_gate_open` 同一判据）；
- 对每个账号取 `resolve_proxy(account)`，去重保序；
- 返回 `[]` 时补货循环只做过期清理，不做任何求解。

`_refill_loop` 与 `_refill_batch` 都改用该集合（不再使用 `get_healthy_proxies()`，该函数保留给其它只读诊断用途）。

### D3 每端口水位 1 / 预算 25s / 死配置清理

- `CAPTCHA_REGIONAL_POOL_MIN` 默认 2 → 1；上限 5 不变。注释与文档同步说明"每端口水位"语义。
- 删除 `CAPTCHA_POOL_MIN` / `CAPTCHA_POOL_MAX`（`captcha.py:39-40` 只赋值、全仓无读取点）与 `.env` 两行。
- `CAPTCHA_SOLVE_TIMEOUT` 默认 40 → 25（`ZCODE_CAPTCHA_TIMEOUT` 仍可覆盖；测试用 monkeypatch 不受影响）。

### D4 端口级失败退避

- 仅在**实际求解失败**（`_solve_one_unlocked` 用尽尝试）时对 `proxy` 计数；成功（含从池命中）清零。
- 连续 2 次失败 → `cooldown = now + CAPTCHA_PORT_COOLDOWN × uniform(0.75, 1.5)`（默认 60s ⇒ 45–90s）。
- 退避期内：`get_verify_param` 命中池仍正常返回；池空则立刻抛 `CaptchaSolveError("solver_port_backoff")`（不占槽、不新建 Node），由网关按既有 `captcha_error` 换号/回退路径处理；`_refill_single_proxy` 同样跳过。
- 该诊断码加入 `_LOCAL_CODES` 白名单，保证管理端可见且不泄漏任何参数内容。
- 与既有"请求内失败复用"（`failures` 字典）互补：后者是请求级，本项是端口级、跨请求。

### D5 落盘证据

- 新模块 `app/evidence.py`：独立 SQLite `data/evidence.db`（WAL），两张表：
  - `requests(req_id, ts, endpoint, model, stream, account, mode, ok, status, error, t_first, t_total, in_tok, out_tok)`；
  - `solve_events(ts, solve_id, source, port, stage, reason, stop_reason, attempts, solve_ms, queue_wait_ms, config_ms, param_chars, exit_code, hit)`。
- 白名单字段：不含 `verifyParam`、token、jwt、cookie；`error` 截断 200 字符。
- 写入路径：`reqlog.finish_ok/finish_error`（请求）与 `captcha`（求解/命中）。
- 保留：每次写入后按计数节流执行 `DELETE ... WHERE ts < now - 7d`。
- 观测端点：`GET /admin/api/evidence/summary?hours=24` 返回请求成功率、`t_first` 分位、求解成功率、池命中率、按端口/按 reason 分布。
- **纯观测**：任何写入失败只 warning，绝不抛出、不改业务语义。

## 4. 取舍与反例

| 备选 | 否决理由 |
| --- | --- |
| 提高求解并发到 2–3 | 收窄后仅用 28% 产能，提并发只会抢 SSE 事件循环；实测端口压力才是瓶颈，不是槽数 |
| 维持 8 大区预热 | 当时 6 个大区无可用账号，产出必然作废；且把首端口压到 1/3 成功率 |
| 保留水位 2 + 拉长 TTL | 水位 2 把端口推到 1.6 枚/min（实测失败区），拉长 TTL 提高旧 token 被 3007 拒绝的概率 |
| 请求到达即现解、彻底去掉池 | 首字将稳定落在 6.5–9s，不满足"客户端体感"验收口径 |
| 用真实浏览器/Frida 求解 | 依赖更重、内存更贵（2GB 机上不可行），且不解决端口压力 |

**最强反例**：若真实到达率远高于 p50 28s（例如持续 2 请求/秒），水位 1 仍会频繁现解。此时正确做法是先看落盘证据里的命中率，再决定"提水位/提并发/加端口"，而不是预先堆高水位——这正是 D5 存在的原因。

## 5. 验收设计

| 层级 | 证据 |
| --- | --- |
| 单元 | 稳定绑定（熔断前后回归原端口）、`serviceable_ports` 过滤、水位 1、连续 2 次失败退避/成功清零、诊断码白名单、证据库写入与保留 |
| 集成（Mock 上游） | 网关在端口退避时按 `captcha_error` 换号而非裸 500；补货只对绑定端口发起（用 stub 记录调用参数） |
| 真实端口压力曲线 | 收窄后重跑三端口对照：每端口产率 ≤0.8 枚/min 时成功率 ≥90% |
| 落盘证据 | 部署后 `evidence.db` 有请求与求解事件，`/admin/api/evidence/summary` 返回命中率 |
| 待办（未验收） | 新账号入库后 20 请求端到端（`t_first` p95 ≤ 5s、无 499/503） |
