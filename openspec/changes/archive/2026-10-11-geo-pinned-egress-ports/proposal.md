# Proposal: 基于大区优先级、百位端口段与账号永久粘性的多出口网关架构

## Why

当前 ZCode2API 网关的多出口代理池采用简单的全局线性取模哈希（`hash(account_id) % len(proxies)`），存在两大系统性隐患：
1. **跨大洲瞬移风控（Geo-Hopping Anomaly）**：当节点池增减时，取模结果剧烈扰动，导致同一账号在数秒内从美国出口跳至日本或香港出口，直接触发阿里 WAF 对单一 Token 的异地异速检测（`3012 unusual activity`）封禁。
2. **更换机场产生出口断崖**：探活脚本每次直接覆盖配置，换机场时存量健康的可用出口瞬间清零，缺乏增量合并与平滑折旧机制。

本方案旨在重构出口引擎，确立大区优先级权重，实行大区百位独立端口段隔离，并在数据库中永久锁定账号归属大区，实现“换账号接单，绝不跨区换节点”的安全闭环。

## What Changes

- **大区优先级与发牌权重（Market-Driven Region Priority）**：根据市面节点供给量与风控对抗经验划分 P0（HK、TW、JP，供给极大、延迟低）、P1（SG、US）、P2（KR、EU、OTHER），账号按各大区健康活跃节点容量比例加权发牌。
- **平滑增量合并与生命周期管理（Incremental Node Lifecycle）**：以 `server_ip:port:type` 为物理唯一指纹。更换订阅时，保留原大区健康存量节点并与新节点合流；不可用节点进入 `stale` 观察期（12h），多次失效才转入 `tombstone` 淘汰，彻底解决换机场出口断崖。
- **落地大区强正则归一化（Egress Region Regex Engine）**：引入双层正则，严格过滤“中转/入口/隧道/专线”前缀，以最终出口落地（Egress）为准归类到 `HK/TW/JP/SG/US/KR/EU/OTHER`。
- **大区百位端口段物理装配（100-Port Tiered Ranges）**：每个大区预留 100 个本地独立监听端口（如 HK `21100~21199`、TW `21200~21299`、JP `21300~21399` 等），端口充裕，支持动态扩展。
- **账号永久锁定大区与故障避让（Sticky Region & Safe Evacuation）**：
  - 账号在首次分配时确定大区并持久化落库 SQLite（`assigned_region` 字段），终身不跨大洲跳变。
  - 当某大区出口全部离线时，调度器**将该账号标记避让冷却，切换其他大区的健康账号接单，绝不强行将该账号切换至异国节点**。
- **Mihomo API 平滑热重载（Zero-Downtime Reload）**：废弃 `systemctl restart mihomo` 暴力杀连接行为，改用 Mihomo Controller API（`PUT /configs`）进行内存平滑重载，保护正在出词的 SSE 长连接。

## Capabilities

### New Capabilities
- `egress-region-pinning`: 实现大区百位端口段物理隔离、账号大区永久粘性绑定、容量加权发牌、跨机场增量生命周期合并与零断流热重载。

### Modified Capabilities
- 无（系统核心推理契约不变更，仅增强网络传输面）。

## Impact

- **代码模块**：
  - `app/client_pool.py`：重构为两级解析（账号 -> 专属大区 -> 大区内一致性哈希），增加大区健康感知与避让标记。
  - `app/models.py` 与 `app/store.py`：`Account` 模型增加 `assigned_region` 字段并持久化至 SQLite。
  - `app/routes/gateway.py`：账号调度器增加大区可用性感知，大区无节点时自动切下一账号。
  - `scripts/mihomo_auto_probe.py`：重构为全量探活、增量生命周期合并、多级落地正则、百位端口段装配及 Controller API 平滑热重载。
- **外部配置**：
  - `/etc/mihomo/config.yaml`：生成按大区百位端口分段的 listeners 与 proxies。
  - `dengyie-zcode2api/.env`：更新 `ZCODE_PROXIES` 为百位段结构化端口列表。
