# 实施任务清单：多轨代理预解水库与三态断路器 (pre-solved-regional-captcha-pool)

## Phase 1: 基础设施与代理亲和预解水库核心 (app/captcha.py)

- [x] 1.1 扩展 `app/settings.py`，新增 `CAPTCHA_REGIONAL_POOL_MIN`、`CAPTCHA_REGIONAL_POOL_MAX`、`CAPTCHA_CIRCUIT_BREAKER_COOLDOWN`、`CLAIM_PROXY_INTERVAL`、`CLAIM_PROXY_MAX_WAIT` 配置项。
- [x] 1.2 在 `app/captcha.py` 实现基于 `collections.deque` 的代理亲和存储结构 `_proxy_pools: dict[str, deque[TokenEntry]]`。
- [x] 1.3 实现 `Singleflight`（单飞防撞）现解任务合并机制（`_in_flight_solves: dict[str, asyncio.Future]`）。
- [x] 1.4 重构 `invalidate(region=None, proxy=None)` 接口，实现大区与代理级靶向清池，彻底消除跨大区误杀株连。
- [x] 1.5 实现 `_seen_certify_ids` 自动剪枝防泄漏定时器（120s TTL）。
- [x] 1.6 实现 CPU 核心数自适应限流信号量（单核钳制为 1，多核 `max(1, cpu_count - 1)`）并加注 10s 获取超时。

## Phase 2: 三态断路器收敛与连接池联动 (app/client_pool.py)

- [x] 2.1 在 `app/client_pool.py` 中建立三态断路器状态机（`CLOSED`、`OPEN`、`HALF-OPEN`），统管所有代理端口健康度。
- [x] 2.2 实现错误分类熔断：网络超时连续 3 次熔断，WAF 明确封禁（F001/3012）连续 2 次快速熔断。
- [x] 2.3 冷却 600 秒后进入 `HALF-OPEN`，放行单次探测请求，探测成功转为 `CLOSED`，失败重新重置 600 秒。
- [x] 2.4 `AccountClientPool.resolve_proxy` 自动规避 `OPEN` 隔离端口，并在大区全灭时抛出 `RegionOfflineError`。
- [x] 2.5 在断路器与 Admin 诊断信息中暴露端口健康状态与隔离剩余倒计时（Observability）。

## Phase 3: 网关路由与活动领取防挤兑改造 (app/routes/gateway.py & app/claim.py)

- [x] 3.1 改造 `app/routes/gateway.py`，取 Token 严格按账号对应代理从水库 O(1) 弹栈；空仓时走 Singleflight 现解。
- [x] 3.2 捕获 3007 挑战时，仅定向调用 `captcha_manager.invalidate(proxy=target_proxy)`，保护其他代理水库。
- [x] 3.3 在 `app/claim.py` 实现 `ClaimPacingGate`：为每个代理端口分配独立 `asyncio.Lock`，强制施加 20s 最小间隔。
- [x] 3.4 为 `claim` 门禁排队增加 45s 超时上限，超过阈值立即抛出 `ClaimPacingQueueFullError` 快速失败，杜绝客户端 499 挂死。
- [x] 3.5 领额度流程全程复用所属代理水库 Token，禁止在领额度阶段同秒唤起 Node 进程。

## Phase 4: 测试套件与回归验证

- [x] 4.1 编写 `tests/unit/test_proxy_affinity_pool.py`：测试代理亲和存储、O(1) 弹栈与 Singleflight 并发合并。
- [x] 4.2 编写 `tests/unit/test_scoped_invalidation.py`：验证 3007 仅清除目标代理/大区 Token，其他大区不受株连。
- [x] 4.3 编写 `tests/unit/test_node_circuit_breaker_fsm.py`：覆盖 CLOSED -> OPEN -> HALF-OPEN 状态转移与探针自愈。
- [x] 4.4 编写 `tests/unit/test_claim_pacing_gate.py`：覆盖 20s 错峰间隔与 45s 排队超时快速失败。
- [x] 4.5 运行全量单元测试（`pytest`），确保全部通过（PASS）。
- [x] 4.6 审查与交接：更新架构文档与路线图，向用户提交完整交付物。
