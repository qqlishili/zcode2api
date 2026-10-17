# 执行记录

> 2026-10-16 核对：当前源码已移除池内共享及前端推断，哨兵相关隔离测试通过；下文数据清理与部署为历史记录，本次未重新清库或现场复验。当前页面、数据与验证边界见 [项目交接](../../../docs/HANDOFF.md)。

## 2026-10-17 本号资格验收（已发布）

- 用户授权验收本号活动领取资格；本轮承接原 tasks / execution 的复核改动，其他 9 份既有文档保持。当前 **18/18**；不归档、不重复清库。
- 源码确认：preview 只使用本号上游列表，单账号同步按 provider/id 找本号 live 对象；自动领取会重新 preview 本号，哨兵发现活动不广播资格。`app/`、`frontend/`、`tests/` 中两个共享函数和前端池内推断符号无残留。修正两处仍描述旧共享逻辑的注释。
- 页面发现真实缺陷：成功的空 preview 仍合并旧缓存；切换账号保留上一账号的选择/预填标识；上一账号晚返回的响应覆盖当前菜单。3 项 DOM 用例修复前全部失败，修复后转绿。成功结果替换本号缓存、切号清除旧目标、过期账号响应丢弃；显式手动输入及上游资格判断保留。
- 补齐此前缺失的 `claimable-activity` delta spec，改为新 capability（主规范原无此项），strict 通过；把“其他账号一律为空”精确为“逐号与本号 preview 对照”，有真实其他活动仍正常展示。沿用用户已接受的真实 API + 页面 JS DOM + 插件链路验收。
- 本机相关：claim **53**、sentinel **12**；前端全套 **26**，均通过。VPS 独立候选 `/root/claimable-verify-20261017-jy6qrnds`、全新数据目录，`ruff check app tests`、`mypy app/constants.py` 和全量 pytest **1280** 通过（273.44 秒）；测试不访问生产账号库。
- 20:13–20:14 CST，当前 17 号中 14 个允许 billing：逐号经当前固定同区代理直接 GET 上游 preview，全部 HTTP 200/code 0/空；真实管理 preview、内存账号 API、SQLite 只读查询一致为空。3 个停用号不请求上游，待领数据仍空。管理 preview 按现有行为上报 launch/daily 事件；本轮没有调用 claim、登录或安装入口。真实上线 JS 对 17 号领取条、操作提示和候选菜单的当前空结果对照通过；无生产正例，正例由隔离用例覆盖。
- 历史 11 号中当前能按名称对应 5 号：5735、9149、xzaq、Abk、suejue，待领均空。uexekabm、y4pj2kco、rlsw34gi、al5o25nz、dsqhl4nn、akbgpryk 不再以原名出现，不能据此认定其已删除或追溯逐号旧清理过程。当前全部 17 号无旧广播残留；本轮没有重清库。
- **历史引用限制**：本机与 VPS 都无法解析旧记录 `614b8d3`；当前 Git 历史从 `aa993eb` 导入开始，不能将旧执行记录当作本轮确认的发布证据。下文保留原历史陈述并标明此限制；当前验收以实际 HEAD、文件哈希、API及运行证据为准。
- 证据：本机 `data/diagnostics/claimable-acceptance-20261017/`（baseline、红绿 DOM、相关 JUnit、全量 CI 摘要、真实 API/JS 对照）；VPS `/root/claimable-live-20261017/` 与全量候选日志/JUnit。真实浏览器视觉、当前不存在的新活动实际领取/到账和历史旧提交可追溯性未验，不计入已完成的资格语义。

### 发布与最终核验

- 修复提交 `c06f5af3d0b620e795f5c24655d98f59ea198760`，main 已推送，VPS 在干净工作树上 fetch + merge --ff-only；10 个发布文件 LF SHA-256 与本机一致。回退代码 `/root/claimable-recovery-20261017-wuaf793b`。本次 Python 仅改注释，运行逻辑仅前端，未重启服务。
- 20:26–20:27 CST 上线管理 preview、账号 API、只读 SQLite 再次对照：14 个允许查询账号一致为空，3 个停用号跳过。实际上线页面仅有既有版本模板替换，与本机被测试 JS 一致；17 号领取条、操作提示、弹窗候选通过，无真实正例，不制造活动。
- Windows → 公网 `/v1/messages` → VPS plugin：Flash / GLM-5.3 各普通/SSE，共 **4 项通过**，HTTP 200、响应模型大小写一致、end_turn、流完整、错误 0。Flash 两项逐字 `OK`，GLM-5.3 两项 `OK.`，不记为四项逐字 `OK` 通过。
- 发布前后 `.env`、出口注册表、生命周期与 Mihomo 配置哈希未变，出口策略完整对象一致（chat direct / claim proxy，HK21100 / JP21303 / TW21202，active=saved、pending=false）；网关 PID1713940、Mihomo2797546、WARP755387 均未变，NRestarts0；plugin host_alive=true/stale=false。
- 本机原 Git 路径在验收途中失去访问，Git MCP 同样失败；从 Git for Windows 官方发布临时解压 MinGit 2.56.0.2，按官方 sha256 `da35e72aa21c005a5a0d298cfbae110bc1609a815730ea0dde84b01a1b3cd3be` 核验后完成提交，不修改系统权限、安装或 Git 配置。标准 venv SSL 问题仍开放，公网脚本使用现有 miniforge Python，不记为标准环境修复。
- VPS 候选源码、隔离数据、压缩包和 scratch 已清理，仅保留全量日志/JUnit与回退。Windows 本轮 `pytest-*` 夹具清理被文件系统拒绝访问，保留现场，不改 ACL、不清理其他目录；不宣称全部临时文件已清空。

### 验收结论

| 维度 | 结果 |
|---|---|
| 完整性 | 18/18，4 条新增 requirement 均有实现/用例与证据映射；未归档 |
| 正确性 | 本号 preview / 自动逐号查询 / 页面切号隔离 / 显式手填兼容均通过相关测试；当前生产空资格通过实际对照 |
| 一致性 | 保留原单号同步、手动上游判定、错误处理和发布流程；仅修复本轮定位的页面残留与过时注释 |
| 限制 | 旧发布提交及 6 个历史名称无法现场追溯；生产无可领取正例，实际新领取/到账、真实浏览器视觉与兼容性未验 |

需求映射：本号资格见 claim preview / 单号落库及集成测试 `test_claim_pool_plans_do_not_create_other_account_eligibility`、哨兵单号同步测试；逐号自动领取见 `auto_claim_all_plans`、哨兵新活动/补领测试；页面边界见 `quota_entitlements.test.mjs` 的空结果/切号/晚响应三例及真实 `after-dom.json`；显式手填见既有 `test_claim_already_claimed_no_retry` 等上游错误码集成测试。当前核验范围无阻断项，历史证据和现场正例限制不补造为通过。

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
