# Proposal

> 2026-10-17 继续实施：按用户明确的多活动场景验收，不因 Global Build 待生效而停用仍有其他余额的账号；真实 API + 页面 JS 的 DOM 验收沿用已确认方式，视觉与真实浏览器兼容性保留未验证。

> 2026-10-16 复核：**部分实现，未验收。**授予量渲染和本号 preview 资格判断已有代码，但隔离调用当前 `planGroupHtml` 可复现：`effective_at` 已过去、balances 仍未结算的额度依旧显示“未生效”。这不满足下面按 `effective_at > now` 判定的要求，原 205 项离线测试没有覆盖此场景；本次未修改业务代码或完成浏览器验收。见 [项目交接](../../../docs/HANDOFF.md)。

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

### New Capabilities

- `plan-slot-display`：套餐槽位渲染 `entitlements` 授予额度，用 `effective_at` 判断待生效，并修正「可领取」与「待结算」判定。

## Impact

- 主要代码：`frontend/admin/accounts.html`（`planGroupHtml`、`fallbackQuotaHtml`、`hasUnclaimedActivity`、`getPoolKnownActivityPlans`、`getHeldPlanIds` 及调用方）。
- 无后端改动（前端已能经 `a.plans[].entitlements` 拿到授予额度与生效时间）。
- 无数据库迁移、无新服务、无第三方依赖。
- 复用已有 happy-dom 与 Node test runner 验证真实页面 JS，不新增依赖；结合上线页面和真实管理 API 数据验收。

## Validation

- 真实验证：用户指定的两个账号，其 Global Build 在生效时间前显示授予量与“未生效”，同时保留其他活动的真实余额及账号正常状态。生效后尚无余额时应显示“待结算”，不能显示“未生效”或“额度未获取”。
- 本号无 preview 资格不显示“可领取”，已持有套餐过滤；真实新活动发额度不由此展示验收核销。
