# 执行记录

## 实施与验收

- 后端 `_build_plan_slots` 增加 `starts_at` 字段，新增 `_extract_start`（unix 秒，兼容 starts_at/startsAt/start_time/effective_at 及毫秒/字符串）。
- 前端 `planGroupHtml` 展示开始+结束日期，未开始加「待生效」徽章；`quotaCell` 对 slots 排序（未开始置后，已开始按 starts_at 升序）。
- CSS 新增 `plan-pending` / `pending-badge` / `pending-line`。
- 测试：`_extract_start` 6 项 + `_build_plan_slots` 的 starts_at 断言；全量单元回归通过。

## CI 与部署

- `ruff check app tests`、`mypy app/constants.py`、`pytest` 全量通过。
- 提交 `928572f`，push 到 origin；VPS `reset --hard` + `systemctl restart zcode2api`，服务 active。
- 部署后验证：`lidmuw` 的 `plan_slots` 现带 `starts_at`（Global Build 1791959600 / Start Plan 1791943431），expire 正常。

## 剩余限制

- 前端为纯展示逻辑，无独立单测框架；「未开始置后」的排序效果需在浏览器页面人工验收（本机无法直接打开生产页面）。
