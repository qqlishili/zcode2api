# zcode2api 当前进度与交接

### 本号活动领取资格（2026-10-17，已发布验收）

- 当前承接 `remove-pool-claimable-sharing`，**18/18**。claim 53、sentinel 12、前端全套 26、VPS 独立候选全量 pytest 1280、Ruff app/tests 与 mypy constants 均通过；新增资格 spec strict 通过，未归档。
- 验收发现领取弹窗旧缓存、切号预填和晚返回响应残留，3 项失败复现后已修复。提交 `c06f5af3d0b620e795f5c24655d98f59ea198760` 已推送/VPS 快进，10 文件哈希匹配；运行逻辑仅前端，后端只改注释，无需重启。生产 14 个允许 billing 账号的本号上游 preview 与真实管理 API/SQLite 均为空；3 个停用号跳过上游。实际上线 JS 对 17 号领取条、操作提示和弹窗候选通过，生产无可领正例，正例由隔离测试覆盖；不主动领取活动、不重复清库。
- 历史 11 号目前按名称能对应 5 号且为空，另 6 号不再以原名出现；旧提交 `614b8d3` 在两端当前导入后的 Git 历史中无法解析，保留证据缺口，不能重述为本轮确认的历史发布。详见[资格验收记录](../openspec/changes/remove-pool-claimable-sharing/execution.md)。本机其余 9 份原文档保持。
- Windows→公网→VPS plugin 两模型各普通/SSE共 4 项通过：HTTP200、模型名一致、end_turn、流内错误0，2项 `OK` / 2项 `OK.`。配置哈希、用途策略和三个服务进程未变，plugin host_alive=true/stale=false；回退 `/root/claimable-recovery-20261017-wuaf793b`。证据在本机 `data/diagnostics/claimable-acceptance-20261017/` 与 VPS `/root/claimable-live-20261017/`；新活动实际领取/到账、浏览器视觉与兼容性未验。

### 套餐时间与排序（2026-10-17，已发布验收）

- 用户授权执行下一步，承接 `normalize-plan-time-and-status-display` 与 `plan-slot-start-date-display`，已补齐 spec/design/tasks，各 **4/4**，strict 通过，未归档。业务提交 `8b1750269a4b721967fe69d3aa4713fb54a34ff0` 已推送 main / VPS 快进，16 文件 LF 哈希一致，远端干净；回退 `/root/plan-time-recovery-20261017-yp344yvj`。
- 第一组复现：后端 9 失败/28 通过；真实页面 JS DOM 5 失败/14 通过。结束短文字丢年份导致临期失效，前端本地时区与后端北京时间不一致，未知开始时间导致已开始排序不完整。
- 第一组已验收：相关后端/集成本机和 VPS 独立候选各 **109 项通过**、Ruff 通过；全部前端最终 **23 项**分别在 UTC / America/Los_Angeles 通过（补充旧状态栏/余额项兼容先 3 失败后通过），两方案 strict 和差分检查通过。其余 11 份未提交文档哈希未变，不提交。
- 第二组已验收：在途 0 后只重启网关 **0.372 秒**，PID `1713940` active/running、NRestarts=0；指定两号只刷新额度、不领取，2 成功/0 失败、仍 active。真实 API + 上线页面 JS load/rowHtml 在两时区通过，套餐开始与额度生效分开显示，其他活动余额保留。当前无未来/未知 starts_at 的生产样本，后置边界由隔离 DOM 覆盖。
- 第三组已验收：公网 plugin 两模型各普通/SSE共 **4 项**均 200、模型原大小写一致、end_turn、错误0；3 项逐字 OK，GLM-5.3 SSE 为 OK.。host_alive=true、stale=false。证据 `data/diagnostics/plan-time-20261017/` 的相关回归、after/final DOM、public-after、deploy、updater-attribution 等报告；VPS `/root/plan-time-live-20261017/`。
- 发布后首次核对配置哈希未变；最终 19:51 定时更新器改写 `.env` / 注册表 / 生命周期，严格“全程未变”断言失败并保留。已核对 env 唯一变化为 ZCODE_PROXIES、文件 mtime 与 19:45:32–19:51:31 updater 对应；Mihomo 配置和 Mihomo/WARP PID、数据库出口策略及固定绑定均未变。chat direct / claim proxy、HK21100/JP21303/TW21202、active=saved、pending=false，未回滚正常更新，不把哈希变化掩盖成通过。
- 保留真实结算恢复、浏览器视觉/兼容性、HK/JP 对话恢复等独立待办，不借本次展示工作提前关闭。

### 额度状态与活动展示修复（2026-10-17，已发布验收）

- 按用户补充，按账号全部真实余额判断可选性：单个活动待生效不影响其他活动余额；余额全空时才区分等待生效/等待结算并保持不可选，后台继续刷新。未到账授予量不计入 remaining，不修改调度状态枚举。
- 两个指定账号的真实 API 及已上线页面 JS 已核对：Global Build 均有 1 亿未生效授予量，其他活动仍有余额且账号 active；一个有 Weekend Flash 余额，另一个只有 GLM-5.3 有余额，Flash 已用完。不得因此停用整号或宣称 Flash 可消费。
- 页面固定时间复现 4 失败/2 通过后修复，13 项 DOM 通过；后端额度/模型/冷却第一组 81 项通过，补充多模型后的专用 12 项通过。初轮全量 1258 通过/4 失败，修复旧模型断言、注册表 URL 夹具及全局 sleep 替换干扰后组合 219 项通过；第二轮 1261 通过/1 失败，覆盖率均 82.10%。后者已定位测试更新器的同步进程扫描阻塞同一事件循环，将其独立运行后 9 项 Mihomo 故障场景全部通过，保留原超时及断连断言；失败历史保留。
- 最终全量 **1262 项通过、覆盖率 82.10%**；Ruff/mypy、页面 13 项与两方案 strict 均通过，8 份业务/测试文件与 VPS 隔离候选 LF 哈希一致。两方案规范与状态已对齐；`entitlements-fallback-before-balances` **8/9**，真实同一权益到账后的自动恢复继续开放；`fix-global-build-entitlement-display` **5/5**，不自动归档。
- **业务修复 `d9379fe32ef0234254ee2c7f637b1bdcaef2cb7b` 已提交/推送/VPS 快进上线**，发布提交 `a77584b`。自动审批此前拒绝默认分支推送，用户明确“允许”后正常审批通过；在途为 0 后仅重启网关 0.82 秒，PID `1679556` active/running、NRestarts=0；回退 `/root/entitlements-recovery-20261017-eskas8ag`。本次快速重启不核销此前停机超时根因。
- **真实上线验收通过**：只刷新两个账号，2 成功/0 失败，不领取活动；均 enabled/active，Global Build 1 亿在 23:00 生效、尚无余额桶，其他余额与正常状态保留。真实页面 JS + API 数据 DOM 通过；Flash/GLM-5.3 各普通/SSE 共 4 项公网插件请求均 HTTP 200、模型一致、end_turn、无错误、逐字 OK。`.env`、注册表、生命周期、Mihomo 配置哈希与 Mihomo/WARP PID 未变；chat direct / claim proxy、active=saved、pending=false、plugin host_alive=true/stale=false。真实浏览器视觉、真实结算恢复仍未验。
- 证据：本机 `data/diagnostics/entitlements-20261017/` 的 full-green.xml、checks-green.json、after.json、after-dom.json、public-after.json、deploy.json；VPS `/root/entitlements-verify-20261017-l9kcendz/` 及 `/root/entitlements-live-20261017/`。原 15 份改动中的额度 tasks/execution 和展示 proposal 已承接，保留原复核记录；其余 12 份文档哈希未变，不提交。

