# Proposal

## Why

活动套餐 `zcode-v3-start-plan-referral`（ZCode Global Build，夜间蹬蹬卡）是**邀请制活动**——只有被邀请的新用户完成新手三任务后才能领取。但代码把它当成「池内出现过的普通活动，人人可领」，导致未持有该套餐的账号都错误显示「可领取」，哨兵还会对无资格账号发起领取（返回 1004 不符合条件）。

根因：`pool_active_plans()` 与 `sync_pool_claimable_plans()` 这套「池内经验共享」机制，假设「池内某个账号探测到/持有的活动，其他账号也能领」。这个假设对「大促人人可领」成立，但对「邀请制/条件领取」不成立。

git 实证：原始 `preview_plans`（`8dfc762` 之前）只信上游 `/billing/preview`；`8dfc762`（v2.5.15）引入 `pool_active_plans` 池内共享；`82d21bc`（v2.6.4）再引入 `sync_pool_claimable_plans` 全池广播。本次修复是**回滚到原始正确语义**。

## What Changes

彻底删除池内共享，只信上游 preview 接口。**实现方式：以历史正确基线（`8dfc762~1`）为参照，在当前最新代码上做精准减法，非 git revert、非整体恢复历史文件。**

- `claim.py`：删除 `pool_active_plans()` 与 `sync_pool_claimable_plans()` 两个函数；`preview_plans` 删掉 `pool_active_plans` 合并与 `sync_pool_claimable_plans` 调用（**保留** `sync_account_claimable_plans` 单账号落库）；`_auto_pick_plan` 删掉 `pool_active_plans` 兜底。
- `sentinel.py`：哨兵补领与全池抢领逻辑删掉 `pool_active_plans` / `sync_pool_claimable_plans`，只依赖各账号自身 `claimable_plans`（preview 结果）。
- `frontend/admin/accounts.html`：删除 `getPoolKnownActivityPlans` 的池内推断；`hasUnclaimedActivity` 只信 `claimable_plans`。
- 存量数据清理：部署后清空 11 个被历史广播污染的账号 `claimable_plans`（脏数据 `zcode-v3-start-plan-referral`）。

## 保留项（明确不删）

- `sync_account_claimable_plans`：单账号落库（preview 结果 → 本账号 claimable_plans），正确行为，被 11 处调用。
- `filter_unclaimed_plans`：sync_account 的辅助函数。
- `preview_plans` 的「探查即落库」语义（只保留单账号落库，删跨账号广播）。

## Capabilities

### New Capabilities

- `claimable-activity`（可领取活动识别）：只信上游 `preview` 结果，不再跨账号共享「池内出现过的活动」。

## Impact

- 后端：`app/claim.py`、`app/sentinel.py`。
- 前端：`frontend/admin/accounts.html`。
- 测试：删除/更新覆盖池内共享的既有测试；新增断言 `preview` 空时不产生 claimable、邀请制活动不被广播。
- 无数据库迁移、无新服务、无第三方依赖。
- 影响：之前依赖「池内共享」的场景（一个账号探测到活动 → 其他账号立即显示可领）会失效；但这是正确行为——可领取资格只能由上游 preview 决定。

## Validation

- 单元测试：`preview` 返回空时，`claimable_plans` 为空，`hasUnclaimedActivity` 为 false。
- 真实验证：逐号比较本号 preview、管理 API、持久化数据和页面；其他账号持有 referral 不能产生本号资格。本号确有其他活动资格时正常显示，不要求所有其他账号一律为空。
- 采用已授权的无浏览器验收：真实管理 API + 上线页面 JS DOM + VPS 插件完整链路；真实浏览器视觉与兼容性明确未验，不主动领取活动。
