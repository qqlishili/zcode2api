# Tasks

依据 [design.md](./design.md) 与两份增量规范实施；实际执行与 VPS 验收证据见 [execution.md](./execution.md)。真实端到端验收受账号到期限制（用户已确认 Q1-C 接受），单列最后一项待办并如实标注未完成。

## 1. 稳定出口绑定

- [x] 1.1 `AccountClientPool.resolve_proxy` 改为"注册表全量端口基准索引 + 环形避让"（`_binding_index` / `_bound_port`）；熔断解除后回到原绑定端口。回归 `test_region_routing.py`、`test_client_pool.py`、`test_client_pool_fence.py`、`test_node_circuit_breaker_fsm.py::test_resolve_proxy_avoids_quarantined_ports` 全部通过。
- [x] 1.2 `test_egress_binding_stability.py`（7 例）：绑定索引用全量端口列表、熔断前后回到原端口、健康子集变化不影响其它账号、同区无合格端口返回 None、`serviceable_ports` 过滤耗尽/冷却/Key 模式账号、空账号池返回空集合。

## 2. 预热域收窄

- [x] 2.1 新增 `AccountClientPool.serviceable_ports(accounts)`：只取 `uses_plan_channel()` 且 `is_selectable()` 的账号的绑定端口，去重保序；高频路径不产生 failover/离线日志。
- [x] 2.2 `CaptchaManager._refill_loop` / `_refill_batch` 改用 `_refill_targets()`；无可服务端口时只做过期清理，不发起求解。
- [x] 2.3 `test_refill_domain.py`（5 例）：目标来自 `serviceable_ports`、无端口时不调度、有端口时逐个补货、水位 1 时不再补货、空仓时才求解。**VPS 实测**：预热端口收窄到 TW/JP 绑定端口（无 HK/SG/US/KR/EU/OTHER），速率 1.2 枚/min（原设计目标速率 12.8 枚/min）。

## 3. 水位、预算与死配置

- [x] 3.1 `CAPTCHA_REGIONAL_POOL_MIN` 默认 2 → 1（注释改为"每端口水位"）；`CAPTCHA_SOLVE_TIMEOUT` 默认 40 → 25。
- [x] 3.2 删除 `CAPTCHA_POOL_MIN` / `CAPTCHA_POOL_MAX`（settings、captcha.py）与 `.env` 两行死配置；`.env.example` 同步（VPS `.env` 已清理，`grep CAPTCHA .env` 为空）。
- [x] 3.3 水位断言落在 `test_refill_domain.py`（已有 1 枚即不再补货）；全仓已无未使用的池配置常量（ruff + grep 复核）。

## 4. 端口级失败退避

- [x] 4.1 `_note_port_success` / `_note_port_failure` / `port_backoff_remaining`：阈值 2、窗口 `CAPTCHA_PORT_COOLDOWN`(60s) × uniform(0.75,1.5)；成功与水库命中清零。
- [x] 4.2 退避期语义：池命中照常返回；池空立即抛 `CaptchaSolveError("solver_port_backoff")`；`_refill_single_proxy` 跳过；不触碰断路器。**VPS 实测**：`端口 21302 连续 2 次求解失败，退避 85s`，补货随即转移到同区 21303。
- [x] 4.3 `solver_port_backoff` 进入 `_LOCAL_CODES` 白名单（`test_port_backoff.py::test_port_backoff_diagnostic_survives_whitelist`）。
- [x] 4.4 `test_port_backoff.py`（6 例）：第 3 次调用不新建 Node、失败不外溢到其它端口、命中清零计数、补货跳过退避端口、断路器状态为空、白名单保留诊断码。

## 5. 落盘证据