### CPA 实际 Messages 链路漏项修复（2026-10-17 17:28 CST，已发布验收）

- 用户报告 CPA 仍提示模型不一致。只读核对本机 CPA `usage-records/usage.db`，17:12:01 记录 `49676` 为 ClaudeExecutor → `POST /v1/messages`，请求 `GLM-5.3-Flash`、响应 `glm-5.3-flash`、HTTP 200；配置指向 VPS `18088`。CPA 的 `claude-opus-5` 等别名先映射为 GLM。此前只修两个 OpenAI 接口，未覆盖 CPA 的 Messages 路径，不能把那轮 16 项验收当成 CPA 已修复。
- 本轮三协议统一执行同名大小写规则。Messages 在规范化前保存请求原名，普通 JSON 及 SSE `message_start.message.model` 复用现有比较函数；其它模型名、事件、思考/签名/用量和错误处理保持。SSE 仅缓冲到模型首帧，后续原始字节与分块继续透传。此规则取代下节 `/v1/messages` 原样返回模型名的约定。
- CPA 真实普通/SSE复现均 HTTP 200 / end_turn / OK，但模型字段不符；路由回归先 **8 失败 / 28 通过**，修复后相关 **240 项通过**，包含三协议大小写/不同模型/别名/缺失模型及 Messages 分块、CRLF、中文、长思考块、尾帧、错误与取消。VPS 按项目实际路径校验三份候选文件 Ruff 通过。
- **业务修复 `410fa4c44e9c6e077d4367b43ed40d96c8bbdec6` 已提交/推送/VPS 快进上线**。等待在途请求归零后，仅重启网关（5.339 秒），PID `1595312` active/running、NRestarts=0；plugin host_alive=true / stale=false。`.env`、注册表、生命周期、Mihomo 配置哈希及 Mihomo/WARP PID 均未变；chat direct / claim proxy、HK21100/JP21303/TW21202、active=saved、pending_restart=false 保持。本机/VPS 三份代码及测试 LF 哈希一致，VPS 工作树干净；回退 `/root/cpa-model-recovery-20261017-yj8napkl`，不核销此前停机超时根因。
- **真实链路 18 项全部通过**：CPA 的 Flash / GLM-5.3 / `claude-opus-5` 映射各普通/SSE，共 6 项；Windows → 公网 VPS plugin 的 Messages 四种请求写法各普通/SSE共 8 项，Chat Completions / Responses 的 Flash 各普通/SSE共 4 项。均 HTTP 200、模型字段符合预期、正常终态、错误 0；13 项逐字 `OK`，另 5 项为 `OK.`。CPA 从 17:26 起抓取的 9 条新 GLM 日志均请求模型=响应模型，包含实际别名链路；旧错误记录保留，不修改 CPA 配置或日志。
- 证据：`data/diagnostics/cpa-model-red-20261017.xml`、`cpa-model-green-20261017.xml`、`cpa-model-20261017/cpa-before.json` / `cpa-after.json` / `live-before.json` / `live-after.json` / `ruff.json` / `deploy.json` / `final-runtime.json`；可复跑 `live_check.py before|after`，凭据从现有 CPA 配置在内存读取，不输出或另存。VPS 本次候选目录/压缩包及本机本轮隔离测试临时目录已清理，报告与回退材料保留；原 15 份未提交改动哈希未变。真实不同模型由隔离回归覆盖，名称一致仍不证明底层模型身份；其它独立任务继续开放。

### 按下游原始大小写回显模型（2026-10-17 16:58 CST，已发布验收）

- 用户明确规则：请求名与上游返回名忽略大小写相同，才回显下游原始大小写；真实不同保留上游原名。两个 OpenAI 兼容接口的普通/SSE统一执行，未报模型时回退请求名。原始请求名独立于出站规范化保存；`codex-auto-review` 等别名与上游实际名不同，不伪装一致。`/v1/messages` 继续原样透传。本节取代下节 16:41 版本的官方大小写映射。
- SSE 先发不含模型名的注释，解析上游 `message_start.model` 后再生成带模型名的首帧；修复旧 SSE 忽略上游模型、提前回显请求名的问题。普通响应和两个流转换器共用相同比较逻辑，不新增模型目录或依赖。
- 新规则回归先 **28 失败 / 10 通过**，修复后相关 **218 项通过**，包括实际路由原始名保留、首帧/后续帧、大小写/不同模型/别名/缺失模型，以及现有用量/取消/异常/终态回归；VPS 独立候选及实际发布文件 Ruff 通过。真实不同模型由隔离回归验证，线上未人为制造模型替换，不将模型名称字段当作底层模型身份的证明。
- **业务修复 `b7140203a196cedbdaedc45695edd2c3f2729418` 已提交/推送/VPS 快进上线**。等待在途请求归零后，仅重启网关（0.367 秒），PID `1570725` active/running、NRestarts=0；plugin host_alive=true / stale=false。本机/VPS 五份源码及测试的 LF 归一化哈希一致、远端工作树干净；原 15 份未提交文档哈希未变。`.env`、注册表、生命周期、Mihomo 配置哈希与 Mihomo/WARP PID 均未变，chat direct / claim proxy、HK21100/JP21303/TW21202、active=saved、pending_restart=false 保持。回退材料 `/root/model-case-recovery-20261017-mqc6alpb`；本次快速重启不核销此前停机超时根因。
- **Windows → 公网 → VPS plugin 完整链路共 16 项通过**：`GLM-5.3-Flash`、`gLm-5.3-fLaSh`、`GLM-5.3`、`glm-5.3` × Chat Completions / Responses × 普通/SSE，均 HTTP 200、模型字段精确等于原始请求、正常终态、错误 0；SSE 汇总首帧及后续全部模型字段。11 项逐字 `OK`，另 5 项为 `OK.`，不记作逐字全通过。
- 证据：`data/diagnostics/model-case-red-20261017.xml`、`model-case-green-20261017.xml`、`model-case-20261017/public-after.json` / `ruff.json` / `deploy.json` / `final-runtime.json`；可复跑脚本 `model-case-20261017/public_check.py`。两端临时网关认证文件、VPS 本次候选目录与压缩包已清理，回退及报告保留。`/v1/messages` 原样透传；HK/JP 对话恢复、新活动到账、容量/长期 health 等独立任务继续开放。

