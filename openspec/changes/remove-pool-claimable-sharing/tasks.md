# Tasks

按组推进，每组独立验收。

> 2026-10-16 核销：共享函数、跨账号广播与前端池内推断已从当前代码移除，哨兵相关隔离测试通过。组 1 / 2 的验收项同时包含 Ruff，未因部分验证完成而勾选；历史部署、数据清理记录见 `execution.md`，当前线上和页面仍待复验，不重复清库。详见 [项目交接](../../../docs/HANDOFF.md)。

## 组 1：claim.py 删除池内共享

- [x] 删除 `sync_pool_claimable_plans` 函数。
- [x] 删除 `pool_active_plans` 函数。
- [x] `preview_plans`：只遍历 `upstream_plans`，不合并池内活动。
- [x] `preview_plans`：删除 `sync_pool_claimable_plans(plans)` 行，保留 `sync_account_claimable_plans(account, plans)`。
- [x] `_auto_pick_plan`：不使用池内活动兜底，只从本号候选中选择。
- [x] 验收：ruff + 相关单测（确认无 `pool_active_plans`/`sync_pool_claimable_plans` 引用残留）。

## 组 2：sentinel.py 删除池内共享

- [x] 删除 import `pool_active_plans`、`sync_pool_claimable_plans`。
- [x] 哨兵补领：只使用各账号自身 `claimable_plans`，不再聚合 `pool_active_plans`。
- [x] `_claim_new_plans_across_pool`：删除 `sync_pool_claimable_plans(new_plans, accounts=all_jwt)`。
- [x] 验收：ruff + 哨兵相关单测。

## 组 3：前端删除池内推断

- [x] 删除 `getPoolKnownActivityPlans` 函数。
- [x] `hasUnclaimedActivity` 删除 `getPoolKnownActivityPlans` 分支，只保留 `claimable_plans` 检查。
- [x] 删除 `getPoolKnownActivityPlans` 的其他调用点（claimStripHtml / 领取流程 / claim 相关）。
- [x] 验收：前端静态检查（无 `getPoolKnownActivityPlans` 残留）。

## 组 4：存量数据清理 + CI + 部署

- [x] 核对 `execution.md` 所记的 11 个账号数据清理结果及当前数据；不因清单旧勾选缺失而重复清库。
- [x] `ruff check app tests`、`mypy app/constants.py`、`pytest` 全量通过。
- [ ] 核对历史发布与当前部署版本；如需发布，遵循开发指南的 Git 快进流程，不以 `reset --hard` 覆盖未知改动。
- [ ] 真实验证：逐号以本号 preview 对照管理 API、持久化数据和上线页面 JS；无资格账号不因他号持有 referral 而显示可领取，有本号其他活动资格时正常展示；补验弹窗空结果、切号及旧响应隔离，不主动领取。
