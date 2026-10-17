# purpose-egress-routing Specification

## Purpose

定义账号对话与活动领取的独立出口策略，使运营者能够使用固定同区代理完成领取，并为对话选择直连或现有 WARP 代理。约束实际外发、失败隔离、兼容行为与验收证据，避免同域名不同接口的拒绝被混为同一故障。

## Requirements

### Requirement: Independent purpose configuration
系统 MUST 支持 chat / claim 分别选择 inherit、direct 或 proxy；数据库完整策略优先于环境默认，用途 inherit 继承旧全局模式。显式用途策略优先于全局模式和旧账号代理；chat proxy MUST 指定本地 HTTP 代理，claim proxy MUST 指定同区固定托管槽位。所有运行消费端 MUST 使用启动时加载的生效快照；无效配置 MUST 明确报错，不得隐式改为直连。

#### Scenario: Existing direct deployment
- **WHEN** 原全局模式为 direct，两个用途配置均未设置，且不存在数据库出口覆盖
- **THEN** 对话、领取及原有匿名启动链路保持现有直连行为，不自动启用 WARP 或修改账号大区。

#### Scenario: Claim proxy with direct chat
- **WHEN** chat 显式 direct、claim 显式 proxy，HK 账号有有效 HK 领取绑定
- **THEN** 对话使用无代理连接，领取使用该 HK 绑定，全局 direct 不得覆盖领取代理。

#### Scenario: WARP selected only for chat
- **WHEN** chat 显式 proxy 指向本地 WARP `http://127.0.0.1:40000`
- **THEN** 对话使用 WARP，领取继续使用自身策略；WARP 不可达时不得静默切到 VPS 原生 IP。

#### Scenario: Invalid or incomplete override
- **WHEN** 用途模式不受支持、显式 chat proxy 缺 URL，或领取绑定结构非法
- **THEN** 配置检查明确失败；合法绑定表未包含某账号大区时，仅该账号的领取链路报告缺少绑定，不影响其它账号对话。

#### Scenario: Database override takes precedence
- **WHEN** 已保存的完整策略为 chat direct，环境默认为 chat proxy
- **THEN** 重启加载后 chat 实际使用数据库的 direct 策略，页面标明生效来源为数据库。

### Requirement: Admin configuration management page
系统 MUST 在现有后台设置入口提供配置管理，保留已有设置并增加出口分流区域，支持对话直连/WARP/指定本地代理、领取模式及各大区节点下拉选择。节点身份 MUST 由服务端校验，用户无需手填指纹/代次；部署路径、端口及运行时程序路径 MUST NOT 作为任意环境变量编辑器暴露。

#### Scenario: Configure egress without hand-editing JSON
- **WHEN** 已登录管理员进入配置管理并为 HK / JP / TW 选择领取节点
- **THEN** 页面显示节点名、地区、端口和不可选原因，保存同区固定身份；既有鉴权、刷新、并发及通知配置继续可用。

#### Scenario: WARP preset
- **WHEN** 管理员选择对话 WARP
- **THEN** 页面保存对应的本地代理配置，不要求填写底层模式组合；保存不启动 WARP 或测试上游。

### Requirement: Atomic authenticated configuration persistence
配置管理读写及候选节点接口 MUST 使用现有管理员鉴权。出口策略 MUST 作为完整对象原子持久化；校验、节点换代或数据库写入失败时 MUST 保留原保存值及运行值。旧通用设置端点 MUST NOT 接受或覆盖出口策略键；出口保存不得覆盖其它设置或写 `.env`，配置 API MUST NOT 接受任意 meta 键或返回节点密码、订阅及账号凭证。

#### Scenario: Unauthenticated or invalid write
- **WHEN** 未登录调用配置接口，或管理员提交非法枚举、URL、跨区绑定或未知字段
- **THEN** 请求被拒绝，数据库和生效策略不变，不发任何上游请求。

#### Scenario: Stale node selection
- **WHEN** 表单打开后，所选端口的节点身份/代次改变或进入退役
- **THEN** 保存返回冲突并要求刷新，不能把旧选择自动绑定到同端口的新节点。

#### Scenario: Database failure
- **WHEN** 完整策略校验通过但数据库提交失败
- **THEN** 返回保存失败，不能部分更新任一出口字段、已保存视图或运行策略。

#### Scenario: Legacy settings cannot modify egress policy
- **WHEN** 旧通用设置表单保存，或调用方在旧设置端点额外提交出口策略键
- **THEN** 该键被过滤忽略且不能修改或清空出口对象，原有鉴权、刷新及通知字段保持原契约。

### Requirement: Saved and active policy visibility
页面 MUST 区分已保存目标与当前进程生效策略、来源及待重启状态。待重启状态 MUST 由服务端比较规范化目标策略及来源与生效快照计算，两者完全等价时为 false，不依赖浏览器脏状态。保存和恢复环境默认 MUST 不改变当前出口、不重启服务、不清理在途请求；重启后各消费者 MUST 一致加载，不能把保存或节点存在当成业务可服务。

#### Scenario: Save requires restart
- **WHEN** 管理员把对话 direct 保存为 WARP，但网关尚未重启
- **THEN** 页面刷新后仍显示当前 direct、已保存 WARP、待重启；新旧对话都继续沿用当前 direct 快照。

#### Scenario: Restart loads the saved policy
- **WHEN** 网关重启并成功加载已保存配置
- **THEN** 页面显示新的当前策略和数据库来源、清除待重启状态，实际请求按该快照选路。

