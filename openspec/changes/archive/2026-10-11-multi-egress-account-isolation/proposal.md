# Proposal

## Why
上游 Z.AI 服务端对底层网络栈指纹与多账号访问特征实施严格风控审计（如 3012 批量群控拦截）。
此前网关层使用模块级单例 `httpx.AsyncClient`，导致系统中所有账号交替复用同一条底层 TCP/TLS 链路，产生传输层强关联；且 `claim.py` 与 `quota.py` 随意创建临时裸客户端直连，缺乏出口治理与隔离。
为此需要引入账号级物理隔离的连接池治理、多机场订阅聚合出口路由，并具备未配置代理时的优雅后备降级能力，彻底切断上游关联并保证高可用。

## What Changes
- **BREAKING**: 废除 `app/routes/gateway.py` 中的模块级共享客户端 `_SHARED_CLIENTS` 与 `_get_shared_client()`，禁止跨账号复用底层 TCP/TLS 连接。
- **新增连接池模块**: 新增 `app/client_pool.py`，提供 `AccountClientPool` 单例，生命周期管理每个账号的 `httpx.AsyncClient`（空闲 5 分钟超时自动回收）。
- **统一出口路由与一致性哈希**:
  - 优先级 1: 账号显式绑定代理（`account.proxy`）；
  - 优先级 2: 全局多出口代理列表（`ZCODE_PROXIES`），按 `account_id` 一致性哈希绑定固定出口，实现“单账号固定出口 IP”；
  - 优先级 3: 优雅降级后备（未配置代理或代理池为空），自动回落为「账号专属独立的直连 Client」，并在日志打印提示，绝不断网。
- **全业务链路收口**: 重构 `app/routes/gateway.py`、`app/claim.py` 与 `app/quota.py`，全部统一使用 `account_client_pool.get_client(account_id)`。
- **跨端订阅通道与 VPS 部署**: 在 `agent-workspace/scripts/remote-vps.ps1` 扩展 `deploy-proxy` 动作，通过 SSH 隧道将多机场订阅安全推送到 VPS `/etc/mihomo/`，利用 Mihomo 原生 `proxy-providers` 自动聚合并提供 4 个地区本地中继端口（21081~21084）。

## Capabilities

### New Capabilities
- `account-client-pool`: 账号专属独立 TCP/TLS 连接池治理、多出口一致性哈希分配与无代理优雅直连降级规范。

### Modified Capabilities

## Impact
- 受影响代码：`app/client_pool.py`（新增）、`app/settings.py`、`app/routes/gateway.py`、`app/claim.py`、`app/quota.py`、`tests/unit/test_client_pool.py`（新增）。
- 外部依赖与运维：`agent-workspace/scripts/remote-vps.ps1`、VPS `/etc/mihomo/` 与 `mihomo.service`。