- [x] 5.1 新增 `app/evidence.py`：`data/evidence.db`(WAL)、`requests`/`solve_events` 两表、7 天保留（`prune()`）、白名单字段、写入失败只 warning。
- [x] 5.2 `reqlog.finish_ok/finish_error` 落库请求；`captcha` 在求解结束与水库命中处落库求解事件（含端口、source、reason、耗时、`hit`）。
- [x] 5.3 `GET /admin/api/evidence/summary?hours=`（请求成功率/`t_first` 分位/求解成功率/命中率/按端口与原因分布）；VPS 实测返回 200 且含 `by_port`。
- [x] 5.4 `test_evidence.py`（6 例）+ `tests/integration/test_evidence_endpoint.py`（3 例，含鉴权与"磁盘上不含 prompt/参数"断言）。

## 6. 文档与术语

- [x] 6.1 `docs/development/01-architecture.md` §4.7 改写"预解池多大区均衡采样"为"按可服务账号绑定端口预热 + 每端口水位 1"，并补端口级退避与稳定绑定；§4.7 其余 40s 表述改为 25s；模块清单补 `evidence.py`。
- [x] 6.2 `docs/development/06-dev-guide.md` 更新 25s 预算、水位与退避参数、落盘证据查询入口与排障指引；`03-data-formats.md` 环境变量表同步；`07-roadmap.md` 原条目加修订说明。
- [x] 6.3 `CONTEXT.md` 增补术语：出口绑定、出口漂移、预解 token 池、池水位、端口产率、落盘证据（只定义概念，不含实现细节）。

## 7. 验证

- [x] 7.1 `ruff check app tests` 通过；全量回归分片执行（unit 556 + integration 390 = **946** 项）：`tests/unit` 仅 3 项失败（`test_notify.py`）、`tests/integration` 仅 16 项失败（`test_oauth_login.py` / `test_oauth_protocol.py`），**全部为本机 Windows 系统代理导致的 `Invalid port: ':1]'`，并已在基线 `03a441d` 的独立 worktree 上完全复现**，与本次改动无关；其余 927 项（含网关/验证码/出口/监控集成用例与全部 26 项新增用例）通过。整进程单跑因这些网络超时用例在本机极慢，改用分片 + 基线对照取证。
- [x] 7.1b **VPS 干净环境复跑**（受影响模块 + 全部新增用例，51 项）：`ruff` 通过、仅 1 项失败（`test_client_pool.py` 的既有注册表隔离缺陷，已在基线用同一数据目录复现），新增 26 项全绿；同日发现并修复"测试污染真实证据库"（`3c12dfc`，清理 555 请求 + 60 求解，复验后污染归零）。
- [x] 7.2 真实端口速率复核：部署后 10 分钟窗口 12 次求解 = 1.2 枚/min；健康端口成功率 **21200 4/4、21303 3/3、21205 1/1（100%）**，求解耗时 6.5–8.5s；唯一失败集中在历史反复熔断的 21302（0/2，`solver_budget_exhausted`），已被端口退避隔离。对照改造前：被压端口 1/3 成功、14–19s。
- [x] 7.3 以 **git 流程**发布 107 VPS：本地 4 个提交（`03f91b7` / `fc2c0aa` / `df372d0` / `3c12dfc`）→ push `origin/main` → VPS 侧 `git stash` + 备份删除未跟踪冲突文件 + `git pull --ff-only` → 重启（`active`、`NRestarts=0`、banner `v2.6.7`、18088 监听）；备份另存 `/root/backup-2.6.6/`（含 pre-deploy 打包与 untracked 备份）。`data/evidence.db` 建库并有请求/求解记录；`/admin/api/evidence/summary` 正常；load average 2.24 → **0.15–0.39**，可用内存 652MB → 822MB。

## 8. 待办（本轮无法完成，须如实标注）

- [ ] 8.1 新 JWT 账号入库后执行 20 请求端到端验收（`t_first` p95 ≤ 5s、无 499/503、水库命中率 ≥80%）；在此之前不宣称端到端已验证。当前账号池 13 exhausted / 1 active / 1 cooling，4 个 Plan 已于 2026-10-13 23:59:59 到期。
