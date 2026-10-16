# Spec Delta

## MODIFIED Requirements

### Requirement: Zero Downtime Hot Reload
日常出口更新及恢复 MUST 仅刷新已配置的节点提供器，保留生产基础配置、进程与固定业务监听；MUST NOT 整份加载基础配置、自动 stop/restart、删除生产连接或强制结束业务。首次结构迁移单独采用已确认的一次维护暂停，MUST NOT 将该暂停隐含排除后宣称首次零中断。连续性 MUST 同时覆盖新请求与既有正常 SSE，由真实行为证据证明。

#### Scenario: Seamless API Config Update
- **WHEN** 新的干净出口集合生成并触发日常提供器刷新
- **THEN** 正在输出流式出词（SSE）的客户端请求不得被中断或产生 TCP RST 错误
- **AND** 新请求继续成功建立连接，生产基础配置、PID 与固定监听保持不变

#### Scenario: Controller Rejects or Times Out
- **WHEN** 提供器刷新被拒绝、超时或响应丢失
- **THEN** 系统核对实际已加载身份并按提交阶段恢复，不自动重启或整份加载
- **AND** 未确认结果时返回失败、保留证据，不发布未验证路由或记录更新成功

### Requirement: Incremental Node Merging across Subscriptions
系统 MUST 按来源保存最后有效完整节点快照，以规范化完整连接定义合并成员。显示名、顺序及重复来源 MUST NOT 改变节点身份；认证、TLS、传输及其它影响连接的定义改变 MUST 作为新身份处理。成功来源差分仅移除该来源的引用，失败来源保留旧快照；所有来源均撤销后才进入待退役，健康失败和观察超时 MUST NOT 充作成员删除证据。

#### Scenario: Airport Rotation without Port Drop
- **WHEN** 用户更换订阅且新订阅暂无台湾节点
- **THEN** 系统继续保留存量仍可服务的台湾节点及端口作为明确标记的临时后备
- **AND** 后续有效更新重评同区替代，不把后备伪装为仍在订阅内的普通成员

#### Scenario: One Source Removes a Shared Node
- **WHEN** 来源 A 成功快照中删除节点，而来源 B 仍引用该节点或本轮获取失败
- **THEN** 仅移除 A 的引用，保留 B 的最后有效快照与节点，不触发物理删除

#### Scenario: Subscription Failure or Ambiguous Empty Response
- **WHEN** 来源超时、非成功响应、解析失败、结构不完整或空响应缺少可信完整性证据
- **THEN** 保留该来源旧快照和成员引用，记录未验证原因，不将结果转换为删除
- **AND** 全部来源失败时生产提供器及最后完整路由不被本轮更新

#### Scenario: Names Change but Connection Definition Does Not
- **WHEN** 完整节点连接定义不变，仅显示名、排列或来源重复情况改变
- **THEN** 保留节点身份和原槽；节点集合与路由不变时不刷新提供器或生成新路由版本

#### Scenario: Connection Definition Changes
- **WHEN** 同显示名节点的认证、TLS 或传输字段改变
- **THEN** 新身份先验证并准备同区安全槽，旧身份按退役契约排空，不原地替换仍被引用的旧端口

#### Scenario: Configured Source Is Explicitly Removed
- **WHEN** 用户从配置中明确移除一个来源
- **THEN** 撤销该来源成员引用；仅重排配置不撤销引用，多来源共有节点仍保留
- **AND** 无替代节点仍受临时后备与安全回收条件约束

### Requirement: Slot Reservation vs Active Routing Decoupling
系统 MUST 将物理端口槽占用、来源成员、健康状态与新操作准入分开。stale 或未验证节点保留原槽身份但 MUST NOT 承接新业务；待退役节点在替代已就绪后停止新准入，仅完成已准入操作。无同区替代时可服务旧节点 MUST 作为明确标记的临时后备保留，后续有效轮次重评；该例外不允许跨区或直连。

#### Scenario: Stale Node Traffic Blackhole Prevention
- **WHEN** 已占槽节点变为 stale
- **THEN** 保留物理槽以防身份抖动，立即从新业务准入中剔除
- **AND** 观察超时不直接使槽可复用

