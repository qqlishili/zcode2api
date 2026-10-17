# 分流实现与验收记录

## 第 1 组：配置契约与失败复现（2026-10-17）

- 在本机独立数据目录执行，未调用生产上游、修改 VPS 配置或重启服务。原 15 份文档改动继续保留。
- `tests/unit/test_purpose_egress.py` 三项回归均先失败：传入代理被全局 direct 改成 None、operation 不支持 purpose、插件未确认仍执行 host.fetch。失败证据为 `data/diagnostics/split-egress-implementation-20261017/red.xml`；分别由第 2/3 组转绿。
- 首次 pytest 在默认临时目录遇 WinError 5，未进入断言，不算复现。按 mattpocock diagnosing-bugs 定位后改用本项目独立 basetemp；标准 venv SSL 故障沿用基础 Python 先加载 SSL 的已知验证入口，没有修复无关环境。
- 新增不可变用途策略及四项环境默认、完整对象校验；Store 在加载数据库之后冻结策略，CLI/网关共用。保存/恢复只改目标，原子提交成功才更新缓存；运行值不变，来源也参与 pending_restart 比较。
- 配置定向验证 **17 项通过**，覆盖默认/部分绑定、非法枚举/URL/身份/端口/未知字段、数据库优先、重启加载、恢复默认、写失败无缓存/数据库半保存、损坏对象拒绝启动。证据 `group1.xml`；差分空白检查通过。未运行全量测试，不把此结果当作路由或公网验收。
- 配置及生效说明见 `docs/egress_configuration.md`、`.env.example`。下一组接入连接池、固定绑定和退役引用。

## 第 2 组：连接池、固定绑定与退役

- 客户端缓存包含账号/用途/实际出口/代次；有效 None 不再表示未解析。同账号异步跨用途取得独立操作，producer 仅派生父操作的路由，账号关闭覆盖所有用途。
- 固定领取绑定校验同区、身份、来源和物理退役；chat draining 不改变，业务断路器按用途/物理身份隔离。生效绑定参与 fixed_ports 和 fixed ack，保存移除不会提前释放；退役文件变化即关闭新准入，不依赖注册表内容变化。
- 回归曾发现提前重新解析路由破坏已有缓存粘性，已修复：继承策略下仍有效的客户端不会因同区增加其它节点而重新哈希。断路器夹具同步新键结构，保持原行为断言。
- VPS 独立目录 `/root/split-egress-verify-20261017-a_kbzjat/repo` 使用隔离 data 与真实 Linux /proc；配置、连接池、操作/旧流/producer、断路器与消费者集成 **70 项通过，1 项插件负向用例留第 3 组**。证据为同目录上层 `group2.xml` / `group2.log`。
- Linux 多进程验证新增 claim-only draining 绑定：操作排空后固定引用仍阻止回收，重启移除绑定才允许回收。单进程另覆盖待重启删除保存的 fixed/operations/producers/caches 计数和新准入关闭。
- 本组未部署，生产 HEAD/三个服务 PID 与实施前一致；没有改生产账号、节点、配置或监听。

## 第 3 组：业务入口、验证码、配置 API / 页面

- 网关/三协议/native/plugin/调度/探针显式 chat；领取、JWT 额度、账号安装/登录、哨兵显式 claim。自动领取整次尝试持有同一操作，异步额度刷新不能串用 chat。管理删除账号已接入所有用途关闭并保留在途引用。
- 插件删除静默直连；继承代理未确认时明确不可用，显式本地代理按实际客户端透传，宿主 vendor 未改。验证码 producer 继承父用途，claim 不触发消费回补；有效 direct 不重新解析账号代理，定向清池不株连其它出口。
- 配置管理扩展原 settings 页及导航、独立表单和保存/恢复；服务端鉴权、整对象校验、过期选择 409、原子保存和 active/saved 展示已接通。显式 claim 的人工选套餐改服务端求解，旧浏览器 token 返回 409 稳定标识，inherit 保留旧入口。
- VPS 独立夹具覆盖相关单元及真实 TCP Mock 集成，**520 项通过**（与第 2 组有重叠，不相加）；证据 `group3.xml` / `group3.log`。三项原始分流失败已转绿。完整受影响及 Linux 回归留第 4 组集中验收。
- 首轮 14 失败中，6 项在未修改 HEAD 的独立 baseline 同样失败（baseline.xml / baseline.log）：旧激活数量/鉴权断言、重复 JWT 身份被误当新增、已移除的跨账号资格共享。仅修正测试夹具/断言到已有契约，未恢复共享或改业务门禁。其它失败为旧接口夹具及候选包遗漏 solver.js，已修复后复验。
- 配置页真实 JS 的 happy-dom 行为验证通过：加载、独立保存、当前/目标、待重启、重载、保存失败保留、旧节点不自动改绑、恢复默认、旧表单保留。运行 `node tests/frontend/egress_settings.test.mjs`；复用现有依赖，无新增包。HTML 内嵌脚本语法及 OpenSpec strict 通过。
- **真实浏览器尚未验证**：当前 CUA 返回 apps/browsers 空，创建 iab 返回 Browser is not available。已向用户说明 API/DOM 与真实插件验收的覆盖边界；未将 DOM 冒充真实浏览器，不核销 4.6。代码和接口验证继续推进。
- 原 15 份文档 SHA256 全部保持；本组尚未发布。

