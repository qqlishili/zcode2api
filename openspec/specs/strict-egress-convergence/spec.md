# strict-egress-convergence Specification

## Purpose

定义单次用途操作的出口一致性：领取及相关激活、安装、额度查询使用 claim，普通/SSE 对话使用 chat。验证码与消费保持实际出口及代次一致；显式 direct 合法，代理失败不得静默直连。

## Requirements

### Requirement: REQ-CONV-01 日活与激活上报必须复用账号大区专属客户端
账号活动领取相关日活、激活及登录事件 MUST 使用 claim 用途的已准入客户端。显式 claim proxy 使用账号同区固定绑定；有效 direct 使用无代理客户端。同次领取中不得自行重选出口，账号设备/登录身份不因用途变化而改变。

#### Scenario: 账号活动领取前置日活
- **WHEN** 执行账号领取前置激活上报
- **THEN** 复用同次 claim 操作的客户端及出口；若独立执行则按 claim 策略取得账号客户端。
- **AND** 显式代理策略下不得创建无代理连接。

#### Scenario: 单元测试与向后兼容
- **WHEN** 无账号的底层事件发送在既有匿名或隔离测试场景未传客户端
- **THEN** 保留默认超时的兼容行为；生产账号链路必须显式传入已准入客户端，不得靠该分支绕过用途策略。

### Requirement: REQ-CONV-02 设备初始化安装序与配置拉取必须绑定代理出口
账号安装序及其配置、激活/登录事件 MUST 使用 claim 策略的同一客户端；无账号的全局启动安装序 MUST 保留全局模式。显式 direct 是合法出口选择，不得仅因没有代理而中止启动；代理策略仍不得静默裸连。

#### Scenario: 账号安装序
- **WHEN** 执行账号初始化安装序
- **THEN** 配置与事件全程透传 claim 操作的客户端，保留原设备身份和 installed_at 守卫。

#### Scenario: 全局启动安装序
- **WHEN** 执行无账号的全局启动安装序
- **THEN** 使用旧全局策略；direct 明确无代理，proxy 依既有健康代理/无托管配置兼容规则执行，不套用任意账号的领取大区。

### Requirement: REQ-CONV-03 验证码求解器必须具备真实 undici 代理能力
验证码求解 MUST 使用消费操作已准入的实际出口。保留现有 undici `^6.21.0` 依赖及真实代理能力；代理依赖或连接失败 MUST 明确报错，不得静默退化直连。有效 direct MUST 清除继承的代理环境，不能反向套入领取代理。

#### Scenario: 依赖与注入生效
- **WHEN** 服务端为代理操作启动 Node 求解
- **THEN** undici 可加载、ProxyAgent 生效，HTTP_PROXY / HTTPS_PROXY 与该操作代理一致。
- **AND** MODULE_NOT_FOUND 或代理失败不允许裸连重试。

#### Scenario: 底层 syncFetch 代理安全
- **WHEN** 求解器执行同步配置/挑战请求
- **THEN** 必须沿用该 producer 的有效代理；有效 direct 才允许无代理外发。

### Requirement: REQ-CONV-04 验证码 Token 大区亲和性隔离
服务端验证码 MUST 与消费操作的实际出口及托管槽位代次匹配。账号大区不能覆盖显式 direct / 本地 chat 代理；不同出口或旧代次 token MUST NOT 混用。浏览器 token 的兼容范围以用途配置规范为准，不冒充服务端亲和保证。

#### Scenario: 同大区命中
- **WHEN** TW 领取操作固定使用 `http://127.0.0.1:21200` 且其有效代次水库有 token
- **THEN** 该领取可命中此水库，不重新哈希到其它同区端口。

#### Scenario: 跨大区隔离与同步现解
- **WHEN** TW 领取使用 `21200`，仅 HK `21100` 或直连水库有 token
- **THEN** 严禁取用其它出口 token，必须通过该次 `21200` 操作按需现解。

#### Scenario: Same account uses direct chat
- **WHEN** 同一 TW 账号的对话策略明确 direct
- **THEN** 对话只取无代理 token，不得因账号大区改用 `21200`，领取水库不被清空。

### Requirement: REQ-CONV-05 大区离线熔断栅栏
代理所需出口不可用时系统 MUST 明确失败且禁止静默直连。区域代理离线门禁 MUST 按用途执行：领取绑定异常不阻塞显式 direct / 本地共享代理的对话；对话拒绝不得直接封禁领取。显式 direct 是主动配置，不属于熔断降级。

#### Scenario: 离线熔断
- **WHEN** TW 账号的有效用途要求代理，但该用途没有有效出口或固定身份已失效
- **THEN** 返回出口不可用，不创建裸机房客户端或跨区客户端。

#### Scenario: 调度避让
- **WHEN** 网关捕获 chat 用途的出口不可用
- **THEN** 仅按 chat 策略换候选账号；不得把 claim 故障当作 chat 的地区离线。

### Requirement: REQ-CONV-06 业务消费端透传实参
服务端验证码消费 MUST 透传账号身份和已准入用途操作，或透传可唯一还原该操作的实际出口/代次。存在操作时 MUST 以其出口为权威，不能因为有效直连的代理值为空而重新执行账号代理解析。

#### Scenario: claim 领取
- **WHEN** 自动或人工选套餐触发服务端领取
- **THEN** 验证码 producer 继承 claim 操作，且 claim POST 继续使用同一操作客户端。

#### Scenario: gateway 对话刷新
- **WHEN** 对话需要初次验证码或挑战重解
- **THEN** 验证码继承 chat 操作及其实际 direct / 代理出口，不能使用同账号的 claim 路由。