### 模型名大小写与另一模型实测（2026-10-17 16:41 CST）

- VPS 真实 plugin 链路已复现：请求 `GLM-5.3-Flash` / `GLM-5.3`，普通响应及原始 Anthropic SSE `message_start` 分别返回小写 `glm-5.3-flash` / `glm-5.3`。当前 Chat SSE 使用请求名，普通响应保留上游名，产生名称大小写不一致；未观察到 Flash 被换成 GLM-5.3。名称字段本身不证明底层模型身份。
- 两模型的普通/SSE均 HTTP 200、正常结束、无流内错误；GLM-5.3 的严格 `OK` 断言有一轮未通过，原始流补测实际返回 `OK.`，不把逐字输出验证记为全通过。当前 `/v1/models` 仅公布这两个模型，不新增未有套餐证据的模型。
- 已在两个 OpenAI 兼容层复用 `MODEL_NAME_MAP` 修正普通响应名称；真实返回其它模型及未知名仍保留，未上报才用请求名。`/v1/messages` 维持原样透传小写。新增回归先 6 失败 / 6 通过，修复后相关 122 项通过；VPS 候选及发布文件 Ruff 通过，分流 OpenSpec strict 通过。
- **修复 `cc35fed975b6b312b7c3c7b0c00c50812b52d157` 已提交/推送/VPS 快进上线**。等待在途请求归零后，仅重启网关（0.266 秒），新 PID `1556299` active/running；本机/VPS 三份源码哈希一致，VPS 工作树干净、plugin host_alive=true / stale=false。`.env`、注册表、生命周期和 Mihomo 配置哈希未变，Mihomo/WARP PID 未变；chat direct / claim proxy、HK21100/JP21303/TW21202及 pending=false 保持。回退材料 `/root/model-name-recovery-20261017-8e_j9j7b`；本次快速重启不补齐前次停机超时根因。
- **Windows → 公网 → VPS plugin 完整链路共 12 项通过**：两模型 × Chat Completions / Responses / Anthropic Messages × 普通/SSE，均 200、非空预期文本及正常终态，流内错误 0；两个 OpenAI 接口模型字段均为官方大小写，Anthropic 原样小写。9 项逐字 `OK`，另 3 项为 `OK.`，分别保留严格断言，不记作逐字全通过。
- 证据：`data/diagnostics/model-name-check-20261017-before.json`、`model-name-red-20261017.xml`、`model-name-green-20261017.xml`、`model-names-20261017/public-after.json` / `deploy.json` / `final-runtime.json`；可复跑脚本 `model-names-20261017/public_check.py`。VPS `/root/model-name-check-20261017.json` 等报告保留。本机 Ruff 进程未返回，已仅停止本次进程；两端临时网关认证文件、VPS 本次候选目录与压缩包均已清理，原 15 份改动哈希未变。未接入或声称验证其它未公布模型。

### 分流实施与验收（2026-10-17 16:34 CST）

按用户授权执行 `split-claim-and-chat-egress`，采用逐组实现/验收/修复，当前 **23/23**。13 份收尾文档已提交 `b8c9b62`、推送并 VPS 快进，本机/VPS HEAD 及文档哈希一致、远端工作树干净，原 15 份改动未变。先前自动审批服务故障已恢复，本次走正常审批完成。业务提交 `78bf7f1` 于 04:22 CST 上线，先以 inherit 保全直连基线，再于 15:52 前后加载显式分流：**plugin 对话 direct，领取 proxy，HK 21100 / JP 21303 / TW 21202**。数据库来源，active=saved、pending_restart=false；入口为 `/admin/settings`「配置管理」，[配置说明](egress_configuration.md)。当前进度以 tasks 为准，以下早期规划和快照均保留各自日期。

- 验证：810 个独立 testcase 取最新结果均通过，Ruff app/tests、相关 OpenSpec strict 和页面 DOM 行为通过。切流后 Windows → 公网网关普通/SSE 均 200 / OK / stop，SSE 有 [DONE]、流内错误 0；host_alive=true、stale=false。
- 领取：三地区真实同区 trace、configs/激活/preview、280 字符服务端验证码、POST HTTP 200 / 1003 通过；真实页面/后台活动查询、无活动跳过及已持有拦截通过。**1003 只核销通路，无新活动 code 0 和到账验收仍开放。**JP 21300/21302 HTTPS 连接失败、21301 求解预算耗尽，人工选择验证通过的 21303；没有自动换槽或跨区。
- 配置验收按用户明确确认采用真实 API + 已上线页面 JS 的 DOM + VPS 插件完整链路。保存后仍旧策略、刷新待重启、重启加载、恢复环境默认/复原、错误 400/409、鉴权 401、原表单保存及脱敏均通过；视觉布局和真实浏览器兼容性未验。实际失败 claim 无直连兜底，同账号 chat 仍可直连。
- **运行限制**：本次重启旧进程等待连接关闭超 90 秒，由 systemd 结束后启动 PID 1518978；服务现正常，原因未定，不算生产优雅排空验收。诊断脚本一次报错曾回显后台密钥，远端日志已脱敏、后续输出统一脱敏，但会话工具输出无法撤回，密钥尚未轮换，建议更换。未启用 WARP，不关闭其公网网关验收。
- 所有报告在本机 `data/diagnostics/split-egress-implementation-20261017/` 及 VPS `/root/split-egress-live-20261017-hd4wu2je/`；[执行记录](../openspec/changes/split-claim-and-chat-egress/execution.md)逐项列证据与限制。回退目录 `/root/split-egress-recovery-20261017-wsos0gff`，仅恢复代码/出口 meta 并重启，不回滚整个账号库或更新器维护的注册表。

> 整理日期：2026-10-17（CST）；direct 补货修复于 **01:44** 上线，公网及补货验收至 **01:46**，服务/账号于 **01:50** 再核对，定时探活后的出口状态更新至 **01:55**。
> 分流方案于 **02:17** 完成规划核验；02:01 / 02:17 只读复核 VPS，未重启或发模型/领取请求。
> 配置管理增补于 **03:04** 保存 Antigravity CLI 复审报告：SHIP、P0/P1/P2=0，仅方案文本，实施仍为 **0/23**。
> 本页是当前进度入口。运行快照、已完成验证、未完成任务分别记录；逐次诊断见专项记录，早期迁移流水见 Git 历史。

