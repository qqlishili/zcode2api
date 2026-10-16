# 执行记录

## 历史基线对照（git 实证）

- 原始正确版 `preview_plans`（`8dfc762~1`）只调 `/billing/preview` 直接返回，无池内合并/广播。
- `8dfc762`（v2.5.15，2026-09-29，作者 qqlishili）引入 `pool_active_plans`（从池内账号「已持有 plans」反推「可领活动」）。
- `82d21bc`（v2.6.4，2026-10-01，作者 qqlishili）引入 `sync_pool_claimable_plans`（跨账号广播）+ `sync_account_claimable_plans`（单账号落库）。

## 根因

`zcode-v3-start-plan-referral`（夜间蹬蹬卡）是邀请制自动发放活动：不在 preview 列表、由「邀请+新手任务」触发、额度在 `entitlements`。池内共享把「池内账号已持有的 plans」误当「人人可领的活动」，广播污染 11 个未持有账号的 `claimable_plans`，使其误显示「可领取」，哨兵还对无资格账号反复 claim。

## 分组实施

- 组 1（claim.py）：删 `sync_pool_claimable_plans`、`pool_active_plans` 两函数；`preview_plans` 删 `pool_active_plans` 合并与 `sync_pool_claimable_plans` 广播，保留 `sync_account_claimable_plans`（单账号落库）；`_auto_pick_plan` 删 `pool_active_plans` 兜底。验收：51 项 claim 单测通过。
- 组 2（sentinel.py）：删 2 import + 哨兵补领的 `active_pids`（改只依赖各账号 claimable_plans）+ `sync_pool_claimable_plans` 调用。验收：12 项哨兵单测通过（更新 2 处旧「池内共享」断言）。
- 组 3（前端）：删 `getPoolKnownActivityPlans` 函数 + `hasUnclaimedActivity` 池内推断分支 + 2 处调用点。验收：无残留 + 4 个 script 块 node 语法通过。
- 组 4：CI 三关通过；提交 `614b8d3`；VPS 部署；清空 11 个被污染账号 `claimable_plans`（服务内存验证残留 0）。

## CI 与部署

- `ruff check app tests`、`mypy app/constants.py`、`pytest`（全量单元回归）通过。
- 提交 `614b8d3` push 到 origin；VPS `reset --hard` + `systemctl restart zcode2api`，服务 active。
- 存量数据：11 个账号的脏 `claimable_plans=['zcode-v3-start-plan-referral']` 已清空。

## 剩余限制

- 前端「未持有账号不再显示可领取」与「已持有账号展示 +1亿待生效」的最终效果需浏览器页面人工验收（本机无法打开生产页面）。
- 附带提交了 entitlements 展示（`_build_plan_slots.pending` 字段 + 前端 `pendingRows`），与池内共享删除同属 referral 活动修复。
