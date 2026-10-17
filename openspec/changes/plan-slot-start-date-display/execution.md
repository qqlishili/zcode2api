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

本方案与时间归一化方案共享代码和诊断目录 `data/diagnostics/plan-time-20261017/`，分别记录任务进度，不重复发真实请求。最终相关后端/集成本机与 VPS 独立候选各 **109 项通过**、Ruff 通过；自审补齐旧状态栏/余额项兼容，全部前端最终 **23 项**分别在 UTC / America/Los_Angeles 通过，两方案 strict 和差分检查通过。第一组当时尚未提交上线，真实验收见下一节；不将隔离夹具当生产未来套餐证据，视觉和真实结算恢复保留未验。

## 第二、三组：真实上线与插件

- 提交 `8b1750269a4b721967fe69d3aa4713fb54a34ff0` 已推送/VPS 快进；16 文件哈希一致，重启网关 0.372 秒。详细配置保护与定时更新器独立差异见[时间方案执行记录](../normalize-plan-time-and-status-display/execution.md)。
- 真实 API 刷新两号 2 成功/0 失败，上线 JS load/rowHtml 与原始数据独立期望一致：`国际-ok li` 的 Weekend（10-12 20:00）在 Global（10-14 14:33）之前；`fozweg@hhhcc.online` 的 Start（10-17 00:43）在 Global（10-17 00:51）之前。套餐开始和 23:00 额度生效期分开显示，同区间不重复。
- 当前两号均无未来 starts_at 或未知开始套餐，未来后置和未知居中的真实生产样本未出现，由固定时间 DOM 覆盖，不制造生产套餐或声称实测过生产未来样本。
- 公网插件两模型普通/SSE共 4 项通过，模型大小写一致、end_turn、错误 0；3 项 OK、1 项 OK.。真实页面/API与两时区 DOM、服务/出口及差分保护通过，4/4；验收记录随 Git 同步，不重启、不归档。临时夹具/候选代码已清理，报告/回退保留。视觉/兼容性和真实结算恢复保持独立未验。