**线上为 plugin 对话直连 + 固定同区代理领取，分流已实现和真实验收；HK / JP 对话恢复仍未核销。**13:52 更新器将 HK 21101/21104 列为对话 active、JP 仍无准入，本轮未对这两个 HK 节点手动复验普通/SSE，不将更新器准入等同于恢复已验收。

接手先读本页第四、五节，再读选定 change 的 proposal / tasks / execution。详细证据见[出口恢复记录](egress_recovery_20261016.md)，全部节点见[节点与机场清单](egress_node_inventory_20261016.md)。

### 配置页增补与独立审查（2026-10-17 CST）

- 用户要求把后台配置管理加入现有分流方案，并通过 Antigravity CLI 审查。本轮仅更新规划，未实现页面/API或改变线上出口。
- 配置管理复用 `/admin/settings`，保留既有设置，新增对话/领取出口与各区节点下拉、当前生效/已保存/来源/待重启；用户不手填节点指纹/代次。
- 数据库完整出口对象优先于环境默认；保存和恢复默认只改待生效目标，重启后统一加载，页面不能暗示保存即切流。部署参数继续留 `.env`，不提供任意配置键编辑。
- 新增持久化/生效快照、鉴权 API、页面及配置 E2E 共 5 个待办，实施由 0/18 调整为 **0/23**；固定引用按当前生效绑定计算，未重启的删除保存不能提前回收节点。
- Antigravity CLI 已对8份方案文本完成首审及同会话复审，最终 **SHIP，P0/P1/P2 均为0**。首轮四条建议已补成明确场景/断言，详见[真实审查记录](split_egress_plan_review_20261017.md)；关联源码外发调用被自动审批拒绝，最终仅审用户授权的方案文本，不包含源码或运行验收。

### 原始分流规划交接（2026-10-17 02:17 CST）

- 初版创建 proposal、design、5 份 capability 增量及 18 项未勾选 tasks，当前增补为 23 项；`openspec status` 的 planning complete / artifacts done 只代表方案文件齐备，**不是实现完成**。
- 拟新增 chat / claim 用途配置，默认继承旧全局模式；首次上线建议对话继续 direct，领取配置同区固定 `(port,node_id,epoch)`；WARP 为可选对话代理，不自动启用。调用边界和配置名见[设计](../openspec/changes/split-claim-and-chat-egress/design.md)。
- 固定领取绑定独立于对话准入；保留来源、身份、退役和固定引用保护，不能通过把 HK / JP 改成对话 active 来绕过。插件的二次直连兜底纳入待修，不能只改 claim POST。
- 兼容变化已明示：显式领取用途下，人工选择套餐仍保留，改用服务端验证码；旧浏览器 token 接口在联网前拒绝。继承旧模式保留原浏览器路径，不冒充亲和已验证。
- 02:17 HK `21100` / JP `21300` 来源存在、epoch=1、未退役、身份匹配，聊天 admission 仍为 draining；仅核对绑定前提，未重做真实领取。enabled JWT 分布 HK 6 / JP 5 / TW 3，TW 也需独立配置和验收，不能只配 HK/JP 后声称全账号完成。
- 本轮仅规划与只读核对。VPS 业务 HEAD `4695bb2`、网关 PID `1132673`，原 15 份文档改动哈希保持不变；后续方案文档通过 Git 同步，无需重启服务。

### 最新修复与下一步优先级（2026-10-17 01:44–01:55 CST）

- **已修复 direct 补货目标/缓存不一致**：过去计划仍返回 TW `21202`，producer 被 direct 强制改为无代理，导致后台检查空代理水库、持续向直连水库求解。现在 direct 的计划目标统一为 None，与实际出口、缓存键和水位一致，proxy 的稳定绑定与离线门禁保留。
- 修复业务提交 `d147f5f8b937ab7e8942bd4cf8504318bd318503` 已在本机/VPS 同步，三个修改文件哈希匹配；仅重启网关，生产配置、注册表、生命周期及 Mihomo 配置哈希一致，Mihomo/WARP 进程未变。01:50 网关正常，17 个账号仍为 active 5 / exhausted 9 / disabled 3。
- **验收后运行状态有独立变化**：定时 `mihomo-probe-updater.service` 于 01:45:22–01:51:51 运行并正常退出，01:51 更新 `.env`、注册表和生命周期；01:55 准入变为 5 个，新增 TW `21201`，HK / JP 仍为 0。网关 PID 未变，实际进程和 `.env` 仍为 `plugin + direct`；不将这次定时更新误记为修复发布改配置，也不宣称新增节点已手动复验普通/SSE。
- **验证**：修复前定向回归 4 失败；修正后本机相关 44 项通过，VPS 隔离相关 104 项通过（有重叠，不相加），Ruff 与 OpenSpec 校验通过。公网普通对话 200 / `OK` / stop，SSE 200 / `OK` / stop / `[DONE]`，流内错误 0。
- **线上观察 140.5 秒，超过 token TTL 75 秒**：重启前后同长度窗口 refill 22 → 6、hit 7 → 6，均有 7 次成功业务请求；后窗口另有 2 次 foreground。满水位停止补货由回归覆盖，生产末尾 45.9 秒没有新 refill；正常消费/过期补货仍保留，不把 22 次全部判为浪费，不将短窗观察当作容量验收。
- 01:07–01:13 已复现的本机标准 venv SSL 故障、pending 额度已生效仍显示“未生效”未修；TW `21200` 验证码失败也未由本次修复解释。早先 `ClientDisconnect` 在读取请求体、尚未派发上游，不据此判定整体对话故障。
- 当时建议先规划领取/对话分流，该规划及配置页增补已完成，实施仍为 0/23；之后推进待结算语义及页面验收。补货修复只关闭其错配子项，第四节主任务不越界核销。验收与回退路径见[补货执行记录](../openspec/changes/fix-direct-captcha-refill-targets/execution.md)。

## 一、当前项目状态

以下当前项更新至 2026-10-17 16:01 CST，历史实测另标日期。分流已重启网关、实际领取通路及公网对话验收；未手动运行探活更新器、未改账号地区、未启用 WARP。

