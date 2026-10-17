# 执行记录

## 用户修订：活动有效期（2026-10-17）

用户明确合并卡片级套餐期/额度生效期为「活动有效期」，两端取同一活动本身的 starts_at / ends_at。新增后端 2 项及页面 2 项先失败，修复后 VPS 独立候选相关 111 项通过、Ruff 通过，前端全部 26 项通过，两方案 strict 通过。首次相关回归命令用了不存在的历史测试路径，没有运行测试，保留日志；校准真实路径后运行上述 111 项，不将该次命令当作业务失败。

槽位保留活动自己的 ends_at，与可能由余额回填的 expire 分开；开始不借用 effective_at。前端只显示活动有效期，未知端点为 —，孤儿桶不虚构活动；模型到期和待生效权益明细保留。可用状态代码未改，六列的接口、字段与显示规则已整理至 [字段清单](../../../docs/account_table_fields.md)。本组实现完成，发布及真实链路验收继续记录，不以隔离通过提前关闭。

业务提交 `811a96383d41ec72ac15bebb9e5be18425ff7cba` 已推送/VPS 快进。在途 0 后只重启网关 3.184 秒，PID1815403、NRestarts0；Mihomo/WARP 进程不变。指定两号只刷新余额，2 成功/0 失败，4 个活动起止与同轮上游 balance 同活动原始字段匹配；全站 7 个非孤儿槽位均已有 ends_at。上线 JS 在 UTC/洛杉矶均通过，状态栏函数逐字一致，同一真实账号数据发布前后渲染一致，模型到期保留。

Windows→公网→VPS plugin 两模型各普通/SSE共 4 项通过：HTTP200、模型一致、end_turn、错误0、全部逐字 OK。配置哈希、active_policy、Mihomo/WARP PID 未变，plugin host_alive=true/stale=false；17 发布文件哈希核对，9 份原有文档保护未变。回退 `/root/activity-period-recovery-20261017-dzblf7c5`，诊断目录本机 `data/diagnostics/activity-period-20261017/`、VPS `/root/activity-period-live-20261017/`。本次候选源码目录已删除，日志/XML保留；6/6 完成不归档，真实浏览器视觉/兼容性仍未验。

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


## 第五组：活动周期与居中布局（2026-10-18）

按用户确认的period规则展示活动开始、结束和右下角，原业务验证保留：隔离后端85项、前端全套32项两时区、Ruff和strict通过；指定两号纯余额刷新2成功/0失败，4个活动上游/API/上线JS核对通过；公网两模型普通/SSE共4项均HTTP200、模型一致、end_turn、错误0、逐字OK。原有数字、pending、状态与排序保持不变。

用户否决全宽布局，要求Git回退并整理提交。最终账号页保持原1280px居中宽度、左右28px内边距，额度列由320增至400px，列间距由14改为8px；六列加内边距/边框总宽1220px，不超出原1224px内容区。名称与有效期仍同行、不换行；监控页通用表格仍1080px。先复现none不等于1280px，修改后全前端32项UTC、额度相关31项洛杉矶通过，并核对原宽度内的六列预算与1280/1366/1536/1920窗口留白。

VPS实际页面/样式v2.6.11与本机一致，真实页面JS在UTC/洛杉矶使用原上游核对过的三类活动快照通过；当前管理API的状态栏保持原逻辑，六列与5个操作按钮完整，居中/留白/1220px宽度预算通过。无需重启，网关/Mihomo/WARP进程、配置哈希、出口策略不变，plugin健康。

按授权撤去最近三次提交，以e51b0b2为父整理为一个提交，精确force-with-lease同步main，VPS干净工作树reset --keep同步；收尾文档并入同一提交。诊断、提交一致性及发布文件哈希保留于data/diagnostics/compact-layout-20261018/及原period-display-20261017/；离线恢复备份为本机pre-rollback.bundle和VPS /root/compact-layout-20261018/pre-rollback.tar.gz。不改账号库、出口或9份原有文档，不再刷新额度或重跑未改变的对话链路。真实浏览器视觉/兼容性与Weekend历史消费资格仍未验，不归档。