#### Scenario: Retiring Node Has No Same Region Replacement
- **WHEN** 全部来源已撤销某节点但同区无可服务替代
- **THEN** 若旧节点仍可服务则明确标记并保留临时后备，暂不回收
- **AND** 若旧节点也失效则报告地域离线并沿用账号避让，不把账号迁往其它大区或降级直连

## ADDED Requirements

### Requirement: One Time Maintenance Migration
首次将直绑监听转换为固定槽和提供器结构 MUST 使用用户已确认的一次维护暂停，长期保持单生产实例。维护 MUST 先核对在途操作、后台任务和共享消费者，保存权限完整的私有回退副本，并统一部署消费者协议与代理结构。MUST 记录实际暂停范围及恢复验证；日常脚本不得继承生产停启权限。

#### Scenario: Migration While Project Is Not Used
- **WHEN** 项目暂时不被业务使用并准备首次结构迁移
- **THEN** 仍确认真实消费者、后台任务和在途连接后才维护，不能把“不用”作为已排空证据
- **AND** 恢复使用前验证完整槽身份、消费者协议、短请求和正常非空 SSE

#### Scenario: Maintenance Migration Fails
- **WHEN** 维护部署、结构加载或恢复验证失败
- **THEN** 保持维护与故障 timer 暂停，按私有备份回退或修复
- **AND** 不将维护暂停混作日常连续性验收通过

### Requirement: Fixed Listener Slots and Safe Empty Slots
生产业务监听 MUST 从既有大区槽配置建立并保持固定；每个槽只能指向一个唯一物理节点身份或 reject 占位，空槽 MUST 拒绝连接，MUST NOT 回退直连或其它槽。未占用或完整排空后已确认回收的同区槽才能接纳新身份；端口冲突或同区容量耗尽 MUST 拒绝不安全追加。

#### Scenario: New Node Enters an Empty Slot
- **WHEN** 新节点已通过验证且同区有安全空槽
- **THEN** 提供器刷新只填该槽，已有占用槽、基础配置及监听身份不变

#### Scenario: Empty Slot Is Called
- **WHEN** 客户端访问未分配或已回收的固定槽
- **THEN** 连接被明确拒绝，不能直连或走其它节点

#### Scenario: Preserved Port Tier Is Exhausted
- **WHEN** 同区所有槽已分配或仍在排空
- **THEN** 拒绝无法安全追加的节点并记录容量原因，不抢占旧身份或跨区借槽

### Requirement: Verified Egress Snapshot Publication
提供器、来源/节点生命周期、扫描结果、环境代理项和注册表 MUST 属于可核验发布。新增替代先在完整候选中就绪，所有旧可见身份保留，关联状态先准备，注册表最后原子提交；保留已有读取格式并提供可核验版本与槽身份。提供器文件写入即可能生效，MUST 在写入前完成候选校验及恢复记录。基础配置只作不变基线，外部冲突 MUST 停止。

#### Scenario: Successful Registry Cutover
- **WHEN** 新替代与全部保留身份已核验，关联状态已准备
- **THEN** 注册表最后原子切换为完整新版本，仅新准入使用该版本允许的出口
- **AND** 旧节点物理配置保留至旧操作排空，路由提交与物理回收分别记录

#### Scenario: Watcher Applies Before Explicit Refresh
- **WHEN** 原子替换提供器后自动 watcher 先于显式刷新生效
- **THEN** 实际载入仍是已预先验证的完整安全候选，旧路由及身份保持可用
- **AND** 不依靠显式刷新作为唯一生效起点

#### Scenario: Validation or File Publication Fails
- **WHEN** 校验、身份核验或关联文件替换失败
- **THEN** 不发布未验证路由，按可见提交点保留或恢复最后完整版本
- **AND** 基础配置和无关内容不被覆盖，不以部分写入或 HTTP 成功冒充完成

