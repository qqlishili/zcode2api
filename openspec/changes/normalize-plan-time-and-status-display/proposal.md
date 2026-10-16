# Proposal

## Why

套餐卡片的时间与状态展示存在 4 类不一致（2026-10-14 实测）：

1. **时间格式不一致**：`starts_at` 走前端 `fmtDate()`（`10/14 14:33`，无年份），`ends_at` 走后端 `_extract_expire`（`2026-10-15 23:59`，有年份），拼接后成 `10/14 14:33 ~ 2026-10-15 23:59`。
2. **语义不准确**：`ZCode Global Build`（`daily_night` 夜间活动）的 `starts_at=10-14 14:33`，但额度 `entitlements[].effective_at=10-14 23:00`——显示 `14:33` 会让用户以为额度那时就能用，实际要等 23:00。
3. **`国际-ok li` 四句矛盾文案**：`用完`（status）+ `有效期 2026-10-15 23:59:59`（planEnd）+ `额度已授予，等待结算`（last_error）+ `额度未获取，点上方刷新额度`（fallbackQuotaHtml 兜底）——四处独立渲染，未归一化。
4. **`待生效` 样式**不统一（需优化视觉，P2）。

## What Changes

**归一化到数据层单一事实源**，前端只读一处：

- 后端 `_build_plan_slots` 新增 `quota_start` 字段：额度生效时间 = `entitlements[].effective_at`（取最早有效值）否则回退 `starts_at`。作为「额度有效期」的唯一起点。
- 后端新增统一的短时间格式化 `_fmt_short(ts) -> "MM-DD HH:MM"`，供 `starts_at`/`quota_start`/`ends_at` 输出统一格式（禁用有年份的长格式混用）。
- 前端 `planGroupHtml`：时间标签统一显示 **`quota_start ~ ends_at`**（额度生效期，方案 B），格式统一 `MM-DD HH:MM`。
- 前端 `fallbackQuotaHtml` / `statusCell`：统一状态文案来源——`status=exhausted` 且有 pending 额度时显示「等待结算」，不再同时渲染「用完」+「额度未获取」。

## Capabilities

### Modified Capabilities

- `plan-slot-display`：时间格式统一为 `MM-DD HH:MM`；套餐卡片显示额度生效期（含 `daily_night` 活动的 `effective_at`）；状态文案归一化。

## Impact

- 后端：`app/quota.py`（`_build_plan_slots` 增 `quota_start`、新增 `_fmt_short`、`_extract_expire` 短格式统一）。
- 前端：`frontend/admin/accounts.html`（`planGroupHtml` 时间标签、`statusCell` 状态文案、`fallbackQuotaHtml` 兜底）。
- 测试：新增 `_build_plan_slots.quota_start` 断言（有 effective_at → 用 effective_at；无 → 用 starts_at）。
- 无数据库迁移、无新服务、无第三方依赖。

## Validation

- 单元测试：`quota_start` 取值（effective_at 优先、starts_at 回退）。
- 真实验证：`国际-ok li` 套餐卡片显示 `10-14 23:00 ~ 10-15 23:59`（额度生效期），状态为「等待结算」，不再出现「用完」+「额度未获取」矛盾。
