# Design

## 目标

彻底删除「池内共享」机制，只信上游 `/billing/preview` 接口。以历史正确基线（`8dfc762~1`，即引入 `pool_active_plans` 之前的版本）为参照，逐处对照删减，同时保留后来引入的「单账号落库」良性改进（`sync_account_claimable_plans`）。

## 历史基线对照（git 实证）

- 原始正确版（`8dfc762~1`）的 `preview_plans` 只调 `/billing/preview` 并直接返回，无池内合并、无跨账号广播。
- `8dfc762`（v2.5.15，2026-09-29）引入 `pool_active_plans`（从池内账号「已持有 plans」反推「可领活动」）。
- `82d21bc`（v2.6.4，2026-10-01）引入 `sync_pool_claimable_plans`（跨账号广播 claimable）+ `sync_account_claimable_plans`（单账号落库）。

## 函数存在性与处置对照

| 函数 | 历史基线(8dfc762~1) | 当前 | 处置 |
|---|---|---|---|
| `parse_plan` | ✅ 存在 | ✅ | 保留 |
| `account_held_plan_ids` | ✅ 存在 | ✅ | 保留 |
| `sync_account_claimable_plans` | ❌ 不存在 | ✅ | **保留**（单账号落库，良性，被 11 处调用） |
| `filter_unclaimed_plans` | ❌ 不存在 | ✅ | **保留**（sync_account 的辅助） |
| `sync_pool_claimable_plans` | ❌ 不存在 | ✅ | **删除**（跨账号广播，污染源） |
| `pool_active_plans` | ❌ 不存在 | ✅ | **删除**（从已持有 plans 反推，污染源） |

## 精确删减清单（当前代码行号）

### claim.py

| 行号 | 内容 | 处置 |
|---|---|---|
| 525-549 | `sync_pool_claimable_plans` 函数定义 | **整段删除** |
| 642-676 | `pool_active_plans` 函数定义 | **整段删除** |
| 715 | `preview_plans` 里 `for p in (*upstream_plans, *pool_active_plans()):` | 改为 `for p in upstream_plans:` |
| 727 | `preview_plans` 里 `sync_pool_claimable_plans(plans)` | **删除该行**（保留上一行 `sync_account_claimable_plans(account, plans)`） |
| 738 | `_auto_pick_plan` 里 `for p in (*(...claimable_plans...), *pool_active_plans()):` | 改为只遍历 claimable_plans，删 `*pool_active_plans()` |

### sentinel.py

| 行号 | 内容 | 处置 |
|---|---|---|
| 25 | `pool_active_plans,` import | 删除 |
| 28 | `sync_pool_claimable_plans,` import | 删除 |
| 220-225 | 哨兵补领 `active_pids = { pool_active_plans(now_ts) ... }` | 改为只依赖各账号自身 `claimable_plans` |
| 271 | `sync_pool_claimable_plans(new_plans, accounts=all_jwt)` | 删除该行 |

### frontend/admin/accounts.html

| 位置 | 内容 | 处置 |
|---|---|---|
| `getPoolKnownActivityPlans` 函数（831-859） | 前端池内推断 | 删除 |
| `hasUnclaimedActivity`（861-875） | 删掉 `getPoolKnownActivityPlans` 分支 | 只保留 claimable_plans 检查 |
| `getPoolKnownActivityPlans` 的其他调用点 | 见 `hasUnclaimedActivity`/`claimStripHtml`/领取流程 | 逐一删除引用 |

## 保留项（明确不删）

- `sync_account_claimable_plans`：单账号落库（preview 结果 → 本账号 claimable_plans），是正确行为，被 claim.py 8 处 + quota.py 3 处依赖。
- `filter_unclaimed_plans`：sync_account 的辅助函数。
- `preview_plans` 的「探查即落库」语义：`sync_account_claimable_plans(account, plans)` 保留，只删跨账号广播 `sync_pool_claimable_plans(plans)`。

## 风险与缓解

| 风险 | 缓解 |
|---|---|
| 删 `pool_active_plans` 后，哨兵补领失去「池内活动」来源 | 哨兵只依赖各账号自身 `claimable_plans`（preview 结果），这正是正确语义——每个账号的可领活动由自己 preview 决定 |
| 删 `sync_pool_claimable_plans` 后，已有被污染的 claimable_plans 残留 | 部署后需清理存量污染：把 11 个账号的 `claimable_plans=['zcode-v3-start-plan-referral']` 清空（它们是历史广播写入的脏数据） |
| 误删 `sync_account_claimable_plans`（单账号落库） | 明确保留，它是良性改进，非污染源 |

## 存量数据清理

部署后需清空 11 个被污染账号的 `claimable_plans`（`国际-5735/9149/xzaq/Abk/uexekabm/y4pj2kco/rlsw34gi/al5o25nz/dsqhl4nn/akbgpryk/suejue`），它们的 `claimable_plans` 被历史广播写入了 `zcode-v3-start-plan-referral`。清理方式：通过 API 触发这些账号的 `refresh`（重新 preview，claimable 会重新从空开始），或直接清空。
