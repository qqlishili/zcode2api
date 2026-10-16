# Spec: strict-egress-convergence

## 1. 契约规范与范围

本规范定义 ZCode2API 系统中单账号生命周期网络外呼的出口归一化行为，确保任何涉及上游业务及风控打点的网络请求与该账号归属的物理代理出口保持 100% 严格一致，彻底消除机房 IP 泄露与跨大区 Token 污染。

---

## 2. 行为需求与场景验收契约

### REQ-CONV-01: 日活与激活上报必须复用账号大区专属客户端
- **描述**：`app/telemetry.py` 的 `post_activation_event` 必须支持接收 `client: httpx.AsyncClient`。
- **场景 1（账号活动领取前置日活）**：
  - 当 `app/claim.py` 的 `report_activation_events(account)` 执行时；
  - 系统必须调用 `account_client_pool.get_client(account)` 获取客户端；
  - 该客户端配置的代理必须是 `account.assigned_region` 对应的大区百位段端口；
  - 激活事件请求必须由该客户端发送，严禁新建无代理连接。
- **场景 2（单元测试与向后兼容）**：
  - 当未传入 `client` 时，使用默认超时建立临时客户端，确保既有无代理 Mock 测试不报错。

### REQ-CONV-02: 设备初始化安装序与配置拉取必须绑定代理出口
- **描述**：`app/install.py` 的 `run_install_sequence_for_account(account)` 与 `_fetch_client_configs` 必须全程使用账号专属客户端；全局 `run_install_sequence()` 必须使用健康代理。
- **场景 1（账号安装序）**：
  - `_fetch_client_configs` 与 `post_activation_event` 必须透传 `account_client_pool.get_client(account)`。
- **场景 2（全局启动安装序）**：
  - `run_install_sequence()` 必须通过 `account_client_pool.get_any_healthy_proxy()` 建立代理客户端，杜绝机房原生 IP 出网。

### REQ-CONV-03: 验证码求解器必须具备真实 undici 代理能力（消灭安慰剂陷阱）
- **描述**：`captcha_node/package.json` 必须显式引入 `"undici": "^6.21.0"` 并在环境完成安装；`app/captcha.py` 的 `_run_solver` 必须向 Node 子进程注入 `HTTP_PROXY` 与 `HTTPS_PROXY`。
- **场景 1（依赖与注入生效）**：
  - Node 启动时 `require("undici")` 必须成功返回，`new ProxyAgent(proxyUrl)` 必须成功挂载为全局 Dispatcher；
  - 严禁出现 `MODULE_NOT_FOUND` 异常捕获后静默退化为裸机房直连。
- **场景 2（底层 syncFetch 代理安全）**：
  - `solver.js` 中的 `syncFetchBody` 必须感知 `proxyUrl`，禁止绕过代理发起原生 HTTP 请求。

### REQ-CONV-04: 验证码 Token 大区亲和性隔离（杜绝跨区污染）
- **描述**：`_Token` 必须记录其出生代理 URL `proxy`；`get_verify_param` 必须根据账号执行亲和性匹配。
- **场景 1（同大区命中）**：
  - 账号为 TW 账号，其大区代理为 `http://127.0.0.1:21200`；
  - 池中存在 `token.proxy == "http://127.0.0.1:21200"` 的有效 Token；
  - 系统出池该 Token 供业务使用。
- **场景 2（跨大区隔离与同步现解）**：
  - 账号为 TW 账号（21200），但池中仅有 HK（21100）生成的 Token；
  - 系统严禁将 HK Token 发放给 TW 账号；
  - 系统必须立即使用 `http://127.0.0.1:21200` 触发 On-Demand 同步现解，保证 Token 出生地与使用地严格同一。

### REQ-CONV-05: 大区离线熔断栅栏（禁止直连降级）
- **描述**：当账号已分配大区但该大区无健康出口时，系统严禁降级为无代理直连。
- **场景 1（离线熔断）**：
  - 账号 `account.assigned_region == "TW"`，但当前 TW 注册表为空；
  - `account_client_pool.get_client(account)` 必须抛出 `RegionOfflineError`；
  - 系统严禁返回裸机房 `AsyncClient`。
- **场景 2（调度避让）**：
  - 网关 `gateway.py` 捕获 `RegionOfflineError` 后，将该账号标记避让，自动轮转至其他大区的可用账号。

### REQ-CONV-06: 业务消费端透传实参
- **描述**：全库验证码获取调用点必须显式传入 `account=account`。
- **场景 1（claim 领取）**：
  - `app/claim.py:675` 必须调用 `captcha_manager.get_verify_param(account=account)`。
- **场景 2（gateway 对话刷新）**：
  - `app/routes/gateway.py:1320` 必须调用 `captcha_manager.get_verify_param(account=account)`。