| 项目 | 当前事实 |
|---|---|
| 本地 / 分支 | `D:\Temp\lizi\zcode2api`，`main`；当前业务 `78bf7f1267082bf1f580ebcdb1e7b8bfe056be17`，后续规范/验收文档提交见 Git |
| VPS / 版本基线 | `/root/code/zcode2api`；业务 `78bf7f1`、16:01 工作树干净；文档继续 Git 快进，无需重启 |
| 已发布业务修复 | `9ad47f7` 修正显式 direct 的调度、区域过滤和连接池；`d147f5f` 补齐 direct 验证码补货目标与水位一致性 |
| 网关 | active/running，PID `1518978`、NRestarts=0，端口 `18088`；`/meta` 200、版本 `2.6.7`；旧进程停机超时限制见顶部 |
| 实际出口策略 | 环境 plugin + 全局 direct；数据库覆盖 chat=direct / claim=proxy，HK21100/JP21303/TW21202，active=saved、pending=false |
| Mihomo | active/running，PID `2797546`、NRestarts=0；800 个回环 listener，端口 `21100–21899` |
| WARP | active/running，PID `755387`、NRestarts=0，Connected；`WarpProxy on port 40000`、MASQUE，项目当前未选用 |
| 出口更新器 | 最近 **2026-10-17 13:45:30–13:52:11 CST** 正常运行退出、ExecMainStatus=0；定时变化独立于分流发布 |
| 账号 | **17 个**：enabled 14，active 5 / exhausted 9 / disabled 3；允许 billing 的 JWT 大区 HK6 / JP5 / TW3，全部配置同区领取 |
| 注册表 | 800 槽：**active 6 / draining 135 / empty 659**；generation `f8760bb48277be5df0b9d5d1af068b1fde0ddb3a9e08461632ae0cb2f41a847d` |
| 当前对话准入端口 | HK `21101/21104`、TW `21200/21201`、US `21506`、OTHER `21833`；JP 为 0。固定领取 `21100/21303/21202` 均 draining、来源有效，不修改其 chat admission |
| 节点来源 | 143 条定义，两来源分别 67 / 76 条；其中 1 条为流量信息占位，实际业务节点 142 个 |
| 最近公共 HTTPS 出口 | 00:53 显式无代理 trace：200，`107.174.244.167`、US、`warp=off`；01:07 未重复该公共请求 |
| 磁盘 | 16:01 Python disk_usage：剩余约 **2.07 GiB**；未清理旧副本 |

此前“16 个账号、active 3”和“准入 active 7”均为旧快照。服务 active、账号 active、准入 active 不等于当前真实模型请求成功；业务验收限于下节注明的时间和范围。

## 二、已完成与实际验证范围

| 已完成事项 | 证据与覆盖限制 |
|---|---|
| 显式 direct 恢复 | `9ad47f7` 已上线；`app/client_pool.py` 与相关测试本地/VPS SHA-256 本轮再次匹配。proxy 模式的同区隔离和禁止静默直连保留 |
| direct 修复回归 | 2026-10-16 新增 3 项回归先失败，修正后 9 份相关测试共 71 项通过；最后受影响文件 12 项复验通过，二者有重叠，不相加；VPS Ruff 通过 |
| direct 验证码补货错配修复 | 2026-10-17 `d147f5f` 上线；新增满/空水位、账号过滤及 proxy 隔离回归，红 4 失败 → 绿；相关 VPS 104 项与 Ruff 通过。生产 140.5 秒同长窗口 refill 22 → 6，容量与 TW 故障仍开放 |
| 公网直连普通请求 / SSE | 2026-10-16 发布后及 17:13 复测通过；2026-10-17 01:45 补货修复后再验：Windows → 公网 `/v1/chat/completions`，`GLM-5.3-Flash`，普通 2.890 秒、SSE 5.719 秒，均 200 / `OK` / stop；SSE 有 `[DONE]`、流内错误 0 |
| VPS 直连 / TW / WARP 对话对照 | 2026-10-16 21:08–21:09：固定账号、官方插件，三路普通请求和 SSE 均 200 / `OK` / `end_turn`，流起止事件完整、错误 0 |
| 领取 POST 通路 | 2026-10-16 21:07–21:10：直连、HK `21100`、JP `21300` 同区补测、WARP，携带各出口新解验证码，对已持有套餐 POST 均 200 / code 1003（已领取） |
| 代理连接能力 | 2026-10-16 分时证据：131 个节点有完整代理 HTTPS 成功证据，10 个仅入口 TCP 可达，1 个入口 DNS 失败；不等于 131 个对话可用 |

**WARP 尚未接入生产，未完成 WARP 下公网网关完整验收；新活动真实领取发额度未验收；未扩大为所有模型、全部 API 或长期账号安全已验证。**

## 三、出口、领取与对话：已讨论的结论

### 1. 当前配置在哪里，能控制什么

| 配置或入口 | 作用 |
|---|---|
| VPS `.env` 的 `ZCODE_EGRESS_MODE=direct` | 旧全局直连；用途 inherit / 匿名启动沿用它。显式用途策略优先，当前 claim 不被全局 direct 覆盖 |
| `ZCODE_EGRESS_MODE=proxy` | 按账号代理/大区绑定选择 Mihomo 出口；已绑定大区离线时不静默降级直连 |
| `ZCODE_PROXIES` | 全局代理候选列表，不是“只给领取使用”的配置 |
| `/etc/mihomo/config.yaml` 的 listeners | 用 `proxy: zcode-slot-<端口>` 指向槽位，例如 `21100` / `21300` 为对应 HK / JP 的入口 |
| Mihomo `mode: rule`、`rules: [MATCH,DIRECT]` | 全局规则快照；不能因此认定指定了 proxy 的 listener 都直连。当前网关 direct 是应用主动绕过 Mihomo |
| WARP `127.0.0.1:40000` | 可显式选用的本地代理；不是系统全局出口，不会被项目 direct 自动使用 |

**当前已提供用途独立开关及配置页**，数据库优先、重启生效，操作见[出口配置管理](egress_configuration.md)。二者都访问 `zcode.z.ai`，HTTPS 加密具体路径，普通代理模式下 Mihomo 不能自行区分；由项目按用途选直连或固定代理入口。

### 2. 领取是否同样返回 3012

查询为 `GET /api/v1/zcode-plan/billing/preview`，领取为 `POST .../billing/claim`，对话为 `POST .../anthropic/v1/messages`。同一域名不意味着各接口采用相同拒绝规则。

| 出口 | 领取 POST（2026-10-16） | 同轮对话普通请求 / SSE |
|---|---|---|
| VPS 直连 | 200 / 1003，已领取 | 均通过 |
| HK `21100` | 200 / 1003，已领取 | 普通请求 405 / 3012，未继续 SSE |
| JP `21300` | JP 账号同区补测 200 / 1003；首轮另一账号验证码预算耗尽，未发 POST | 普通请求 405 / 3012，未继续 SSE |
| TW `21200` | 验证码未通过本地校验，未发 POST | 普通请求和 SSE 均通过 |
| WARP `40000` | 200 / 1003，已领取 | 普通请求和 SSE 均通过 |

