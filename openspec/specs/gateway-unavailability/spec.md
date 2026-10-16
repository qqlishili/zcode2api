# gateway-unavailability Specification

## Purpose
为三协议网关在开始输出前的最终账号不可用响应提供结构化原因、证据来源、重试建议及请求关联，使调用方与后台无需解析中文文案即可核对失败，避免把额度、并发、未知和未完成尝试混为一谈。

## Requirements

### Requirement: 最终不可用响应保持兼容并可关联

网关 MUST 对 Messages、Chat Completions、Responses 的共享最终不可用出口保持 HTTP 503 与 `error.type=no_available_account`，在 error 中增加 `code`、`causes`、`retryable`、`retry_after`、`request_id`。causes 的元素仅含 `code` 与 `origin`，按二者去重及字典序排序；message 仅用于展示。响应 MUST 携带与 request_id 和监控 req_id 相同的 `X-Request-Id`。此契约不改变其它 HTTP 错误或开始输出后的 SSE 终态。

#### Scenario: 三协议及流式入参仍在输出前失败
- **WHEN** 三个接口分别以 stream=true/false 请求且调度最终不可用
- **THEN** 均返回同一 JSON 字段语义与 503/type，关联 ID 一致，旧客户端可以忽略新增字段

#### Scenario: 展示文案变化
- **WHEN** message 改写或包含“并发”“额度”字样
- **THEN** 原因与重试字段保持由结构化证据决定，不解析 message

### Requirement: 原因只来自实际判定

网关 MUST 区分以下原子码：`pool_empty`、`account_disabled`、`credential_invalid`、`quota_exhausted`、`model_quota_exhausted`、`account_cooling`、`local_concurrency_limit`、`upstream_concurrency_limit`、`upstream_rate_limited`、`upstream_429_unknown`、`upstream_transport_error`、`upstream_server_error`、`captcha_retry_exhausted`、`account_risk_blocked`。origin MUST 为 `account_state`、`local_scheduler`、`upstream`、`transport` 之一。仅有当前 provider 无配置账号才命中 pool_empty；缺失或未知额度窗口 MUST NOT 记成零额度。历史禁用状态 MUST NOT 冒作本次风控证据，历史 last_error MUST NOT 用于反推原因。无法解释的状态 MUST 保留未知。

#### Scenario: 无池与状态排除
- **WHEN** 当前 provider 无配置账号，或账号分别因 enabled=false、INVALID、DISABLED、EXHAUSTED 被实际排除
- **THEN** 分别记录 pool_empty、account_disabled、credential_invalid、account_disabled、quota_exhausted，不细猜历史风控或官方全部模型余额

#### Scenario: 模型窗口与冷却反例
- **WHEN** 同名模型窗口 remaining<=0，或窗口缺失/remaining=null，或 COOLING 的截止仍在未来/已到期/缺失
- **THEN** 仅实际零余量排除命中 model_quota_exhausted；缺失/null窗口保持可用语义；未来有效截止命中 account_cooling，缺失截止保留未知，到期不报告仍冷却

#### Scenario: 本地与上游并发及限流
- **WHEN** 本地槽位满、上游明确并发业务码、明确普通限流码或未知429分别导致换号并最终失败
- **THEN** 分别记录 local_concurrency_limit、upstream_concurrency_limit、upstream_rate_limited、upstream_429_unknown，不互相冒用

#### Scenario: 换号错误来源
- **WHEN** 连接/预读取失败、上游5xx内部重试耗尽、验证码挑战连续失败、明确风控或鉴权失败进入换号路径
- **THEN** 分别保留 transport、服务器、验证码、风控、鉴权原因及正确 origin；独立验证码获取500或其它直接错误不改成503

### Requirement: 汇总区分原因与停止覆盖

网关 MUST 区分后台 stop_reason=`no_candidate` 与 `attempt_limit`。达到尝试上限时公开 code MUST 为 `dispatch_attempt_limit`，不得宣称全池耗尽。否则，存在未分类或终止覆盖不足时 MUST 为 `unknown_unavailability`；覆盖足够且只有一个不同原子码时使用该码，多个不同原子码时使用 `mixed_unavailability`。相同码的多个来源不构成混合。causes MUST 保留有证据且仍解释本次终止的原因，已恢复的阻塞只作为后台历史观测，不纳入公开终因。

#### Scenario: 混合与未知
- **WHEN** 账号有不同实际障碍，或存在未知/请求期间状态变化导致覆盖不足
- **THEN** 分别返回 mixed_unavailability 或 unknown_unavailability，保留已知 causes，不固定挑选一个病因

#### Scenario: 尝试上限与未尝试账号
- **WHEN** 达到真实上游尝试次数上限而仍有未尝试账号
- **THEN** 返回 dispatch_attempt_limit，后台标记覆盖缺口，retryable=null，不把未尝试账号记成额度或认证失败

#### Scenario: 换号后成功
- **WHEN** 较早账号失败但后续账号成功
- **THEN** 返回既有成功结果，监控为成功，不残留最终不可用字段

### Requirement: 重试建议有候选与时间依据

retryable MUST 为 true/false/null：至少存在一个有证据的临时恢复路径且该候选没有同时存在的硬障碍时为 true；覆盖完整且全部障碍需要配置、启用、凭证或额度变化时为 false；其它情形为 null。尝试上限一律 null。未知429或验证码耗尽仅凭失败类别不能证明可恢复；未知429有合法 Retry-After 且无硬障碍时可以建议有界重试。retry_after MUST 仅在 true 且有可靠时间依据时为非负整数秒，并在同一响应设置一致的 `Retry-After`；否则为 null 且不设置该头。并发无释放时点 MUST NOT 编造秒数。

#### Scenario: 冷却及硬障碍
- **WHEN** 账号未来冷却到期且模型可用，或该账号同时存在模型零余量
- **THEN** 前者可建议按截止重试，后者不得仅凭冷却建议重试

#### Scenario: 上游等待要求
- **WHEN** 上游给出有效秒数或 HTTP-date Retry-After，且内部等待已消耗部分时间
- **THEN** 对外剩余秒数向上取整，不早于上游要求，不采用内部等待封顶值；缺失/非法值不伪造头

#### Scenario: 多候选时间聚合
- **WHEN** 临时可恢复候选具有多个适用等待条件或某候选缺少必要时间依据
- **THEN** 每候选取等待截止最大值，再取已证实可恢复候选最早值；存在可能更早恢复但时点未知的候选时 retry_after=null

### Requirement: 监控与公开结果一致并保护隐私

监控 MUST 复用请求内存记录，在最终503中保存与公开响应同源的 `error_code`、`error_causes`、`retryable`、`retry_after`、`stop_reason`，保留旧 error 文案。新增后台证据 MUST 有界，只保存匿名候选关联、阶段、判定时间、原始HTTP码与已取得业务码；未取得业务码保持 null。公开错误 MUST NOT 含账号标识、名称、凭据、额度数值、请求内容、地址或原始上游响应。诊断 MUST NOT 额外请求上游、刷新/恢复账号或新增数据库写入。

#### Scenario: 同源与脱敏
- **WHEN** 发生最终503并查询监控
- **THEN** code/causes/重试字段与响应一致，关联 ID 相同，公开无私密字段，后台新增证据无原始正文或凭据

#### Scenario: 生命周期回归
- **WHEN** 请求成功、取消、等待后重占槽失败或流关闭
- **THEN** 保持现有终态及槽位释放语义，不新增账号状态转换、上游请求或错误终态污染
