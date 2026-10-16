# Spec Delta

## MODIFIED Requirements

### Requirement: Singleflight Empty-Take Consolidation

When a burst of concurrent requests targets an empty proxy reservoir, the system MUST consolidate those concurrent requests into a single in-flight solving operation, broadcasting the resulting token to the active requests while banking secondary tokens into the queue. A waiting request's cancellation or deadline expiry MUST NOT cancel the solving operation while other foreground or refill consumers still require it. A result delivered to foreground consumers MUST NOT also be banked as the same reservoir token; a refill-only result MUST be banked at most once.

#### Scenario: Merging Concurrent Solves on Empty Queue
- **WHEN** 3 concurrent requests arrive simultaneously for proxy `21100` whose reservoir is empty
- **THEN** the system MUST initiate exactly ONE Node.js solve task for `21100`
- **AND** all 3 requests MUST await the completion of this single in-flight task without launching redundant solver processes

#### Scenario: Cancelling One Waiting Request
- **WHEN** 3 requests share a solve and one request is cancelled or its waiting deadline expires before completion
- **THEN** the other requests MUST still receive the producer's actual result
- **AND** the cancelled request MUST leave without corrupting the shared result or releasing a resource it does not own

#### Scenario: Foreground Joins Refill
- **WHEN** a foreground request joins the same proxy's active refill solve
- **THEN** no duplicate solve MUST be started for that proxy, and subsequent solve admission MUST use foreground priority while foreground demand remains
- **AND** the shared successful result delivered to foreground requests MUST NOT also remain available in the reservoir as the same token

#### Scenario: Refill Requests Are Consolidated
- **WHEN** several refill triggers target the same proxy during one refill-only solve
- **THEN** they MUST share the producer and its result MUST be inserted into that reservoir at most once

#### Scenario: Last Consumer Leaves
- **WHEN** all consumers leave a solving operation and no refill demand remains
- **THEN** the operation MUST be cancelled and its child process reaped, and its slot and shared-operation registration MUST be released
- **AND** a later request for that proxy MUST be able to create a fresh operation

### Requirement: Adaptive CPU Concurrency Guard

The system MUST calculate solver concurrency limits based on host CPU core count (1 on single-core systems, `max(1, cpu_count - 1)` on multi-core systems) and enforce bounded acquisition waits to prevent solver deadlocks. Only an active solve attempt, including its process cleanup, MUST consume solver capacity; configuration retrieval and retry backoff MUST NOT hold that capacity. Foreground and refill work MUST share the same capacity limit. Foreground acquisition MUST use the solving operation's remaining budget without an additional 10-second cutoff.

#### Scenario: Single-Core VPS Concurrency Clamping
- **WHEN** running on a host with 1 CPU core
- **THEN** active Node.js solves MUST be limited to strictly 1, preserving CPU protection and event-loop responsiveness

#### Scenario: Two-Core Foreground and Refill Contention
- **WHEN** running on 2 cores with both foreground and refill demand
- **THEN** total active Node.js solves MUST remain at most 1
- **AND** assigning different priorities MUST NOT create a second independent capacity allowance

#### Scenario: Retry Backoff Releases Capacity
- **WHEN** a solve attempt fails and will retry after a backoff while another foreground proxy needs a solve
- **THEN** the failed attempt MUST release its capacity before backoff
- **AND** the other foreground solve MUST be able to begin during that interval when capacity is available

#### Scenario: Legal Solve Exceeds Old Queue Slice
- **WHEN** the single slot is occupied by an attempt lasting 11 seconds and another foreground solve still has sufficient remaining budget
- **THEN** the waiting solve MUST be able to acquire capacity after the active attempt completes
- **AND** the old 10-second queue slice MUST NOT make it fail first

#### Scenario: Timeout and Shutdown Reclaim Resources
- **WHEN** a solve is cancelled, exceeds its execution deadline or the application closes
- **THEN** all owned child processes MUST be terminated and reaped, capacity returned once, and owned background/shared registrations cleared
- **AND** cleanup MUST complete before application-close completion is reported

### Requirement: Three-State Node Circuit Breaker (CLOSED / OPEN / HALF-OPEN)

The system MUST implement a three-state circuit breaker per proxy port. It MUST count only explicit egress network failures or upstream WAF evidence, transition to OPEN for the existing 600-second cooldown, and use exactly one actual HALF-OPEN network attempt to test recovery. Local solver/SDK/parameter failures, capacity waits, cancellation and unknown failures MUST NOT increment egress failure counts or change an account's quota or credential state. Health inspection MUST NOT consume the HALF-OPEN attempt; an attempt abandoned before networking MUST release its admission without reporting a proxy failure.

#### Scenario: WAF Ban Rapid Tripping
- **WHEN** a proxy receives 2 consecutive explicit Alibaba WAF F001 or 3012 ban responses
- **THEN** that proxy MUST transition to OPEN for 600 seconds and be bypassed during routing
- **AND** local parameter validation failures MUST NOT be counted as these responses

#### Scenario: Half-Open Probe Recovery
- **WHEN** a proxy has been OPEN for 600 seconds
- **THEN** healthy-port inspection MUST leave its recovery admission available and exactly one actual network attempt MUST be admitted
- **AND** a successful attempt MUST close the breaker; an evidenced egress network failure MUST reopen it for 600 seconds

