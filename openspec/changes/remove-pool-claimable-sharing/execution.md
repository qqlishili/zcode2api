# 执行记录

> 2026-10-16 核对：当前源码已移除池内共享及前端推断，哨兵相关隔离测试通过；下文数据清理与部署为历史记录，本次未重新清库或现场复验。当前页面、数据与验证边界见 [项目交接](../../../docs/HANDOFF.md)。

## 2026-10-17 本号资格验收（发布前）

- 用户授权验收本号活动领取资格；本轮承接原 tasks / execution 的复核改动，其他 9 份既有文档保持。当前 **16/18**，发布与上线 JS 最终验收待完成；不归档、不重复清库。
- 源码确认：preview 只使用本号上游列表，单账号同步按 provider/id 找本号 live 对象；自动领取会重新 preview 本号，哨兵发现活动不广播资格。`app/`、`frontend/`、`tests/` 中两个共享函数和前端池内推断符号无残留。修正两处仍描述旧共享逻辑的注释。
- 页面发现真实缺陷：成功的空 preview 仍合并旧缓存；切换账号保留上一账号的选择/预填标识；上一账号晚返回的响应覆盖当前菜单。3 项 DOM 用例修复前全部失败，修复后转绿。成功结果替换本号缓存、切号清除旧目标、过期账号响应丢弃；显式手动输入及上游资格判断保留。
- 补齐此前缺失的 `claimable-activity` delta spec，改为新 capability（主规范原无此项），strict 通过；把“其他账号一律为空”精确为“逐号与本号 preview 对照”，有真实其他活动仍正常展示。沿用用户已接受的真实 API + 页面 JS DOM + 插件链路验收。
- 本机相关：claim **53**、sentinel **12**；前端全套 **26**，均通过。VPS 独立候选 `/root/claimable-verify-20261017-jy6qrnds`、全新数据目录，`ruff check app tests`、`mypy app/constants.py` 和全量 pytest **1280** 通过（273.44 秒）；测试不访问生产账号库。
- 20:13–20:14 CST，当前 17 号中 14 个允许 billing：逐号经当前固定同区代理直接 GET 上游 preview，全部 HTTP 200/code 0/空；真实管理 preview、内存账号 API、SQLite 只读查询一致为空。3 个停用号不请求上游，待领数据仍空。管理 preview 按现有行为上报 launch/daily 事件；本轮没有调用 claim、登录或安装入口。真实上线 JS 对 17 号领取条、操作提示和候选菜单的当前空结果对照通过；无生产正例，正例由隔离用例覆盖。
- 历史 11 号中当前能按名称对应 5 号：5735、9149、xzaq、Abk、suejue，待领均空。uexekabm、y4pj2kco、rlsw34gi、al5o25nz、dsqhl4nn、akbgpryk 不再以原名出现，不能据此认定其已删除或追溯逐号旧清理过程。当前全部 17 号无旧广播残留；本轮没有重清库。
- **历史引用限制**：本机与 VPS 都无法解析旧记录 `614b8d3`；当前 Git 历史从 `aa993eb` 导入开始，不能将旧执行记录当作本轮确认的发布证据。下文保留原历史陈述并标明此限制；当前验收以实际 HEAD、文件哈希、API及运行证据为准。
- 证据：本机 `data/diagnostics/claimable-acceptance-20261017/`（baseline、红绿 DOM、相关 JUnit、全量 CI 摘要、真实 API/JS 对照）；VPS `/root/claimable-live-20261017/` 与全量候选日志/JUnit。真实浏览器视觉、当前不存在的新活动实际领取/到账和历史旧提交可追溯性未验，不计入已完成的资格语义。

## 历史基线对照（git 实证）

- 原始正确版 `preview_plans`（`8dfc762~1`）只调 `/billing/preview` 直接返回，无池内合并/广播。
- `8dfc762`（v2.5.15，2026-09-29，作者 qqlishili）引入 `pool_active_plans`（从池内账号「已持有 plans」反推「可领活动」）。
- `82d21bc`（v2.6.4，2026-10-01，作者 qqlishili）引入 `sync_pool_claimable_plans`（跨账号广播）+ `sync_account_claimable_plans`（单账号落库）。

## 根因

`zcode-v3-start-plan-referral`（夜间蹬蹬卡）是邀请制自动发放活动：不在 preview 列表、由「邀请+新手任务」触发、额度在 `entitlements`。池内共享把「池内账号已持有的 plans」误当「人人可领的活动」，广播污染 11 个未持有账号的 `claimable_plans`，使其误显示「可领取」，哨兵还对无资格账号反复 claim。

## 分组实施

- 组 1（claim.py）：删 `sync_pool_claimable_plans`、`pool_active_plans` 两函数；`preview_plans` 删 `pool_active_plans` 合并与 `sync_pool_claimable_plans` 广播，保留 `sync_account_claimable_plans`（单账号落库）；`_auto_pick_plan` 删 `pool_active_plans` 兜底。验收：51 项 claim 单测通过。
- 组 2（sentinel.py）：删 2 import + 哨兵补领的 `active_pids`（改只依赖各账号 claimable_plans）+ `sync_pool_claimable_plans` 调用。验收：12 项哨兵单测通过（更新 2 处旧「池内共享」断言）。
- 组 3（前端）：删 `getPoolKnownActivityPlans` 函数 + `hasUnclaimedActivity` 池内推断分支 + 2 处调用点。验收：无残留 + 4 个 script 块 node 语法通过。
- 组 4：CI 三关通过；提交 `614b8d3`；VPS 部署；清空 11 个被污染账号 `claimable_plans`（服务内存验证残留 0）。

## CI 与部署

- `ruff check app tests`、`mypy app/constants.py`、`pytest`（全量单元回归）通过。
- 提交 `614b8d3` push 到 origin；VPS `reset --hard` + `systemctl restart zcode2api`，服务 active。
- 存量数据：11 个账号的脏 `claimable_plans=['zcode-v3-start-plan-referral']` 已清空。

## 剩余限制

- 前端「未持有账号不再显示可领取」与「已持有账号展示 +1亿待生效」的最终效果需浏览器页面人工验收（本机无法打开生产页面）。
- 附带提交了 entitlements 展示（`_build_plan_slots.pending` 字段 + 前端 `pendingRows`），与池内共享删除同属 referral 活动修复。
