# Tasks

按组推进，每组独立验收。

## 组 1：claim.py 删除池内共享

- [ ] 删除 `sync_pool_claimable_plans` 函数（525-549）。
- [ ] 删除 `pool_active_plans` 函数（642-676）。
- [ ] `preview_plans`：`for p in (*upstream_plans, *pool_active_plans())` → `for p in upstream_plans`。
- [ ] `preview_plans`：删除 `sync_pool_claimable_plans(plans)` 行，保留 `sync_account_claimable_plans(account, plans)`。
- [ ] `_auto_pick_plan`：`for p in (*(claimable_plans), *pool_active_plans())` → 只遍历 claimable_plans。
- [ ] 验收：ruff + 相关单测（确认无 `pool_active_plans`/`sync_pool_claimable_plans` 引用残留）。

## 组 2：sentinel.py 删除池内共享

- [ ] 删除 import `pool_active_plans`、`sync_pool_claimable_plans`。
- [ ] 哨兵补领（220-225）：`active_pids` 改为只来自各账号自身 `claimable_plans`，不再聚合 `pool_active_plans`。
- [ ] `_claim_new_plans_across_pool`（271）：删除 `sync_pool_claimable_plans(new_plans, accounts=all_jwt)`。
- [ ] 验收：ruff + 哨兵相关单测。

## 组 3：前端删除池内推断

- [ ] 删除 `getPoolKnownActivityPlans` 函数。
- [ ] `hasUnclaimedActivity` 删除 `getPoolKnownActivityPlans` 分支，只保留 `claimable_plans` 检查。
- [ ] 删除 `getPoolKnownActivityPlans` 的其他调用点（claimStripHtml / 领取流程 / claim 相关）。
- [ ] 验收：前端静态检查（无 `getPoolKnownActivityPlans` 残留）。

## 组 4：存量数据清理 + CI + 部署

- [ ] 部署后清空 11 个被污染账号的 `claimable_plans`（触发 refresh 重新 preview，或直接清空）。
- [ ] `ruff check app tests`、`mypy app/constants.py`、`pytest` 全量通过。
- [ ] 本机 commit + push；VPS `git fetch && reset --hard` + `systemctl restart zcode2api`。
- [ ] 真实验证：`国际-ok li` / `lidmuw`（已持有 referral）外的账号，不再显示「可领取」；`claimable_plans` 恢复为空。