## 第 4 组：集中检查与发布验收（进行中）

- 扩展至 52 份相关测试文件（全部 integration 与相关用途/设备/安装/额度单元），初轮 799 通过、10 失败；其中 9 项在未修改 HEAD 的 baseline4 也复现，剩余一项是本次 purpose / 未解析代理参数的旧 Mock 断言。只修正夹具/断言：真实不同 JWT 身份、显式对应额度模型、给探针替身补凭证、声明用途；没有改生产业务来迎合测试。
- 修复后定向复验 85 项通过，剩余一个假 JWT 的 Mock 前缀碰撞经真实失败输出定位，修正测试 token 前缀后相关 15 项通过。没有每个小修改都重跑整批。`group4-latest.xml` 合并每个 testcase 的最新结果，**809 个用例无失败**；不是各批次数量相加。
- 发布前自审增加嵌套 billing 超时回归：在外层 claim 操作内实际 request 的 read timeout 变成 None，先复现失败（本机 nested-timeout-red.xml）。修正为逐请求默认 25 秒，避免长流客户端吞掉 preview 限时；相关用途/节流/领取 **59 项通过**，Ruff app / tests 全部通过。
- 纳入新增超时场景后，**810 个 testcase 的最新结果均通过**，合并证据为 `data/diagnostics/split-egress-implementation-20261017/group4-latest.xml`；各原始 JUnit/日志保留在 VPS 独立验证目录。DOM 行为、两页面 JS 语法及 change strict 已通过；真实浏览器仍未覆盖。
- 发布前从 Windows 显式无代理访问公网网关：普通对话 HTTP 200 / OK / stop；SSE HTTP 200 / OK / stop / [DONE]，流内错误 0。证据 public-before-deploy.json。临时认证文件已两端删除，不打印凭证。
- 回退材料已保全于 `/root/split-egress-recovery-20261017-wsos0gff`（目录 0700）：代码归档/HEAD、原出口 meta 是否存在及值、.env、注册表/生命周期和 Mihomo 配置哈希。原数据库无出口覆盖；回退只恢复该 meta，不回滚整个账号库。
- 04:08 左右生产只读核对仍为 HEAD 027aafe、网关 PID 1132673、plugin + direct。enabled JWT 大区 HK 6 / JP 5 / TW 3；来源有效且未 retiring/reclaimed 的候选分别 5 / 24 / 7，尚未作为领取成功证明。未部署、未保存生产分流配置。
- **04:22:06–04:22:08 CST 已经 Git 快进部署 `78bf7f1267082bf1f580ebcdb1e7b8bfe056be17`**，仅重启网关，PID 1263681、active/running、NRestarts=0；Mihomo 2797546 和 WARP 755387 未变。`.env`、注册表、生命周期及 Mihomo 配置 SHA256 与发布前一致。两个用途仍为 inherit / environment，数据库无出口覆盖、pending_restart=false；真实通道 plugin + direct，host_alive=true、stale=false；配置/候选 API 及 `/meta` 均 200。
- 发布后 Windows 显式无代理请求公网网关：普通 HTTP 200 / OK / stop，4.562 秒；SSE HTTP 200 / OK / stop / [DONE]、流内错误 0，4.282 秒。证据 `public-after-inherit.json`。回退目录 `deploy-inherit.json` 和 `inherit-evidence.json` 保留部署及 SQLite 运行证据，临时认证文件两端已删除。
- 从 ready 到检查 **235.9 秒**，超过 75 秒 TTL：`solve_events` refill 4 / foreground 1 / hit 2，全部 port=null（direct）、reason 为空；业务记录普通成功 1 / SSE 成功 2，均 200。journal 未匹配到消息不代表没有求解，本结论使用只读 evidence.db；正常消费/过期补货保留，短窗不代表容量验收。定时更新器最近仍为 01:45:22–01:51:51 正常退出，下一次 07:45，未因本次发布触发。
- 4.2 据以上证据完成，当前 **16/23**；4.3–4.6 和第 5 组继续待办。生产尚未保存显式分流策略，HK/JP/TW 领取候选需重新实测；真实浏览器缺口不核销。
- **15:41 CST 继续验收**：用户明确选择“采用无浏览器验收”，据此更新 4.6 和设计验收说明；视觉布局、真实浏览器兼容性继续未验证，不能冒充浏览器验收。定时更新器已于 13:45:30–13:52:11 再次正常运行，旧 01:55 注册表快照不作为当前状态。
- 独立诊断目录 `/root/split-egress-live-20261017-hd4wu2je` 只读生产账号、使用隔离 Store/evidence，未切生产策略。HK 21100 / TW 21202 的当前固定身份及来源通过准入；真实公共 trace 分别 HK / TW、warp=off，configs/激活/preview/claim 均 HTTP 200，preview code 0 且无新活动，验证码各 280 字符，已持有套餐 POST 返回 1003。只证明通路，未验证新活动发额度。JP 21300 首轮连接失败；公共 trace + ZCode configs 对照中 21300/21302 均 ConnectError，21301 为 JP 出口且 configs code 0，接续验证 21301。
- 首轮诊断脚本把“已持有非基础套餐”误作全部地区预检前提，HK 后在 JP 选账号前 StopIteration；JP 仍持有基础体验套餐，脚本已按真实持有套餐选择，不修改账号/伪造资格。HK 成功报告保留，未把诊断脚本退出当业务失败或整轮完成。
- JP 21301 的 configs/激活/preview 均成功，验证码在原 25 秒预算内未完成、未发 claim POST；没有增加预算。21303 / 21304 则各自 trace=JP、验证码 280 字符、真实 POST HTTP 200 / 1003，预检证据 `claim-preflight-21303-21304.json`；最终人工固定选择 **HK 21100 / JP 21303 / TW 21202**，不设自动替换。
- 线上页面 JS + 真实 `/static/js/auth.js` 通过配置管理验收：未鉴权 GET/PUT/nodes 均 401，非法 URL 400、失效 selection 409 且策略不变；恢复环境默认保持原 active，随后在地区下拉选择 3/3 覆盖、保存并刷新，active 仍 inherit/environment，saved 为 direct/proxy/database、pending_restart=true。旧鉴权/刷新/并发/通知表单值保留，未调用领取 POST。报告 `admin-live-save.json`。
- **15:52 CST 已重启加载分流**，网关 PID 1518978；active=saved、source=database、pending_restart=false，人工领取 verification_mode=server，报告 `admin-live-active.json`、`split-restart-observed.json`。`.env`、注册表、生命周期、Mihomo 配置哈希与保存前一致，Mihomo/WARP PID 不变。14 个允许 billing 账号覆盖 HK6/JP5/TW3；固定三槽均未改对话 admission。4.3 完成，当前 **17/23**。
- **重启限制**：旧网关停在 Uvicorn “Waiting for connections to close”，超过 systemd 90 秒后被结束，新进程正常启动；短命令 60 秒超时不代表重启未执行，已只读核对新进程/运行策略，不重复重启。未取得旧连接清单，原因未定，不将此事记为生产优雅排空通过，也不以隔离排空测试代替。诊断脚本曾因错误静态资源路径和 eval 词法作用域失败，修正并本地闭包自检后通过；错误日志中的凭证已脱敏，后续对象传值且输出统一脱敏。
- 15:57 CST 读取实际数据库策略，以同版本连接池在隔离进程逐区复验：HK 21100 → `116.49.103.221` / HK；JP 21303 → `160.248.0.143` / JP；TW 21202 → `1.162.168.19` / TW，均 warp=off。configs、激活、preview、claim POST 均 HTTP 200；preview code 0 / 无活动，验证码均 280 字符，POST 均上游 1003。未删除生产持有套餐来绕过防重，诊断直接调用底层提交已持有套餐，仅核销通路；报告 `claim-active-21100-21303-21202.json`。生产后台各区 preview/claim_refresh 也成功；页面真实 JS 拦截已持有，POST /claim 无活动正常跳过，旧 /claim/manual 返回 409 / claim_verification_route_mismatch，见 `accounts-live.json`。新活动人工领取发码/到账仍未发生，以无网络用例覆盖页面服务端分支，不伪装成生产新活动成功。
- 15:58 独立失败隔离：同区有效身份 JP 21300 的真实 preview 因 ConnectError 失败，仅缓存该 claim 代理、没有 direct 兜底客户端；同账号 chat 仍明确 None，真实 trace 为 VPS `107.174.244.167` / US / warp=off，生产配置未改。报告 `failure-isolation.json`；跨出口 token/旧代次和取消隔离由第 1–4 组回归覆盖，不用一次真实请求冒充穷尽验证。
- 分流后 Windows → 公网网关普通请求 200 / OK / stop，3.422 秒；SSE 200 / OK / stop / [DONE]，5.313 秒、流内错误 0。插件状态 host_alive=true、stale=false。`public-after-split.json`，认证文件两端删除。生产 137 秒补货窗口只见 direct refill 3 / hit 2，没有固定 claim-only 后台补货；本次真实代理求解写在诊断独立 evidence，不混为生产事件。
- 无浏览器验收补充：真实旧表单以原值保存后设置及出口均不变；active 为数据库分流时恢复环境默认，只改变 saved/environment 和 pending=true，active 不变，随后通过 API 恢复原完整对象并刷新后 pending=false。报告 `admin-live-active.json`。恢复没有重启/领取副作用；视觉和真实浏览器兼容性仍未验证。第 4 组 4.4–4.6 已达到用户确认的验收边界。
- 回退复核：旧代码归档可读（552 成员）、manifest 和原出口 meta 缺省记录存在，当前远端工作树干净；回退仍只恢复出口 meta/代码、重启网关，不回滚整库或更新器维护的注册表。未启用 WARP，WARP 公网网关验收继续开放。4.4–4.6 核销后 **20/23**，进入第 5 组。

