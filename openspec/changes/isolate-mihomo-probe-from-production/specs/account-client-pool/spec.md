# Spec Delta

## MODIFIED Requirements

### Requirement: Consistent proxy routing
The system SHALL bind each account to a stable eligible proxy endpoint via consistent hashing when global proxy list `ZCODE_PROXIES` is configured, unless an explicit `account.proxy` is specified. 已绑定大区的账号 MUST 沿用该大区边界；托管代理资格及身份以完整已提交注册表为准，静态全局列表和缓存命中不得绕过退役栅栏。仍有效的账号绑定保持稳定；失效缓存脱离新业务入口，新操作使用同区替代，旧操作继续使用已准入的实际身份。显式固定引用不得被自动重写，相关节点回收等待该引用被明确释放。

#### Scenario: Explicit account proxy takes priority
- **WHEN** an account has a non-empty `proxy` field configured
- **THEN** the client instance for this account uses the account-level proxy
- **AND** 若该代理属于托管槽，则纳入引用和准入核验；固定引用未释放前不能自动删除或复用该槽

#### Scenario: Consistent hashing over global proxy list
- **WHEN** global `ZCODE_PROXIES` contains multiple proxy URLs and the account has no explicit proxy
- **THEN** the system consistently maps the account to the same eligible proxy URL based on hash of account ID
- **AND** 已有绑定仍有效时不因其它槽新增而重建客户端，失效时才使用当前提交版本重新选择

#### Scenario: Cached Client Becomes Ineligible
- **WHEN** 新版本停止旧节点的新操作准入，但账号缓存仍持有其客户端
- **THEN** 新操作不能直接返回并使用旧缓存，转用同区替代
- **AND** 旧客户端在已有操作结束前不被关闭，不能降级直连或跨区换出口

#### Scenario: Stale Global List References a Retired Slot
- **WHEN** 运行中的静态全局代理列表仍包含已退役或已复用槽的旧引用
- **THEN** 以当前提交的槽身份和资格栅栏阻止旧引用联网，不把静态列表作为重新准入依据

### Requirement: Idle client cleanup
The system SHALL track client activity and automatically close and evict client instances that have remained idle beyond the configured TTL (default 300 seconds), but MUST only physically close clients after all actual operation references have been released. 空闲时间、HTTP 成功或从可选缓存移除 MUST NOT 代替排空；账号关闭及退役关闭也必须保护仍在使用的客户端，最终回收幂等。

#### Scenario: Evict client after idle timeout
- **WHEN** a client instance has not been accessed for more than the idle timeout and has no actual operation references
- **THEN** the pool closes the client and frees resources on the next prune cycle

#### Scenario: SSE Outlives Idle Timeout
- **WHEN** 长 SSE 或验证码相关操作持续时间超过客户端空闲 TTL
- **THEN** 不关闭仍被引用的客户端，直到实际操作结束及连接清理完成才回收

#### Scenario: Account Client Is Removed While In Use
- **WHEN** 账号客户端被撤出缓存或进入退役，但仍有已准入操作
- **THEN** 停止新准入并保留该实例供旧操作完成，引用归零后幂等关闭

## ADDED Requirements

### Requirement: Atomic Operation Admission and Identity
系统 MUST 在取得可联网客户端时同时核验当前提交版本、节点/槽身份并增加实际操作引用，不能将取得旧句柄与准入分为无栅栏的两步。每次已准入操作 MUST 固定实际出口直到结束；已关闭准入的旧裸句柄不能产生新操作。版本更新与准入竞争必须产生完整的旧已准入或新已准入结果，不能遗漏引用。

#### Scenario: Registry Changes During Client Acquisition
- **WHEN** 出口版本切换与客户端取得并发
- **THEN** 操作要么已被计入旧身份引用并受旧操作保护，要么使用新身份准入
- **AND** 不出现“排空已确认后旧句柄又开始联网”的窗口

#### Scenario: Captcha and Upstream Share One Operation Identity
- **WHEN** 已准入业务在验证码等待中遇到出口退役
- **THEN** 验证码与后续上游仍使用该操作固定的旧出口，引用覆盖整个等待和请求
- **AND** 如需换出口必须重新准入并重新取得对应验证码，不能将旧 token 搬到新身份

#### Scenario: Admitted Operation Starts Captcha After Retirement
- **WHEN** 操作已准入旧身份，但其需要的验证码现解在关闭新准入之后才开始
- **THEN** 求解沿用该父操作的有效身份并持有独立引用，使旧操作能够完成
- **AND** 没有有效父引用的新补货或独立求解仍被拒绝，不能借此重新开放旧业务准入

### Requirement: Exact Operation Release
引用 MUST 覆盖选出口、排队/验证码、实际外呼、正文或 SSE 消费及最终关闭。正常、异常、取消、重试结束与本地失败 MUST 配对释放且仅释放一次；HTTP 响应头成功、断路器结算和账号并发槽释放 MUST NOT 提前结算出口引用。仍运行的独立后台 producer MUST 持有自身引用。

#### Scenario: HTTP Success Arrives Before Body Completion
- **WHEN** 已收到 HTTP 200 但正文或 SSE 尚未消费及关闭
- **THEN** 继续持有出口引用，不能关闭客户端或报告排空

#### Scenario: Operation Cancels or Fails
- **WHEN** 已准入操作被取消、遇到本地错误或外呼异常
- **THEN** 在实际连接和子任务清理后幂等释放，不泄漏或提前减少引用

#### Scenario: Singleflight Producer Outlives One Waiter
- **WHEN** 共享验证码求解仍在执行而某个等待者取消
- **THEN** 仅释放该等待者持有的操作引用，producer 自身引用保持到实际求解和子进程回收结束

### Requirement: Identity Scoped Cache and Result Isolation
客户端、验证码 token/producer、水库补货及断路器结果 MUST 关联节点与槽身份代次，不能只以可复用代理 URL 判断身份。新身份不得继承旧 token、客户端、断路器结果或排空确认；其它节点新增与重复路由发布不得使有效绑定产生无意义重建。已撤销成员不得因旧请求网络成功重新获得成员资格。

#### Scenario: Port Is Reused by a New Node
- **WHEN** 同一大区端口在完整排空后分配给新身份
- **THEN** 使用新代次和独立缓存状态，拒绝旧身份 token、结果、引用和确认

#### Scenario: Queued Task Holds an Old Proxy URL
- **WHEN** 延后执行的任务仍保存旧槽 URL，而该槽已安全复用为新身份
- **THEN** 核验任务入队时的节点身份/代次并拒绝旧引用，不能仅凭 URL 把它重新标记为新身份
- **AND** 无法提供代次的显式固定引用未解除前阻止复用

#### Scenario: Old Request Succeeds After Retirement
- **WHEN** 已退役身份的最后旧操作成功完成
- **THEN** 只结算该旧操作结果，不能重新打开旧准入或污染新身份的断路器
