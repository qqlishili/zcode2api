# Proposal

## Why

2026-10-14 排查发现探针整轮停滞：`probe_all_candidates` 只以 `HTTP 200` 判定节点健康，且 `main()` 要求池内存在 `status == 'active'` 的账号。VPS 实测 16 个账号全部为 `exhausted`（额度耗尽但凭证有效），据此判定整轮 `probe_account_missing` 未提交，143 个节点中 81 个长期停留在 `stale`，注册表仅剩 62 个可用出口。

实测同时证明两件事：以项目自身的 `app.agent.build_request` 构造请求时，`exhausted` 账号经任意节点均可返回 `code=1005 exceed quota limit`（链路端到端打通，仅无额度）；而手写裸请求会因缺失追踪头与 body 变换被上游判为异常形态而返回 3012。因此健康判定必须复用项目既有的业务错误归一化，不能自行解释状态码。

## What Changes

- 探活判定改复用 `app.routes.gateway._classify_business_error`：将上游响应归一为有效状态码，只有网络层失败才计为节点失败；额度耗尽（402）、限流（429）、验证码挑战（403）等业务响应均视为链路可达。
- 账号候选条件由 `status == 'active' and enabled` 放宽为凭证有效即可（JWT 或 API Key 存在且启用），使额度耗尽账号可承担探针，探活本身不再消耗额度。
- 单节点请求超时由固定 3.5s 放宽为可配置值；`trace` 定位保持独立超时。
- 单节点在计数失败前允许一次重试，避免请求形态抖动直接判死节点。
- 整轮失败语义不变：无可发布 active 出口仍保留最后完整快照（既有 `no_active_egress` 栅栏）。

## Capabilities

### New Capabilities

- `probe-node-health`：探针节点可达性判定、账号候选资格与整轮未提交语义。

### Modified Capabilities

- `isolated-egress-probing`：细化「基础设施失败不得转换为节点健康」在单节点健康判定上的实现，明确业务响应与网络失败的区分。

## Impact

- 实施影响 `scripts/mihomo_auto_probe.py` 的 `probe_all_candidates` 与 `main()` 账号前提检查；不改动探测实例隔离、注册表发布、节点退役及消费者排空链路。
- 复用既有 `app/routes/gateway.py` 判定函数与 `app/constants.py` 业务码分组，不新增依赖、不新增常驻服务。
- 新增单元用例覆盖业务响应判活、网络失败判死、账号候选放宽与整轮前提；同步 `docs/development/` 相关小节并保持代码中文注释与提交风格一致。