19:17 的两账号 × 五出口 × preview/current，共 20 项 GET 均 200 / code 0。21:10 对当时 13 个允许 billing 的账号逐个查 preview，均为空；**该历史结果不覆盖本轮账号变化后全部 14 个 enabled 账号的当前活动资格。**

1003 只证明进入“已领取”业务返回，不证明新活动能发额度，也不证明上游在去重返回前一定检查了验证码。不能借其它账号的活动候选补齐领取验收。

**验证码失败的直白含义**：TW `21200` 当时连续 4 次没拿到可用结果，只返回 76 字符、未通过本地有效性校验，因此领取请求根本没发出去。该节点的查询和对话当时可成功；验证码这一步的失败原因仍未查清，不能写成领取接口 3012、节点完全断网或验证码已被上游拒绝。

### 3. 全量节点结论

2026-10-16 18:14–18:32，固定账号、每节点新插件宿主、不附验证码头，142 个业务节点：**4 个普通对话和 SSE 通过、125 个 405/3012、13 个两次请求失败**；另 1 个信息占位不计正常或故障节点。

- 对话通过：赔钱机场 `21200` 台湾2；kcyun 来源 `21202` 台湾 Taipei S5、`21506` 美国 Philadelphia S23、`21833` 加拿大 Toronto S3。
- 赔钱机场 66 个业务节点：正常 1 / 3012 63 / 请求失败 2；kcyun 76 个：正常 3 / 3012 62 / 请求失败 11。kcyun 对应快冲云按文件名推定，具体文件原始订阅地址未保留。
- 公共 HTTPS 补测：旧失败的 `21201`、`21206` 也能代理访问；10 个其它节点只确认入口 TCP 可达；HK `21103` 入口域名经系统及两家公共 DNS 确认无法解析（公共 DNS 为 NXDOMAIN）。
- HK `21100` / JP `21300` 公共请求显示实际 HK / JP 出口，证明 VPS 能经这两个节点通信；3012 是该轮 ZCode 请求收到的拒绝，不能解释成 VPS 根本连不上机场。

01:07 准入 4 个与旧全量对话测试的 4 个一致；01:51 定时探活后准入新增 TW `21201` 为 5 个，未手动复验该节点当前普通/SSE，也未重跑全量业务验收，旧扫描结论保留原日期。完整节点名、机场、端口及连接能力见[节点清单](egress_node_inventory_20261016.md)。现有证据支持出口相关拒绝，**具体风控规则、永久封禁、纯 IP 信誉成因均未确认；不能说所有机场节点都不可用。**

### 4. 分流方向与规范冲突

已实施方向：**活动领取及其验证码走固定同区节点，对话走直连，WARP 可选但未启用。**[split-claim-and-chat-egress](../openspec/changes/split-claim-and-chat-egress/execution.md) 已覆盖 HK/JP/TW，不能跨区兜底。

正式方案已界定手动/自动领取、preview、领取内激活、验证码 producer/补货、额度查询、安装、后台探针的归属；实施须保持同一领取操作的验证码与提交同出口、同身份，保留同区隔离、领取节流及退役引用。不能只给 claim POST 换代理，也不能用对话探针失败直接证明节点不能领取。

五份主规范已同步，原“账号全生命周期单一代理/禁止任何直连”更新为[同次用途操作一致](../openspec/specs/strict-egress-convergence/spec.md)；direct 水位场景、固定引用和代理失败不回退保留。原 direct 补货增量同步当前 requirement，防止后续归档覆盖分流场景；本轮不归档。

直连会让多个账号共享 VPS IP；共享 WARP 也不等于每账号独立 IP。上游仍可能关联账号，短时成功及 risk_strikes=0 均不能保证长期安全。

## 四、核对后的任务与剩余限制

**原确认的 11 项待办中，“活动授予额度展示”“套餐时间与状态”“套餐开始日期与排序”“本号活动领取资格”共 4 项已完成，余 7 项仍开放；新增“出口分流（含后台配置管理）”23/23 已完成，均未归档。**下表保留这 12 条的状态；TW 验证码失败作为第 10 项独立子项，不重复计数。目录数、勾选数、业务待办数使用不同口径，不能把方案齐备当作业务完成。

| # | 任务 | 当前进度与下一步 |
|---|---|---|
| 1 | HK / JP 对话出口恢复 | **未核销**；直连已验收。最新更新器列 HK 21101/21104 为 active，但未手动补普通/SSE；JP 无准入。分流不冒充地区对话恢复 |
| 2 | 额度待结算语义 | **8/9**；按账号全部真实余额判定、修复上线及真实页面/插件回归完成；同一权益真实到账后的自动恢复未验 |
| 3 | 活动授予额度展示 | **5/5，已发布验收**；过去/未知时间正确待结算，13 项 DOM、上线页面/API及公网插件通过；真实浏览器视觉未验 |
| 4 | 套餐时间与状态 | **4/4，已发布验收**；北京时间短格式、完整结束时间判断临期、真实 API/上线 JS 与插件通过，状态沿用全部真实余额规则 |
| 5 | 套餐开始日期与排序 | **4/4，已发布验收**；套餐期与不同的额度生效期独立，已开始升序、未知居中、未来后置，当前生产无未来样本，边界由隔离 DOM 覆盖 |
| 6 | 插件热重载与管理面板 | 部分实现；完成重载 API、UI 入口、状态/同步及客户端中断统计分离 |
| 7 | 移除池内活动领取共享 | **18/18，已发布验收**；本号资格、页面旧缓存/切号隔离、当前数据及插件回归通过；无生产正例，新活动到账独立开放，不重复清库 |
| 8 | 登录成功事件 | 事件及 installed_at 守卫已实现；新账号 OAuth 到奖励完成的真实闭环、bigmodel 实测待验收 |
| 9 | 指纹一致性调查 | 未完成；跨通道逐字段比较实际外发身份和上游影响 |
| 10 | 验证码容量与失败排查 | **direct 补货目标/缓存错配子项已修复上线并验收**；冷启动、稳态及同批次命中率仍待验收；TW `21200` 拿不到有效验证码的原因仍待查，不弱化校验 |
| 11 | 本地标准测试环境 | Windows `.venv` 的 SSL DLL 故障待修；基础 Python 预加载 SSL 的替代执行不算 venv 已修复 |
| 新增 | 领取 / 对话独立出口配置及管理页 | **23/23，已上线验收并提交同步，尚未归档**：对话 direct、HK/JP/TW 固定代理领取，真实管理页面 JS/API 和公网普通/SSE通过；有新活动再沿原待办补 code 0/到账 |

