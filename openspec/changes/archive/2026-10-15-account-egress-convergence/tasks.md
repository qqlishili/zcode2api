# Tasks: 全生命周期账号网络出口严格归一化 (Account Egress Convergence)

## Phase 1: 依赖加固与大区离线熔断栅栏
- [x] 1.1 在 `captcha_node/package.json` 中添加 `"undici": "^6.21.0"` 依赖，执行 `npm install` 确保 `require("undici")` 可用。
- [x] 1.2 改造 `captcha_node/solver.js` 中的 `syncFetchBody`，注入代理网络配置，彻底消灭安慰剂代理与底层裸外呼。
- [x] 1.3 在 `app/client_pool.py` 中定义 `RegionOfflineError`，在 `get_client` 中加固：已分配大区但无健康端口时严禁降级直连，必须显式抛出异常。
- [x] 1.4 在 `app/client_pool.py` 中新增 `get_any_healthy_proxy()` 辅助方法，提供全局健康出口探测。
- [x] 1.5 编写 `tests/unit/test_client_pool_fence.py` 验证大区离线熔断与代理解析正确性。

## Phase 2: 日活、安装序与公共配置代理透传
- [x] 2.1 重构 `app/telemetry.py` 的 `post_activation_event`，支持接收可选 `client: httpx.AsyncClient`，废除强制裸建 Client。
- [x] 2.2 重构 `app/claim.py` 的 `report_activation_events`，前置获取 `account_client_pool.get_client(account)` 并注入。
- [x] 2.3 重构 `app/install.py` 的 `_fetch_client_configs` 与 `run_install_sequence_for_account`，全程透传账号专属 Client。
- [x] 2.4 重构 `app/install.py` 的 `run_install_sequence` 与 `app/captcha.py` 的 `fetch_config`，通过健康代理拉取配置，杜绝机房原生出网。
- [x] 2.5 编写 `tests/unit/test_telemetry_proxy_injection.py` 验证 `telemetry`、`install` 与 `fetch_config` 代理透传有效性。

## Phase 3: 验证码 Token 大区亲和性与消费端实参透传
- [x] 3.1 改造 `app/captcha.py` 的 `_Token` 数据结构，增加 `proxy` 属性记录生成出口。
- [x] 3.2 改造 `app/captcha.py` 的 `_run_solver`，在 `create_subprocess_exec` 时向 Node 子进程注入 `env={"HTTP_PROXY": proxy, "HTTPS_PROXY": proxy}`。
- [x] 3.3 改造 `app/captcha.py` 的 `get_verify_param`，实现根据 `account` 的专属代理进行 Token 亲和性匹配；未命中同大区 Token 时立即使用该代理触发 On-Demand 现解。
- [x] 3.4 改造 `app/claim.py:675`，将 `captcha_manager.get_verify_param()` 改为 `captcha_manager.get_verify_param(account=account)`。
- [x] 3.5 改造 `app/routes/gateway.py:1320`，将 `captcha_manager.get_verify_param(port)` 改为 `captcha_manager.get_verify_param(account=account)`。
- [x] 3.6 编写 `tests/unit/test_captcha_proxy_affinity.py` 验证 Token 亲和性隔离（杜绝 HK Token 被 TW 账号混用）与子进程环境变量注入。

## Phase 4: 全量回归与生产部署闭环
- [x] 4.1 运行全量单元测试（400+ 项），确保 100% PASS，无破坏性回归。
- [x] 4.2 SFTP 同步修改后的代码及 `captcha_node/package.json` 至 VPS 生产环境。
- [x] 4.3 在 VPS 执行 `cd /root/code/dengyie-zcode2api/captcha_node && npm install` 安装 `undici`。
- [x] 4.4 VPS 执行 `py_compile` 语法校验并 `systemctl restart zcode2api.service` 热重载。
- [x] 4.5 真实环境端到端验证：脱敏核验单账号的日活上报、验证码求解与业务调用出口 100% 处于同一大区住宅出口。
