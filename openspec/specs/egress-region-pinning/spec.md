# egress-region-pinning Specification

## Purpose

定义节点地区、百位端口隔离和稳定账号归属；对话准入、同区固定领取绑定与物理退役分别校验。显式对话直连或本地共享代理不修改账号大区，也不保证消除地理或账号风控。

## Requirements

### Requirement: Egress Region Classification
The system MUST classify each egress node into standard region codes (`HK`, `TW`, `JP`, `SG`, `US`, `KR`, `EU`, `OTHER`) using exit落地 regex, and MUST verify ground-truth outbound IP location during live probe to correct deceptive node names.

#### Scenario: Deceptive Egress Name Correction
- **WHEN** 节点名称标为 `日本中转->香港直连` 但真机探针出口 IP 归属地探测为美国时
- **THEN** 系统必须将该节点归入 `US` 大区端口段，严禁归入 `HK`

### Requirement: Port Tier Segmentation and Active Registry
The system MUST retain regional tiers HK 21100–21199, TW 21200–21299, JP 21300–21399, SG 21400–21499, US 21500–21599, KR 21600–21699, EU 21700–21799 and OTHER 21800–21899, and the dynamic active registry for inherited managed chat routing. Explicit claim bindings MUST reference same-region reserved slot identities independently of chat admission; enabling claim use MUST NOT add those slots to the chat-active route list.

#### Scenario: Real-time Active Port Registry
- **WHEN** 香港大区筛选出 4 个可服务对话节点
- **THEN** 系统将对应 HK 端口写入对话活跃注册表供继承代理模式寻址。

#### Scenario: Claim-only HK slot
- **WHEN** HK 固定领取绑定有效，但该槽位未通过对话准入
- **THEN** 领取按自身门禁尝试，该端口仍不出现在对话活跃列表。

### Requirement: Slot Reservation vs Active Routing Decoupling
The system MUST preserve slot offsets while evaluating admission for the requested purpose. Stale, identity-mismatched, empty, source-removed, or retiring slots MUST NOT accept new traffic. Chat draining alone MUST NOT forbid an explicitly bound, source-present claim slot; all fixed claim bindings and actual operation references MUST participate in retirement safety checks.

#### Scenario: Stale Node Traffic Blackhole Prevention
- **WHEN** 节点未验证或失效，不符合所请求用途的准入条件
- **THEN** 保留监听占位但禁止该用途新请求；不能仅凭端口存在宣称可服务。

#### Scenario: Claim binding prevents false drain completion
- **WHEN** 回收协议检查仍配置为固定领取出口的旧槽位
- **THEN** 必须识别该固定引用；在解除绑定、关闭新准入并释放在途操作前不得回收复用端口。

#### Scenario: Saved target is not the active binding
- **WHEN** 页面保存删除旧领取绑定，但运行快照仍持有该绑定
- **THEN** 排空协调继续报告旧固定引用，重启加载新配置之前不得回收该槽位。

### Requirement: Offline Seeding and Account Sticky Region Pinning
The system MUST retain existing account assignments and offline deterministic seeding quotas (P0 >= 80%, P1 <= 20%, P2 = 0%). Inherited managed proxy routing and explicit claim bindings MUST stay within that region. Explicit chat direct or a configured local shared chat proxy MUST NOT rewrite the assignment or require a chat-active port in the stored region.

#### Scenario: Deterministic Region Allocation
- **WHEN** 按既有离线发牌方式为 13 个账号分配地区
- **THEN** 保留原 P0 中 HK/TW/JP 10–11 个账号的目标及稳定归属规则；启用用途配置不重新发牌或批量改账号大区。

#### Scenario: Direct chat with fixed claim region
- **WHEN** HK 账号选择直连对话、固定 HK 领取
- **THEN** 账号大区仍为 HK；对话直连不算领取出口漂移，领取不得自动改为 TW。

### Requirement: Region-Aware Scheduler Selection
The scheduler MUST evaluate region availability using the effective chat policy. Under inherited regional chat proxy routing it MUST bypass accounts whose regions have no eligible chat proxy without consuming request attempts. Explicit direct or a configured shared local chat proxy MUST NOT be excluded solely by stored-region or claim-route failure.

#### Scenario: Avoid Request Attempt Depletion
- **WHEN** 继承区域代理的对话请求到达，US 无活跃对话出口
- **THEN** 调度器直接避让 US 账号，选择其它可服务地区账号，不消耗实际请求次数。

#### Scenario: Claim unavailable while direct chat works
- **WHEN** US 领取绑定不可用，但 chat 明确 direct 且账号具备额度
- **THEN** 该领取故障不构成对话调度排除条件。

### Requirement: Incremental Node Merging across Subscriptions
The system MUST merge candidate nodes incrementally by `server:port:type:secret:sni` fingerprint across subscription rotations, maintaining active/stale states to avoid egress port drops.

#### Scenario: Airport Rotation without Port Drop
- **WHEN** 用户更换订阅且新订阅暂无台湾节点时
- **THEN** 系统必须继续保留存量有效的台湾大区健康节点与监听端口，保障已绑定台湾大区的账号正常可用

### Requirement: Zero Downtime Hot Reload
The probe service MUST reload Mihomo configuration via its Controller API to avoid terminating active SSE streams.

#### Scenario: Seamless API Config Update
- **WHEN** 新的干净出口配置生成并触发加载时
- **THEN** 正在输出流式出词（SSE）的客户端请求不得被中断或产生 TCP RST 错误