#### Scenario: Local Failure Does Not Quarantine Egress
- **WHEN** three foreground solves fail with invalid parameters, unavailable Node or budget exhaustion without egress network evidence
- **THEN** the proxy MUST retain its existing failure count and state
- **AND** its otherwise eligible accounts MUST NOT be excluded as region-offline because of these local failures

#### Scenario: Half-Open Local Abort Releases Admission
- **WHEN** the admitted recovery attempt is cancelled or fails locally before making a network request
- **THEN** its admission MUST be released without adding failures or extending quarantine
- **AND** a subsequent actual request MUST be able to test that proxy without remaining stuck behind the abandoned attempt

## ADDED Requirements

### Requirement: 后台补货使用空闲容量并为前台让位

后台补货 MUST 只在无未完成前台求解需求且有空闲容量时开始新的独立尝试；前台重试退避仍属于未完成需求。容量不足或需要让位时，补货 MUST 延后且不占等待队列、不执行失败计数、不标记出口或账号故障。已有后台尝试可以完成，随后 MUST 为前台让位。各代理的补货 MUST 保持亲和隔离，延后一个代理不能阻塞全部其它代理的周期检查。

#### Scenario: 前台到达正在运行的补货
- **WHEN** 后台尝试已经运行，另一出口前台需求到达
- **THEN** 当前后台尝试可以结束，后续后台尝试 MUST 延后，前台优先取得下一空闲容量
- **AND** 不终止有效的后台进程以腾槽，也不突破原并发上限

#### Scenario: 前台退避仍有优先权
- **WHEN** 前台的失败尝试已释放容量并进入退避，后台补货再次触发
- **THEN** 新的独立后台尝试 MUST 延后，不在前台尚未完成时再次占满容量

#### Scenario: 负载结束恢复补货
- **WHEN** 未完成前台需求降为零，水库低于既有最低库存且容量空闲
- **THEN** 补货 MUST 在既有周期机制下恢复，在正确代理队列存入有效结果
- **AND** 无需新增人工恢复操作

### Requirement: 求解预算有界且不连带健康后备出口

一次共享求解 MUST 使用从创建开始的绝对总截止时间，覆盖配置获取、排队、执行和退避，采用现有验证码超时设置，默认 40 秒。同一共享求解的重试或新等待者 MUST NOT 重置期限。其它出口的新求解及现有有限挑战重解 MUST 取得各自独立预算，不继承首个出口已耗尽的期限；上游模型连接、429等待及已开始的模型/SSE输出 MUST NOT 受本次求解期限限制。有效亲和缓存 MUST 可以立即返回。

#### Scenario: 失败出口不能用尽健康出口的机会
- **WHEN** 出口 A 的求解耗尽 40 秒预算，随后同一请求选择有额度且空池的健康出口 B
- **THEN** B 的新求解 MUST 有自己完整的预算并能产生真实成功结果
- **AND** 不得把 A 的过期期限作为 B 的启动拒绝理由

#### Scenario: 配置获取和退避也消耗预算
- **WHEN** 配置获取、容量等待、尝试执行及退避累计达到本次总截止
- **THEN** 不再联网或启动下一次尝试，终态 MUST 指明预算耗尽并开始资源回收
- **AND** 回收时间单独可观测，不能宣称进程在截止瞬间已完全回收

#### Scenario: 后加入者和取消不重置共享期限
- **WHEN** 新请求加入正在求解的同出口 producer，或某个等待者取消
- **THEN** producer MUST 继续使用其创建时的期限，不续期或被单个等待者取消
- **AND** 其它消费者仍能取得真实结果

#### Scenario: 有效缓存不需要新求解
- **WHEN** 亲和水库中存在有效 token
- **THEN** 系统 MUST 允许立即取出有效 token
- **AND** 不等待或启动新的 producer，不终止正常模型输出

### Requirement: 同请求同出口失败不重复现解

一次客户端请求 MUST 复用本次已终止的同出口求解失败证据，在亲和缓存仍空时避开再次独立现解；它 MUST 保留不同出口、有效缓存和无需验证码通道的服务机会。避让记录 MUST 随本请求结束释放，不持久化、不跨请求共享，取消或后台延后 MUST NOT 被记录为出口求解失败。

#### Scenario: 同出口换号不再启动一轮
- **WHEN** 候选 A 的验证码 producer 已终止失败，后续候选 B 使用同一实际出口且仍无缓存
- **THEN** B MUST 复用相同安全失败证据和 solve_id，不再次发起同一出口 producer
- **AND** 健康另一出口 C MUST 仍可正常求解并完成模型请求

#### Scenario: 有效缓存允许恢复
- **WHEN** 同请求已记住出口 A 的失败，但后续 A 水库中已有有效补货 token
- **THEN** 有效 token MUST 可以被立即消费
- **AND** 不能因先前避让记录而阻断本次实际成功

#### Scenario: 新请求具有新的机会
- **WHEN** 先前请求结束后新请求到达同出口
- **THEN** 新请求 MUST 不受前一个请求的失败记录约束
- **AND** 仍遵守单飞与有限 producer 预算
