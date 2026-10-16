# Proposal

## Why

2026-10-13 在 107 VPS（`/root/code/dengyie-zcode2api`，commit `03a441d`）取证确认：网关"请求失败"不是验证码被上游拒绝，而是**端口级求解压力**叠加三项既有缺陷。

`get_healthy_proxies()` 对每个大区只取**首个健康端口**（循环内 `break`），于是补货算力全部压在这些端口上；而账号请求出口走的是 `resolve_proxy()` 的 `md5(account_id) % 健康端口集合`。用项目自带 `solver.js` 在同一大区实测：

| 端口 | 是否被补货压 | 结果 | 单次耗时 |
| --- | --- | --- | --- |
| 21301（JP 首端口，refill 目标） | 是 | 1/3 成功 | 14.0 / 17.6 / 18.8s（`sdk_failed`） |
| 21303（JP 第三端口） | 否 | 3/3 成功 | 6.9 / 6.9 / 6.8s |
| 21203（TW 第三端口） | 否 | 2/2 成功 | 6.5 / 6.3s |

单端口连打 5 次：1/5 成功，吞吐 0.79 枚/min。即被压端口的有效产能塌到约 1.4 枚/min，而该端口要保持 2 枚/75s 的库存需要 1.6 枚/min —— **永远补不满**。

三项叠加缺陷：

1. **算力错配**：8 个大区全部预热（每区 2 枚 = 16 枚），但当时只有 JP/TW 共 3 个可服务账号 → 约 75% 求解算力产出无人能消费的 token。
2. **库存不等式不成立**：16 枚 / TTL 75s = 需 12.8 枚/min，本机单槽实测产能 4.0–4.9 枚/min → 池的稳态是"空"。实测池命中率（`t_first<3s`）仅 4.9%–13.8%。
3. **出口漂移**：`resolve_proxy` 用"健康端口数"作哈希分母，端口熔断会让账号出口改变 → 已预热 token 作废，且同一账号在出口间跳动。

后果（`/admin/api/monitoring` 25.3 小时 117 条 + journald 1 小时）：约 90% 请求在请求路径内现解，`t_first` p50 **11.8s**、p90 **19.1s**、max 40.1s；6 条 499「客户端断开」；求解失败（`sdk_failed` 58 + `solver_budget_exhausted` 16）被 `record_result` 记成账号 `last_error="人机校验失败"`。

## What Changes

- **稳定出口绑定**：`resolve_proxy` 改为"全量端口列表基准索引 + 环形避让"。熔断只产生临时 failover，解除后回到原绑定端口；分母不再随健康集合变化。
- **预热域收窄**：新增 `AccountClientPool.serviceable_ports()`，补货只针对**当前可服务账号实际绑定的端口**；无可用账号的大区不再预热。
- **每端口水位 1**：`CAPTCHA_REGIONAL_POOL_MIN` 默认 2 → 1（产率 0.8 枚/min/端口，低于实测压力阈值）；删除从未被读取的 `CAPTCHA_POOL_MIN` / `CAPTCHA_POOL_MAX` 死配置。
- **端口级失败退避**（新增）：同一端口连续 2 次求解失败后退避 45–90s（随机化），前台落入退避窗口时立即失败换号/回退，不再继续踩被压端口。
- **预算收敛**：`CAPTCHA_SOLVE_TIMEOUT` 默认 40 → 25 秒（实测 `sdk_failed` 耗时 13–20s；40s 只是白占唯一求解槽）。
- **落盘证据**（新增）：`data/evidence.db` 记录每次请求结果与每次求解事件（端口、source、reason、耗时、是否池命中），保留 ≥7 天，并提供 `GET /admin/api/evidence/summary`。

## Impact

- 代码：`app/settings.py`、`app/client_pool.py`、`app/captcha.py`、`app/reqlog.py`、`app/routes/admin_api.py`，新增 `app/evidence.py`。
- 契约：`regional-captcha-waterfall`（预热域、水位、退避、稳定绑定）；新增能力 `captcha-egress-evidence`。`claim.py` / `quota.py` / `install.py` 只消费既有 `get_verify_param()` / `resolve_proxy()`，签名不变。
- 配置：`.env` 删除死配置两行；`CAPTCHA_REGIONAL_POOL_MIN` 默认值变化即生效（`.env` 未设置该键）。
- 无数据库迁移（证据库为独立 SQLite，首次启动自建）；无新依赖。
- 预期：3 个端口 × 0.8 枚/min = 2.4 枚/min 需求（占低压产能 8.5 枚/min 的 28%），到达间隔 p50 28s < TTL 75s，消费即触发补货 → 稳态命中，首字回到 1–3s 量级。

## Non-goals

- 不改上游协议、不引入浏览器求解器、不改 `solver.js`。
- 不提升求解并发（保持单核 1 槽；收窄后仅占 28% 产能，余量留给 SSE）。
- 不换 VPS 规格。
- 不做账号批量迁移（只改绑定算法本身）。
- 不新增验证码求解的全局失败缓存。

## 验收限制（如实标注）

4 个 Plan 账号的 `plan.ends_at = 2026-10-13 23:59:59`，经用户确认（Q1 选 C）接受到期后全线失败。因此本变更的 DoD 为：**行为测试 + 真实端口压力曲线 + 落盘证据**；"新账号入库后 20 请求端到端验收"列为显式待办，未完成前不宣称端到端已验证。
