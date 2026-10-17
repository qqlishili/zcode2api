# 执行记录

## 历史实施与验收（2026-10-14，原记录保留）

- 后端 `_build_plan_slots` 增加 `starts_at` 字段，新增 `_extract_start`（unix 秒，兼容 starts_at/startsAt/start_time/effective_at 及毫秒/字符串）。
- 前端 `planGroupHtml` 展示开始+结束日期，未开始加「待生效」徽章；`quotaCell` 对 slots 排序（未开始置后，已开始按 starts_at 升序）。
- CSS 新增 `plan-pending` / `pending-badge` / `pending-line`。
- 测试：`_extract_start` 6 项 + `_build_plan_slots` 的 starts_at 断言；全量单元回归通过。

## 历史 CI 与部署

- `ruff check app tests`、`mypy app/constants.py`、`pytest` 全量通过。
- 提交 `928572f`，push 到 origin；VPS `reset --hard` + `systemctl restart zcode2api`，服务 active。
- 部署后验证：`lidmuw` 的 `plan_slots` 现带 `starts_at`（Global Build 1791959600 / Start Plan 1791943431），expire 正常。

历史剩余限制：前端为纯展示逻辑，无独立单测框架；「未开始置后」的排序效果需在浏览器页面人工验收（本机无法直接打开生产页面）。此项为历史口径，以下按用户确认的无浏览器方式继续，不把历史记录当成本轮验收。

## 第一组：套餐期与排序

2026-10-17 已核对后端 starts_at 现有秒值和回退；页面开始与额度生效期此前混淆。新增 DOM 复现套餐期缺失、未知时间打乱已开始排序；首轮共 5 失败/14 通过，修复后 19 项通过。未来/未知/相同时间及现在边界覆盖，排序不修改原始数组。

本方案与时间归一化方案共享代码和诊断目录 `data/diagnostics/plan-time-20261017/`，分别记录任务进度，不重复发真实请求。最终相关后端/集成本机与 VPS 独立候选各 **109 项通过**、Ruff 通过；自审补齐旧状态栏/余额项兼容，全部前端最终 **23 项**分别在 UTC / America/Los_Angeles 通过，两方案 strict 和差分检查通过，第一组已验收。尚未提交上线，真实 API/上线页面与插件验收待执行；不将隔离夹具当生产未来套餐证据，视觉和真实结算恢复保留未验。