## 第 5 组：规范、交接与交付

- 合并五份主规范；保留 account-client-pool 空闲回收、egress-region-pinning 分类/订阅合并/热重载、regional-captcha-waterfall 节流/CPU 等未变要求及原场景。direct 补货增量的六个场景均保留，并同步当前七场景完整 requirement，防止后续归档覆盖 claim 不常驻预热的规则；两个 change 均不归档。
- 同步主规范 Purpose、CONTEXT 及架构文档：单次用途操作固定实际出口、代理失败不回退、direct 的唯一 None 水库、claim-only 引用、数据库快照/待重启和 3012 证据边界。没有把同账号多 IP 宣称为无风控风险，也未改变账号隔离实现。
- 相关五份主规范 strict、split-claim-and-chat-egress strict、fix-direct-captcha-refill-targets strict、差分空白检查通过。全仓 `openspec validate --specs --strict` 为 8/9：未改的 gateway-unavailability 原有 requirement 超过 500 字符警告，同 HEAD 内容已逐行比较一致；不弱化校验或改无关规范迎合检查，保留独立问题。
- 16:01 运行快照：17 账号（enabled14、active5/exhausted9/disabled3），registry active6/draining135/empty659。13:52 更新器已给 HK21101/21104 对话准入、JP仍空；本轮未手动验这两个 HK 普通/SSE，故 HK/JP 恢复任务不关闭。固定领取三槽仍为 draining、身份/来源有效，磁盘剩余约2.07GiB。
- HANDOFF 已更新真实配置、逐地区结果、兼容边界及后续优先项，早期数字明确保留为历史。独立问题继续包括 TW21200 求解成因、新活动 code0/到账、容量/长期health、WindowsSSL、额度显示、WARP公网、生产停机排空超时。诊断脚本异常曾回显后台密钥，远端日志已脱敏、后续对象传值及输出脱敏；会话工具输出无法撤回，未自行轮换现有密钥，交接明确建议更换。
- 16:01 时 5.1/5.2 完成，**22/23**。当时 Git 暂存被审批服务的 `unknown provider for model gpt-5.6-luna`（model_not_found）阻塞，命令未执行，未绕过审批。
- **16:34 CST 收尾同步完成，23/23**：正常审批已恢复，`b8c9b62b6a82f2684103969df00382abda79efbe` 仅提交 `closeout-owned.json` 的 13 份规范/交接文档，经 push 和 VPS `pull --ff-only` 同步；本机/VPS HEAD 相同、VPS 工作树干净，13 份文件按 LF 归一后 SHA256 一致。原 15 份改动哈希全部未变、未提交。网关 PID 1518978 active，plugin host_alive=true / stale=false，chat direct / claim proxy、三个固定槽及 active=saved / pending=false 未变；此文档同步未重启服务。原验收临时凭证与测试进程清理由 `final-runtime.json` 保留证据。后续模型名修复单独记录，不归入本 change 的业务验收。
- 16:09:59 最终只读核对（`final-runtime.json`）：VPS HEAD 78bf7f1、工作树干净，网关/Mihomo/WARP active/running；active=saved 的 direct/proxy/database、pending=false，host_alive=true/stale=false，受保护配置哈希不变。本轮诊断脚本进程及临时 `.auth` 文件均为零，本机公网脚本也已退出并删除临时凭证；保留必要诊断报告及回退材料。仅关闭本轮新建辅助 Netcatty 会话，保留原工作会话。
