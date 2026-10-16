# Proposal

## Why

页面上每个套餐（plan）只展示结束日期（`至 YYYY-MM-DD`），不展示开始日期，也未按「是否已开始」排序。用户希望：每个 plan 展示开始 + 结束日期；未开始的 plan 放到下面。

上游 `billing/balance` 的 `plans[]` 已含 `starts_at` 与 `ends_at`（unix 秒），且 `fetch_quota` 已全量保留到 `account.plans`。后端 `_build_plan_slots` 目前只提取结束时间（`expire`），前端 `planGroupHtml` 只渲染 `至 {结束}`。

## What Changes

- 后端 `_build_plan_slots` 增加 `starts_at` 字段（从 `pl[].starts_at` 提取，unix 秒，供前端判断「未开始」与展示开始日期）。
- 前端 `planGroupHtml`：plan 卡片头同时展示开始日期与结束日期（已开始的显示 `MM-DD ~ MM-DD`；未开始的显示 `MM-DD 起 · 至 MM-DD` 或等效）。
- 前端 `quotaCell`：对 `plan_slots` 排序，未开始（`starts_at > now`）的 slot 放到后面；已开始的按 `starts_at` 升序。

## Capabilities

### Modified Capabilities

- `plan-slot-display`：套餐槽位展示开始/结束日期，并按开始时间排序（未开始置后）。

## Impact

- 后端：`app/quota.py`（`_build_plan_slots` 增加 `starts_at` 字段）。
- 前端：`frontend/admin/accounts.html`（`planGroupHtml` 展示 + `quotaCell` 排序）。
- 测试：新增单测覆盖 `_build_plan_slots` 输出含 `starts_at`；前端为纯展示逻辑，无独立单测框架，靠人工验收。
- 无数据库迁移、无新服务、无第三方依赖。

## Validation

- 单元测试：`_build_plan_slots` 在 `plans[].starts_at` 存在时输出对应 `starts_at`。
- 真实验证：页面刷新后，`国际-ok li` 与 `lidmuw` 的套餐卡片显示开始/结束日期，未开始套餐置后。
