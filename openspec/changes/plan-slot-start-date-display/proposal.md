# Proposal

## Why

页面上每个套餐（plan）只展示结束日期（`至 YYYY-MM-DD`），不展示开始日期，也未按「是否已开始」排序。用户希望：每个 plan 展示开始 + 结束日期；未开始的 plan 放到下面。

上游 `billing/balance` 的 `plans[]` 已含 `starts_at` 与 `ends_at`（unix 秒），且 `fetch_quota` 已全量保留到 `account.plans`。2026-10-17 核对：后端已输出 `starts_at`，前端已有基础排序，但页面只显示额度生效期；未知开始时间穿插时，已开始套餐的比较规则不完整。

## What Changes

- 核对并保留后端 `starts_at` 秒值，缺失时不虚构开始时间。
- 前端卡片显示「套餐期」开始至结束；与额度生效时间不同才另列「额度生效期」，避免重复和概念混淆。时间格式沿用 `normalize-plan-time-and-status-display` 的北京时间 `MM-DD HH:MM`。
- 未开始（`starts_at > now`）放后；已开始按 `starts_at` 升序，未知开始时间稳定放在已知已开始之后、未来之前；同时间及未来套餐保持原顺序，不改 API 数组。

## Capabilities

### New Capabilities

- `plan-slot-start-display`：补充套餐开始/结束时间和确定性排序；主规范尚无套餐展示能力，以独立开始/排序契约避免与其他未归档方案重复新增同一能力。

## Impact

- 后端：`app/quota.py`（`_build_plan_slots` 增加 `starts_at` 字段）。
- 前端：`frontend/admin/accounts.html`（`planGroupHtml` 展示 + `quotaCell` 排序）。
- 测试：后端已有 `starts_at` 断言；扩展现有 Node test runner + happy-dom 的真实页面 JS DOM 验收，无新增依赖。
- 无数据库迁移、无新服务、无第三方依赖。

## Validation

- 第一组：后端开始秒值、真实页面 JS 的开始/结束/额度时间区别、已开始/未知/未来混排、相等边界和不修改输入数组。
- 第二组：刷新现有指定账号 `国际-ok li` 与 `fozweg@hhhcc.online` 的真实管理 API，加载上线页面 JS 验证；不借用历史 `lidmuw` 快照或制造生产套餐。
- 第三组：与时间归一化方案共享公网插件普通/SSE和部署保护验收。按用户确认采用无浏览器方式，视觉布局和真实浏览器兼容性仍未验。
