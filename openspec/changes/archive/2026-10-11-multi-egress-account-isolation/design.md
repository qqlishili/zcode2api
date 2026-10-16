# Design

## Context
参见 `proposal.md`。现有代码中，`app/routes/gateway.py` 内部使用弱引用字典 `_SHARED_CLIENTS` 为每个事件循环提供单例 `httpx.AsyncClient`，导致系统中 13 个账号交替复用同一批底层 TCP 连接；`app/claim.py` 与 `app/quota.py` 则是即时创建单次 client 直连出网。
需要重构成统一的客户端生命周期管理器 `AccountClientPool`，并具备多出口哈希与直连优雅降级。

## Goals / Non-Goals

**Goals:**
- 实现账号级 `httpx.AsyncClient` 实例完全隔离，底层 TCP 连接池与 TLS 会话互不干扰。
- 支持 `account.proxy`（最高优先级）与全局 `ZCODE_PROXIES` 多出口一致性哈希绑定（单账号固定出口 IP）。
- 支持优雅后备降级：当未配置任何代理时，系统自动降级为「账号专属直连 Client」，业务 100% 走通，绝不阻断。
- 自动化连接资源回收：空闲超过 TTL（默认 300 秒）自动释放与关闭 Client。
- 业务调用点归一化收敛：`gateway.py`、`claim.py` 与 `quota.py` 统一接入 `account_client_pool.get_client(account_id)`。
- 跨端多机场订阅聚合与直推：通过 `agent-workspace/scripts/remote-vps.ps1 deploy-proxy` 经 SSH 推送至 VPS Mihomo，开辟 4 地区本地回环端口（21081~21084）。

**Non-Goals:**
- 在 Python 代码内直接实现 Shadowsocks/VMess/Trojan 代理协议解析（减法原则，完全交由 Mihomo 内核托管）。
- 对代理节点做动态深度测速排序（复用 Mihomo 内置 health check 与 url-test）。

## Decisions

### 1. 采用 `AccountClientPool` 单例托管而非每个请求新建 Client
- **原因**: 每个请求单独创建与销毁 `httpx.AsyncClient` 会产生高频 TCP 握手开销与端口耗尽风险；单例池按 `account_id` 维护并缓存 Client，同账号请求复用连接，跨账号完全隔离。
- **备选方案**:
  - 全局单例 Client（现状）：被上游 WAF 抓取多账号关联风控，已废弃。
  - 每个请求新建 Client：性能较差，高并发下导致 TIME_WAIT 端口枯竭。

### 2. 多出口一致性哈希分配与单账号固定绑定
- **原因**:
  - 对每个账号计算哈希：`index = int(hashlib.md5(account_id.encode()).hexdigest(), 16) % len(proxies)`；
  - 保证同一账号只要代理池不变，始终命中同一个出口端口与地区，避免同一账号在几秒内 IP 跨国跳跃引发安全拦截。
- **备选方案**:
  - 随机轮询：导致单账号 IP 频繁漂移，触发上游异地登录拦截。

### 3. 未配置代理时的独立直连优雅后备
- **原因**: 满足第一性原理与韧性原则。未配置代理时不应抛错中断，也不应回退到全局共享池；直接为账号生成无代理的独立 Client，消除网络串联。

### 4. 敏感订阅信息与代码仓库物理栅栏
- **原因**: 机场订阅含有用户私有 Token，严禁提交到 Git。订阅仅存在于本地 `agent-workspace/.env`，通过 SSH 直接写入 VPS `/etc/mihomo/sub.url`（权限 0600）。

## Risks / Trade-offs

- **[Risk] 代理节点故障导致请求超时** → **Mitigation**: VPS Mihomo 配置自动 URL-test 健康切换同地区可用节点；同时单账号可随时覆盖 `account.proxy`。
- **[Risk] 账号过多导致内存或连接句柄增长** → **Mitigation**: 池管理器内置 `prune_idle()`，空闲超 300 秒自动执行 `aclose()` 并驱逐。
- **[Risk] 单元测试受系统代理干扰** → **Mitigation**: 单元测试中显式注入 mock proxy 或测试用例隔离环境。

## Migration Plan
1. 新增 `app/client_pool.py` 及完整单元测试 `tests/unit/test_client_pool.py`。
2. 改造 `gateway.py`、`claim.py`、`quota.py` 收口接入。
3. 在 `agent-workspace` 编写 `remote-vps.ps1 deploy-proxy` 并推送到 VPS。
4. VPS 拉起 Mihomo 4 地区中继端口，全量运行单测与连通性验证。
