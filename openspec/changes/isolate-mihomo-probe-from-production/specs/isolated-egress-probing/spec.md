# Spec Delta

## Purpose

本规范定义周期出口探测与生产代理之间的运行隔离，确保节点检测不会移除业务监听、改变已发布路由或重启生产实例，并规定探测资源的回收、重叠执行与整轮基础设施失败的可观察结果。首次维护转换与日常提供器发布分别验收，探测权限不得成为生产停启或整份加载权限。

## ADDED Requirements

### Requirement: Probe and Production Isolation
周期探测 MUST 使用独立于生产的运行实例、配置、数据目录、控制入口与回环监听。节点切换和探测请求 MUST 仅使用探测实例；探测阶段 MUST NOT 写生产配置或注册表、停止或重启生产实例，且 MUST 保留生产监听及在途业务连接。

#### Scenario: Full Cycle Runs Alongside Production
- **WHEN** 生产实例正在处理业务且周期任务执行全部候选节点探测
- **THEN** 生产 PID、配置和业务监听身份保持不变，旧注册表仍可读取
- **AND** 业务请求可以继续建立连接，既有 SSE 不因探测而断开

#### Scenario: Probe Resource Conflicts
- **WHEN** 探测监听端口被占用或临时控制入口无法建立
- **THEN** 任务仅可有限重试或失败，不得接管其它进程端口或退回生产控制入口
- **AND** 生产配置、监听与发布快照保持原状

### Requirement: Managed Probe Lifetime
探测 MUST 在确认实例就绪后开始，有有限启动和整轮执行期限。正常结束、失败、取消及受管终止 MUST 回收本轮子进程、连接、临时监听和临时目录；恢复生产所需副本 MUST 保留至发布或恢复确认完成。

#### Scenario: Child Exits Before Readiness
- **WHEN** 探测进程在就绪前退出或控制入口未在期限内响应
- **THEN** 整轮失败并回收本轮临时资源，不执行节点探测或生产发布

#### Scenario: Cancellation During Probing
- **WHEN** 正在探测的任务被取消或超过整轮期限
- **THEN** 临时子进程在有限清理时间内被终止并回收，不遗留探测监听或后台进程
- **AND** 不触发生产重启、不发布本轮结果

### Requirement: Single Writer for Complete Updates
从读取旧状态到发布、恢复、退役确认及本轮清理完成，系统 MUST 只允许一个完整更新持有发布资格。timer 与手工执行 MUST 使用同一互斥边界；未获资格的执行 MUST 在外呼及状态变更前退出。排空等待 MUST 有本轮预算，超出预算保留待退役状态而不是强制结束业务。

#### Scenario: Manual Run Overlaps Timer
- **WHEN** timer 已执行本轮更新而另一手工执行同时启动
- **THEN** 第二次执行记录已在运行并退出，不外呼、不创建竞争性探测或发布配置
- **AND** 首轮的发布与恢复资源不被第二次执行清理

### Requirement: Infrastructure Failure Does Not Become Node Health
无候选、无可用于现有探测流程的账号、探测设施故障、取消或未完成扫描 MUST 标为整轮未提交，不得被转换成全部节点失败并发布空路由。没有可发布 active 出口时 MUST 保留最后完整快照，并明确该快照未经本轮重新验证。日志及审查材料 MUST 仅包含必要白名单诊断，不包含订阅、账号凭据、节点秘密或原始验证码参数。

#### Scenario: Controller Fails Mid Scan
- **WHEN** 探测控制入口在扫描中途不可用，无法可靠完成后续节点切换
- **THEN** 整轮失败，不提交部分生命周期或新的活跃注册表
- **AND** 旧生产快照保持可见，诊断区分设施失败与单节点请求失败

#### Scenario: No Publishable Egress
- **WHEN** 本轮没有候选、没有探测前提或得到零个可发布 active 出口
- **THEN** 不发布空注册表，记录未提交及相应原因，不修改账号状态或额度

#### Scenario: All Subscription Fetches Fail
- **WHEN** 所有配置来源获取失败，没有可信的新完整快照
- **THEN** 不把旧节点转换成删除或全体过期，生产提供器及最后完整路由保持不变
- **AND** 来源快照保留，注明本轮未重新验证，不以缓存存在宣称外部节点健康

### Requirement: Publication Requires Migrated Production Structure
日常任务 MUST 先确认生产已完成固定槽与提供器结构迁移、消费者支持当前准入/排空协议且基础配置及运行身份符合基线；不满足时 MUST 无副作用拒绝发布，不自动创建生产监听、加载整份配置或重启生产。新节点只能使用安全同区槽，容量不足不影响已发布旧身份。

#### Scenario: Timer Runs Before Maintenance Migration
- **WHEN** 生产仍是旧直绑结构或消费者不支持当前协议
- **THEN** 本轮不执行生产发布，报告迁移前提不满足，不部署第一版或 fallback restart

#### Scenario: Drain Confirmation Exceeds Cycle Budget
- **WHEN** 新路由已提交但旧操作在本轮预算内没有排空
- **THEN** 保留旧物理节点、待退役状态及具体原因，正常回收本轮探测资源
- **AND** 不将路由已提交记录为节点已回收，不强杀业务或抢占旧槽