### Requirement: Consumer Fenced Node Retirement
所有来源撤销且替代可服务后，系统 MUST 停止旧身份的新业务准入，将新业务及失效缓存客户端迁移到同区替代，保持每个已准入操作出口一致。实际操作、验证码 producer、未分配缓存及所有托管消费者引用排空，并核验实际节点连接为零后，才能物理删除、将槽设为 reject；监听继续存在。固定显式引用、未知消费者、缺失或过期确认 MUST 阻止相关回收。

#### Scenario: Old SSE Drains While New Requests Migrate
- **WHEN** 旧节点有长 SSE 和验证码相关操作，新版本已有同区替代
- **THEN** 新业务成功使用替代，旧操作保持原出口并正常完成
- **AND** 仅在所有引用及实际连接排空后删除旧节点、核验 reject，不关闭在途客户端

#### Scenario: Producer Survives a Waiter Cancellation
- **WHEN** 旧出口的一个验证码等待者取消，但 producer 仍在运行
- **THEN** 保留 producer 自身引用及旧节点，不能把等待者释放当作全出口排空

#### Scenario: Fixed Reference or Unknown Consumer Remains
- **WHEN** 显式托管端口引用、旧协议消费者或未知消费者尚未确认关闭准入
- **THEN** 保留旧身份并记录等待原因，即使当前连接数为零也不得回收

#### Scenario: Safe Same Region Slot Reuse
- **WHEN** 旧准入关闭、全部当前消费者排空、reject 已生效且持久回收记录已核验
- **THEN** 后续发布可把该槽分配给新验证的同区身份和新代次
- **AND** 旧缓存、token、断路器和确认不能继承到新身份或重新启动旧操作

### Requirement: Current Process Bound Drain Confirmation
退役确认 MUST 绑定本次请求、当前发布版本、节点/槽身份、消费者协议及真实进程启动身份。MUST 覆盖实际运行的全部托管消费者，并在删除前再次核对集合、身份、准入关闭、零引用和实际连接；没有前台请求时也能推进确认。旧确认、确认丢失、进程/worker 变化或无法归属连接 MUST 等待重新确认，不能推断零引用。

#### Scenario: Gateway Restarts After Confirmation
- **WHEN** 已收到排空确认后网关进程重启或 worker 集合改变
- **THEN** 旧确认失效，新进程先遵守当前版本准入再启动后台任务，回收等待当前确认

#### Scenario: Stale Confirmation Is Replayed
- **WHEN** 确认与当前请求、版本或槽身份不匹配
- **THEN** 拒绝把它作为物理删除或槽复用依据

#### Scenario: No Foreground Traffic
- **WHEN** 项目没有前台请求但已有待退役节点
- **THEN** 应用仍停止旧补货、排空缓存并输出当前确认，不需要以新业务请求触发

### Requirement: Recover Interrupted Egress Publication
系统 MUST 在下一轮探测前识别并处理未完成发布。注册表提交前仅撤销未提交新增、恢复旧完整快照并刷新提供器；注册表提交后 MUST 向前核验新路由，不重新打开已关闭的旧准入。退役清理是独立阶段，任何未知身份、外部修改或不确定结果 MUST 保留记录并停止相关操作，不整份重载或自动重启。

#### Scenario: Process Stops After Provider Addition
- **WHEN** 替代已加入提供器但注册表尚未提交时更新器退出
- **THEN** 旧可见路由与身份持续可用，下一轮先恢复旧快照并撤销未提交新增

#### Scenario: Process Stops After Registry Commit
- **WHEN** 新注册表已提交但退役清理或收尾未完成
- **THEN** 核验新版本并向前收尾，重新请求当前排空，不回滚旧准入

#### Scenario: Process Stops After Physical Removal
- **WHEN** 旧节点已移除并变 reject，但回收记录尚未完成
- **THEN** 按实际提供器与槽身份核验清理状态，无法确认时冻结槽
- **AND** 新进程/版本须重新确认，不能直接依据旧零引用记录复用

#### Scenario: Recovery Finds Conflicting State
- **WHEN** 任一目标不属于待提交记录的旧/新版本，或运行身份无法确认
- **THEN** 停止、返回明确失败并保留记录，不覆盖外部修改或继续发布