独立运维事项保留：vendor host.js 升级评估、磁盘/旧备份清理、3 个重复 change 目录处置。本次整理没有授权自动升级、删除或归档，不算已完成。

## 五、OpenSpec 方案台账

2026-10-17 本地 `openspec list --json`：**15 个未归档目录、15 个归档目录**；3 项 in-progress、8 项 complete、4 项 no-tasks。complete 只表示清单全勾，no-tasks 不表示代码全未实现。原 15 份未提交文档中累计 6 份已承接到相关提交，另 9 份保留，见第八节。

| 方案 | 清单进度 | 当前判断 |
|---|---|---|
| [split-claim-and-chat-egress](../openspec/changes/split-claim-and-chat-egress/proposal.md) | **23/23** | 业务/配置管理/三地区领取通路/插件公网普通和 SSE 已验收；第 5 组提交同步完成，尚未归档，新额度到账、真实浏览器视觉及 WARP 公网仍未验证 |
| [fix-direct-captcha-refill-targets](../openspec/changes/fix-direct-captcha-refill-targets/execution.md) | **6/6** | 本次修复、回归、上线与真实业务/补货观察已完成；未归档，不关闭验证码容量主线 |
| [egress-blacklist-investigation](../openspec/changes/egress-blacklist-investigation/proposal.md) | 未拆 tasks | 调查未完；早期“被拉黑/2 个可用”不替代第三节新证据。此 change 只做调查，选路改动应另开 change |
| [captcha-pool-capacity](../openspec/changes/captcha-pool-capacity/proposal.md) | 未拆 tasks | 容量、冷启动与稳态命中率调查未完成 |
| [entitlements-fallback-before-balances](../openspec/changes/entitlements-fallback-before-balances/proposal.md) | **8/9** | 状态要求按全部真实余额对齐，1262 项全量与发布验收通过；真实同权益结算恢复仍开放 |
| [fix-global-build-entitlement-display](../openspec/changes/fix-global-build-entitlement-display/proposal.md) | **5/5** | 规范/任务补齐，页面修复、真实上线 API/JS DOM 及公网插件验收通过；未归档，视觉未验 |
| [normalize-plan-time-and-status-display](../openspec/changes/normalize-plan-time-and-status-display/proposal.md) | **4/4** | 109 项相关回归、双时区 DOM、上线 API/JS 与公网插件通过；已发布，未归档，视觉/兼容性及真实结算恢复独立未验 |
| [plan-slot-start-date-display](../openspec/changes/plan-slot-start-date-display/proposal.md) | **4/4** | 套餐期与额度期独立、排序边界和真实当前顺序通过；无生产未来样本，隔离覆盖，不归档 |
| [plugin-hot-reload](../openspec/changes/plugin-hot-reload/proposal.md) | 未拆 tasks | 探针已按账号解析代理，避免重复修 R15；重载与管理入口仍缺 |
| [remove-pool-claimable-sharing](../openspec/changes/remove-pool-claimable-sharing/proposal.md) | **18/18** | 已发布验收；全量1280、前端26、本号上游/API/SQLite/上线JS与公网plugin普通/SSE通过；无生产正例，历史引用与视觉限制明确保留 |
| [report-app-login-success-event](../openspec/changes/report-app-login-success-event/proposal.md) | **7/12** | 新账号真实闭环及 bigmodel 未验收 |
| [unify-activation-fingerprint](../openspec/changes/unify-activation-fingerprint/proposal.md) | 未拆 tasks | 完整指纹调查仍开放，原引用不足不能推出所有路径一致 |
| [pin-captcha-egress-and-refill-domain](../openspec/changes/pin-captcha-egress-and-refill-domain/tasks.md) | **23/24** | 有同名 2026-10-15 归档副本；8.1 旧未勾与归档低频/高频记录并存，验收缺口归入验证码事项 |
| [fix-captcha-failure-diagnostics-and-scheduling](../openspec/changes/fix-captcha-failure-diagnostics-and-scheduling/tasks.md) | **24/24** | 有同名归档副本；不重做，不宣称本轮全部复验 |
| [isolate-mihomo-probe-from-production](../openspec/changes/isolate-mihomo-probe-from-production/tasks.md) | **30/30** | 有同名归档副本；历史验收不替代当前生产连续性验收 |

原“10 个非重复变更 + 1 个本地环境 = 11 项”中已完成 4 项，余 7 项，再加已完成的分流主线。验证码旧副本验收缺口及已完成补货子项并入验证码主线。3 个进行中目录共 **7 个未勾框**，含 pin 的旧副本项；不是全项目只剩 7 个子任务，4 项还没有正式 tasks。

## 六、此前误判、失败与核销边界

- **撤销 health 漏写诊断，保留业务故障**：注册表槽位为 node_id / epoch / region / admission，没有 health 字段；应查生命周期 state 到 admission 的对应，不能因此关闭 HK / JP 恢复。
- **额度原失败已修复上线**：planGroupHtml 在 effective_at 已过去、余额未结算时显示“未生效”的复现已转绿；原 205 项隔离测试未覆盖。用户现已明确按全部真实余额判断可选性，proposal/design/tasks 已统一；真实到账恢复仍未核销。
- **撤回不充分核销**：此前新增 24 个勾选撤回 2 个，保留 22 个实现/离线子项（3 / 12 / 7）；不据此把整个额度、共享移除或登录事件方案标完成。
- **指纹调查未关闭**：匿名首启常量档案不能证明账号路径错误；部分路径使用 profile_for(account)、插件带账号 device_mid，也不能证明全部实际外发字段一致。
- **无效诊断不作结论**：切换隔离 Mihomo selector 却复用插件宿主时，旧连接可能仍走前一出口；该批 TW 失败/HK 成功不采信，最终逐节点扫描重建宿主。curl --http2 实际协商仍为 HTTP/1.1，未完成真正 H2 对照。
- **验证码覆盖不夸大**：早期扫描有 fake_param，全量最小插件扫描未附验证码头；部分固定 HK / JP 做过真实验证码对话对照，不代表全部节点均完成此验收。

## 七、验证记录与证据入口

