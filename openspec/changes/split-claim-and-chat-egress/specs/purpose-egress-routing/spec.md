## Purpose

定义账号对话与活动领取的独立出口策略，使运营者能够使用固定同区代理完成领取，并为对话选择直连或现有 WARP 代理。约束实际外发、失败隔离、兼容行为与验收证据，避免同域名不同接口的拒绝被混为同一故障。

## ADDED Requirements

### Requirement: Independent purpose configuration
系统 MUST 支持 chat / claim 用途分别选择 inherit、direct 或 proxy，默认 inherit；inherit 继承旧全局模式，显式用途配置优先于全局模式和旧账号代理。显式 chat proxy MUST 指定本地 HTTP 代理，显式 claim proxy MUST 指定按账号大区配置的固定托管槽位。无效配置 MUST 明确报错，不得隐式改为直连。

#### Scenario: Existing direct deployment
- **WHEN** 原全局模式为 direct，两个用途配置均未设置
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
