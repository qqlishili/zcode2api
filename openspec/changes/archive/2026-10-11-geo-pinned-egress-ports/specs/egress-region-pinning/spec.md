# Spec Delta

## Purpose

本规范定义网关多出口节点基于大区优先级、百位端口物理隔离与账号永久粘性（Sticky Region）的路由分配契约，彻底阻断跨洲地理瞬移风控与换机场断崖。

## ADDED Requirements

### Requirement: Egress Region Classification
The system MUST classify each egress node into standard region codes (`HK`, `TW`, `JP`, `SG`, `US`, `KR`, `EU`, `OTHER`) using exit落地 regex, and MUST verify ground-truth outbound IP location during live probe to correct deceptive node names.

#### Scenario: Deceptive Egress Name Correction
- **WHEN** 节点名称标为 `日本中转->香港直连` 但真机探针出口 IP 归属地探测为美国时
- **THEN** 系统必须将该节点归入 `US` 大区端口段，严禁归入 `HK`

### Requirement: Port Tier Segmentation and Active Registry
The system MUST allocate physical listening ports into distinct 100-port tiers per region (HK 21100~21199, TW 21200~21299, JP 21300~21399, SG 21400~21499, US 21500~21599, KR 21600~21699, EU 21700~21799, OTHER 21800~21899) and MUST output a dynamic active port registry (`data/egress_registry.json`).

#### Scenario: Real-time Active Port Registry
- **WHEN** 香港大区筛选出 4 个健康节点时
- **THEN** 系统必须将 `{"HK": [21100, 21101, 21102, 21103]}` 写入活跃注册表供网关精准寻址

### Requirement: Slot Reservation vs Active Routing Decoupling
The system MUST decouple port slot reservation from active routing, ensuring that stale or unverified ports keep their slot offset in Mihomo listeners but MUST NOT receive live user traffic.

#### Scenario: Stale Node Traffic Blackhole Prevention
- **WHEN** 某个节点在重载后未响应变成 stale 状态时
- **THEN** 该节点在 Mihomo 监听中保留端口占位以防序号抖动，但必须立即从网关活跃注册表中剔除

### Requirement: Offline Seeding and Account Sticky Region Pinning
The system MUST bind each account to an assigned region via offline deterministic seeding with hard tier quotas (P0 >= 80%, P1 <= 20%, P2 = 0%), persist it to SQLite, and MUST only route requests through active ports within that assigned region.

#### Scenario: Deterministic Region Allocation
- **WHEN** 执行离线发牌脚本时
- **THEN** 系统必须将当前 13 个账号中的 10~11 个账号绑定至 P0 大区（HK/TW/JP），并在后续请求中终身锁定

### Requirement: Region-Aware Scheduler Selection
When all proxies in an account's assigned region fail, the scheduler MUST pre-filter and bypass accounts of that region during account selection, immediately selecting an account from a healthy region without consuming request attempt limits.

#### Scenario: Avoid Request Attempt Depletion
- **WHEN** `US` 大区活跃出口为 0 且收到客户端请求时
- **THEN** `store.select()` 必须在候选池中直接跳过所有 US 账号，指派健康大区账号（如 HK 账号）接单

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
