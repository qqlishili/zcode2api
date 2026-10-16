# Tasks

## 1. 数据模型与大区离线固化

- [x] 1.1 在 `app/models.py` 与 `app/store.py` 扩充 `assigned_region` 属性，验证向后兼容与 SQLite 序列化落库测试通过
- [x] 1.2 编写并执行离线发牌脚本 `scripts/seed_account_regions.py`，按两阶段硬配额（P0: HK/TW/JP 占 80%+，P1: 20%，P2: 0%）完成 13 个账号大区永久固化并验证落库

## 2. 调度器与两级路由连接池改造

- [x] 2.1 重构 `app/client_pool.py` 读取 `data/egress_registry.json` 实现两级解析（账号 -> assigned_region -> 活跃端口一致性哈希），验证同大区内轮换且跨大区物理隔离
- [x] 2.2 改造 `app/store.py` 与 `app/routes/gateway.py` 选号逻辑，前置感知大区健康状态，自动剔除无可用出口大区的账号，验证大区故障时不消耗重试次数并无缝指派健康大区账号接单

## 3. 全量探活、真实 IP 校验与增量生命周期引擎

- [x] 3.1 编写落地大区正则提取与真实出口 IP（Ground-Truth Geo-IP）双重校验模块，验证中转地名与欺骗性命名被正确校正
- [x] 3.2 升级 `scripts/mihomo_auto_probe.py` 引入全凭证指纹（`server:port:type:secret:sni`）与 `active/stale/tombstone` 增量合并，实现槽位占位与在线路由解耦（stale 绝不进在线池）
- [x] 3.3 执行全量 144 节点真机风控探活（取消 8 节点截断），产出按 `HK/TW/JP/SG/US/KR/EU/OTHER` 分组的干净节点池与 `data/egress_registry.json`

## 4. 百位端口段装配与平滑热重载

- [x] 4.1 按大区百位段（HK 21100~21199, TW 21200~21299, JP 21300~21399 等各 100 端口）生成 `/etc/mihomo/config.yaml` 物理监听配置
- [x] 4.2 接入 Mihomo Controller API `PUT /configs` 平滑热重载，验证重载时活动的 SSE 长连接请求不产生 TCP RST 断流

## 5. 端到端全链路验收

- [x] 5.1 验证 13 个账号的大区绑定状态、各大区活跃端口映射及数据库持久化完整性
- [x] 5.2 从本地发起端到端模型请求测试，验证响应返回 HTTP 200 且账号出口无跨国漂移、无黑洞超时、上游 0 封禁
