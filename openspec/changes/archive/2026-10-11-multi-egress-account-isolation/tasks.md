# Tasks

## 1. 客户端连接池核心与配置

- [x] 1.1 在 `app/settings.py` 中增加 `ZCODE_PROXIES` 环境变量解析（支持逗号分隔解析为列表），并通过单测验证。
- [x] 1.2 实现 `app/client_pool.py`（`AccountClientPool`），支持账号独立 Client、一致性哈希出口绑定、无配置优雅直连降级与空闲连接回收，编写 `tests/unit/test_client_pool.py` 验证各场景分支。

## 2. 业务链路收敛与折旧

- [x] 2.1 改造 `app/routes/gateway.py`，彻底移除全局单例 `_SHARED_CLIENTS`，统一接入 `account_client_pool.get_client(account.id)` 并通过网关单元测试。
- [x] 2.2 改造 `app/claim.py` 与 `app/quota.py`，移除临时 `httpx.AsyncClient`，统一收敛至 `account_client_pool` 并通过单测复验。

## 3. 跨端多机场订阅与 VPS 中继

- [x] 3.1 在 `agent-workspace/scripts/remote-vps.ps1` 扩展 `deploy-proxy` 动作，读取 `AIRPORT_SUB_URLS` 并经 SSH 安全推送到 VPS `/etc/mihomo/`。
- [x] 3.2 VPS 配置并启动 Mihomo 守护进程，监听本地 4 地区中继端口（21081~21084），并在 VPS `.env` 注入 `ZCODE_PROXIES`，通过 `curl -x` 验证出口连通性。

## 4. 全链路集成验收与 DoD 交付

- [x] 4.1 在本地和 VPS 执行全量单元测试与 Ruff 检查，保证 100% 通过无回归。
- [x] 4.2 使用 `openspec validate` 与 Antigravity CLI 完成变更规范验收。
