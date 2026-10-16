# Proposal

## Why

活动套餐 `zcode-v3-start-plan-referral`（ZCode Global Build，夜间蹬蹬卡）在页面上出现四类展示 bug：

1. 只展示名字，不展示授予的 token 数量（`entitlements.grant_units=1 亿`，但 `balances` 空）。
2. 没有「待生效」样式——上一轮用 `plans[].starts_at` 判断待生效，但正确判据是 `entitlements[].effective_at`（权益生效时间，实测 `1791990000` 在未来）。
3. 未持有该套餐的其他账号都错误显示「可领取」——`getPoolKnownActivityPlans` 收集「池内出现过的活动」，`hasUnclaimedActivity` 只要「本号没持有池内活动」就返回 true，**完全没校验领取资格**。
4. `国际-ok li` 显示三句矛盾文案（「有效期」+「额度已授予等待结算」+「额度未获取」）。

根因：前端只从 `balances` 渲染 token 与「待生效」，忽略了 `entitlements`（授予额度 + 生效时间）；「可领取」判定不信上游 `preview` 资格真相源，而信池内推断。

## What Changes

- 前端 `planGroupHtml` / `fallbackQuotaHtml` 渲染 `entitlements` 的授予额度（`+grant_units` token），用 `effective_at` 判断「待生效」（`effective_at > now`）。
- 前端 `hasUnclaimedActivity` 改为只信 `preview` 接口结果（`claimable_plans`），删除 `getPoolKnownActivityPlans` 的池内推断路径。
- 前端「待结算」文案对齐：`entitlements` 有授予额度但 `balances` 空时，不显示「额度未获取」，显示「+grant_units 待生效」或「已授予待结算」。

## Capabilities

### Modified Capabilities

- `plan-slot-display`：套餐槽位渲染 `entitlements` 授予额度，用 `effective_at` 判断待生效，并修正「可领取」与「待结算」判定。

## Impact

- 主要代码：`frontend/admin/accounts.html`（`planGroupHtml`、`fallbackQuotaHtml`、`hasUnclaimedActivity`、`getPoolKnownActivityPlans`、`getHeldPlanIds` 及调用方）。
- 无后端改动（前端已能经 `a.plans[].entitlements` 拿到授予额度与生效时间）。
- 无数据库迁移、无新服务、无第三方依赖。
- 前端为纯展示逻辑，无独立单测框架，靠人工页面验收。

## Validation

- 真实验证：`lidmuw` / `国际-ok li` 页面刷新后，`ZCode Global Build` 显示 `+1 亿 token（待生效，X 起）`；未持有该套餐的账号不再显示「可领取」；`国际-ok li` 不再出现「额度未获取」矛盾文案。