#### Scenario: Restore environment defaults
- **WHEN** 管理员恢复环境默认并保存
- **THEN** 只移除数据库出口覆盖，页面提示重启；其它设置及当前路由不变，重启后使用当次启动的环境默认。

#### Scenario: Equivalent target is not pending
- **WHEN** 管理员保存或恢复后的规范化策略及来源与当前生效快照完全相同
- **THEN** 服务端返回 pending_restart=false，页面重载不产生虚假的待重启提示；仅策略相同但来源不同仍需重启加载该来源。

#### Scenario: Pending binding removal cannot allow reclaim
- **WHEN** 固定领取绑定已从保存目标中移除，但当前进程尚未重启
- **THEN** 原生效绑定仍参与退役固定引用计数并禁止端口回收，不能按待生效配置提前释放。

### Requirement: Fixed same-region claim binding
显式代理领取 MUST 绑定账号已有大区内的一个 `(port, node_id, epoch)`，独立于对话活跃端口列表。系统 MUST 校验来源存在、身份匹配、端口大区、退役状态及领取断路器；不得将空槽、已失去来源、正在退役或已换代的槽位用于新请求。单纯对话拒绝 MUST NOT 删除领取准入。

#### Scenario: Chat rejected while claim remains configured
- **WHEN** HK 槽位因对话 3012 处于对话 draining，但领取固定绑定身份一致、来源有效且未进入退役
- **THEN** 允许该账号尝试领取链路，不将槽位改成对话 active，不借此宣称领取成功。

#### Scenario: Missing region or obsolete identity
- **WHEN** 账号未分配大区、无对应绑定，或端口已换成不同 node_id / epoch
- **THEN** 领取在联网前明确失败，不跨区、不换槽、不复用旧 token、不降级直连。

#### Scenario: Retirement preserves all references
- **WHEN** 更新器请求回收被领取绑定引用的槽位
- **THEN** 新领取准入关闭；绑定、客户端、producer 和在途请求仍参与排空计数，旧 SSE / 操作释放前不得误报可回收。

### Requirement: Purpose ownership of account workflows
对话普通请求、SSE、挑战重解和对话探针 MUST 使用 chat 策略。领取、preview、领取相关激活、JWT 套餐额度查询、按账号安装/登录事件、哨兵活动查询 MUST 使用 claim 策略；匿名启动与非 ZCode 的登录及 API Key 额度接口 MUST 保持旧策略。同一用途操作 MUST 固定出口身份直至结束。

#### Scenario: Automatic claim sequence
- **WHEN** 账号经页面、CLI、入池自动领取或哨兵发起服务端领取
- **THEN** 同次领取的 preview、激活、验证码配置实际外呼、求解和 claim POST 使用同一已准入出口及账号身份；领取后额度查询使用 claim 策略。

#### Scenario: Conversation followed by quota refresh
- **WHEN** 对话经 direct 或 WARP 成功后触发 JWT 额度刷新
- **THEN** 对话保持 chat 出口；额度刷新明确进入 claim 策略，不因异步上下文继承而沿用 chat 客户端。

#### Scenario: Claim outage does not exclude conversation
- **WHEN** 账号领取绑定缺失或领取被拒，但其对话策略可服务且账号具备对话资格
- **THEN** 对话调度不得仅凭领取出口故障排除该账号。

### Requirement: Channel uses the admitted chat route
native 与 plugin 通道 MUST 使用同一已准入 chat 出口。插件 MUST NOT 通过全局模式、未确认白名单或捕获异常把代理请求改成直连；需要确认的托管对话代理未确认时 MUST 明确不可用。显式本地 chat 代理可直接尝试，其失败不得触发其它出口。

#### Scenario: Global direct with explicit chat proxy
- **WHEN** 全局 direct、chat 显式代理，插件承接对话
- **THEN** 插件实际请求走指定代理，验证码出生出口与对话出口一致。

#### Scenario: Unconfirmed managed chat proxy
- **WHEN** 继承的托管对话代理未通过已有确认门禁
- **THEN** 请求返回现有出口不可用结果，不能向插件传空代理后获得一次“直连成功”。

### Requirement: Manual claim verification compatibility
显式 claim 用途配置下，系统 MUST 保留人工选择账号/套餐的操作，并使用服务端验证码领取；浏览器来源的 token MUST 在提交上游前明确拒绝，页面 MUST 给出服务端领取操作。claim 为 inherit 时 MUST 保留旧浏览器路径，并不得将其标记为已验证出口亲和。

#### Scenario: Operator chooses a plan under split routing
- **WHEN** 领取用途配置为 proxy 或 direct，用户在页面选择活动并提交
- **THEN** 页面调用现有服务端领取入口，在所选领取出口求解及提交，不启动无法保证同出口的浏览器滑块流程。

#### Scenario: Old external caller posts a browser token
- **WHEN** 显式领取用途配置生效，旧调用方仍提交浏览器 token
- **THEN** 接口返回可识别的兼容性错误并提示使用服务端领取，不发送 claim POST。

### Requirement: Route diagnostics and acceptance boundaries
诊断 MUST 展示用途、配置模式、实际出口种类、本地端口和失败阶段，不输出凭证、验证码或订阅。实际出口、公网普通/SSE、领取业务返回和发额度 MUST 分别验收；1003 MUST NOT 被计作新活动到账，对话探针成功 MUST NOT 被计作领取成功。

#### Scenario: Already claimed account during validation
- **WHEN** 固定代理领取返回 HTTP 200 / code 1003
- **THEN** 只记录领取业务通路已进入“已领取”返回；无可领取活动时，code 0、额度到账及本地状态刷新验收保持未完成。