| 入口 | 内容 |
|---|---|
| [出口恢复记录](egress_recovery_20261016.md) | 直连修复发布、真实请求/SSE、三项目源码、HTTP/SOCKS/TLS、请求形态、领取 POST、WARP 及原始报告路径 |
| [全部节点清单](egress_node_inventory_20261016.md) | 142 个业务节点 + 1 个信息项；机场、节点名、端口、对话结果及连接能力 |
| `data/diagnostics/handoff-20261016/`（本地忽略目录） | 原 205 项测试日志/JUnit、pending-label-review.json 失败复现、direct 红绿回归、全量和领取/对话报告；文件名索引见专项记录 |
| `data/diagnostics/handoff-20261017/`（本地忽略目录） | 整理前交接副本、15 个既有改动的哈希基线，用于核对未误改 |
| `data/diagnostics/status-review-20261017/`（本地忽略目录） | 01:07–01:13 本机/VPS核对、修复前 direct 补货隔离复现和生产统计、页面函数复现、原有改动哈希 |
| `data/diagnostics/direct-refill-fix-20261017/`（本地忽略目录） | 本次红绿/104 项 JUnit、公网普通/SSE、发布 manifest、140.5 秒前后观察、最终服务及定时更新器状态、15 份原改动哈希基线 |
| `data/diagnostics/split-claim-and-chat-egress-20261017/planning-checks.json`（本地忽略目录） | 分流方案链接/规范场景保留检查、18 项均未实施、原 15 份改动及本机/VPS 业务源码哈希核对；不是分流运行验收 |
| [Antigravity CLI 方案审查](split_egress_plan_review_20261017.md) | 配置页增补后的8份方案文本首审/复审、4条意见落实、最终 SHIP/零缺陷及范围限制；原始 CLI 证据在 `data/diagnostics/split-egress-admin-review-20261017/` |
| VPS `/root/egress-direct-recovery-20261016-0gck0fjw/` | 回退材料，env.before / client_pool.py.before 本轮确认仍存在；保全，不入 Git |
| VPS `/root/direct-refill-recovery-20261017-uyh4tk_q/` | 本次补货修复旧源码、配置哈希、发布及观察证据；保全，不入 Git |

原 205 项测试使用基础 Python 预加载 SSL，再加载 `.venv/Lib/site-packages` 依赖；不是标准 venv 或全量检查通过。Windows Ruff 曾因 DLL 初始化失败退出，改用 VPS Ruff 检查对应候选文件。

本轮 VPS 隔离候选的整库 pytest（1262 项）、Ruff/mypy 已通过；Windows 标准 venv 仍有独立 SSL 故障。当前额度页面已有 13 项 JS DOM 及新上线页面/真实数据核验，公网插件 4 项通过。前轮三协议模型名称实测以顶部专节为准，不推广为所有模型的完整验收。

**仍未验收**：新账号 OAuth 奖励闭环、bigmodel 真实投递、真实结算后恢复、新活动发额度、真实浏览器视觉、WARP 公网网关链路、长期多账号出口风控。

## 八、工作区、发布与运维边界

整理前已存在以下 15 份未提交文档改动，逐文件哈希已保留。额度 execution/tasks 与活动展示 proposal 共 3 份进入 d9379fe；时间方案承接 normalize proposal 进入 8b17502；本次本号资格再承接 remove-pool 的 execution/tasks 进入 c06f5af，原复核记录保留。其余 9 份哈希未变、保持未提交，不覆盖或顺带发布。本地任务勾选可能与 VPS 已提交版本不同。

```text
docs/development/06-dev-guide.md
docs/development/07-roadmap.md
openspec/changes/entitlements-fallback-before-balances/execution.md
openspec/changes/entitlements-fallback-before-balances/tasks.md
openspec/changes/fix-captcha-failure-diagnostics-and-scheduling/tasks.md
openspec/changes/fix-global-build-entitlement-display/proposal.md
openspec/changes/isolate-mihomo-probe-from-production/tasks.md
openspec/changes/normalize-plan-time-and-status-display/proposal.md
openspec/changes/pin-captcha-egress-and-refill-domain/tasks.md
openspec/changes/plugin-hot-reload/proposal.md
openspec/changes/remove-pool-claimable-sharing/execution.md
openspec/changes/remove-pool-claimable-sharing/tasks.md
openspec/changes/report-app-login-success-event/execution.md
openspec/changes/report-app-login-success-event/tasks.md
openspec/changes/unify-activation-fingerprint/proposal.md
```

用户此前已授权线上出口恢复、临时直连、必要真实请求/SSE，以及相关 Git 提交、推送与快进部署；此前默认分支推送曾被自动审批拒绝，用户明确“允许”后正常审批通过，已推送并快进部署，见顶部记录。已有范围内不重复索权。Antigravity 结论仍只指之前分流方案文本。不升级 vendor、不清理旧目录、不归档 change、不修改长期记忆；另 11 份原文档保持未提交。同步继续走 Git，业务服务无需因文档变动重启。

运行数据在 VPS `.env`、`data/accounts.db`、`data/evidence.db`、`data/node_lifecycle.json`、`data/egress_registry.json`、`data/plugin-auth.json`；不入 Git，不向新文档复制凭证、订阅 token 或真实账号标识。`vendor/magpie-plugins` 是真 submodule，本轮 VPS pin `6a3fe9b49e7e638ed79f00d274ad6e1b93a384cd`；`vendor/magpie_host.js` 是人工维护的单文件协议来源，不自动整文件替换。扩大仓库可见范围前另核历史隐私，工作树脱敏不代表历史已脱敏。

`/root/code/ZCode`、`/root/code/magpie`、`/root/code/magpie-plugins` 已在此前诊断中联合核对；未升级，未运行完整 ZCode 桌面 E2E。旧项目、运行时副本、回退目录及诊断报告保留；清理前先核唯一改动、当前用途和回退价值。

开发、部署、迁移见[开发指南](development/06-dev-guide.md)。保留要点：systemd PATH 与登录 shell 不同；复制 venv 后核 shebang；跨目录同步用 Git，避免 PowerShell 递归合并误覆盖。`scripts/mihomo_auto_probe.py` 会发真实请求并发布运行状态，不是只读命令。

## 九、下一步建议

1. 当前额度/展示修复已发布验收，继续等待同一权益真实余额生成后，核验后台自动恢复；两个指定账号 Global Build 于 23:00 生效，现有其他余额不代表该权益已经结算。保持 direct chat / 固定代理 claim；HK 新准入节点需另补普通/SSE，不能将更新器准入当成真实恢复。
2. 有账号具备新活动资格时，验证领取 code 0、额度到账和本地状态刷新；1003 不核销此项。TW 验证码失败另查原因，保留有效性校验。
3. 本号活动资格已完成专项验收；下一项建议推进 `report-app-login-success-event` 的真实新账号登录到奖励闭环，先只读核验可用样本和现有记录，不冒充已验证。标准测试环境、vendor 评估及磁盘事项分别跟踪。

补货错配、分流、活动授予量展示、套餐时间及排序、本号活动资格已发布验收；原 11 项主任务中余 **7 项**开放。生产重启连接排空超时、诊断输出凭证处理、旧 gateway-unavailability 长 requirement strict 警告均保留。旧交接的迁移流水与早期数字可通过 Git `780d671:docs/HANDOFF.md` 追溯，不作为当前状态使用。
