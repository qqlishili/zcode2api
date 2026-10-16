# Design: 全生命周期账号网络出口严格归一化设计 (Account Egress Convergence)

## 1. 架构背景与第一性原理

在反爬与业务风控对抗中，阿里 WAF 与上游 API 服务通过追踪客户端设备网络层特征（IP、ASN、TLS Client Hello、网络跳数）与业务层凭证（JWT、User ID、Device ID）进行联合风控打分。

### 核心矛盾与历史盲区
- **物理事实**：真实终端用户在本地运行客户端时，所有外呼流量（包括软件自动检测更新、无痕验证码拉取、日活心跳上报、额度拉取、对话推理）均来自该终端所在的单一物理宽带出口（如台湾住宅 IP）。
- **历史实现缺陷**：
  1. **日活上报裸奔**：`telemetry.py` 私建裸 `AsyncClient`，直连 VPS 机房 IP（RackNerd AS36352）；
  2. **验证码求解器“安慰剂代理”陷阱**：`captcha_node/package.json` 缺少 `undici` 依赖，导致 `require("undici")` 抛错静默捕获，Node 求解器仍然裸走 VPS 机房 IP 直连阿里 CDN 与风控服务器；
  3. **验证码跨大区污染风险**：通用预解池若使用 HK 代理求解，发放给 TW 账号使用，导致“港台跨区 IP 冲突”；
  4. **大区故障穿透裸连**：`client_pool.py` 在大区端口全部离线时错误降级为无代理直连，瞬间以机房 IP 裸奔出网；
  5. **旁路配置与全局安装序泄露**：`captcha.fetch_config()` 与 `install.run_install_sequence()` 仍走无代理连接。
- **二阶后果**：上游风控引擎和阿里 WAF 抓到同一账号在毫秒级内同时跨越中美或港台异地活跃，直接触发 3012（`unusual activity`）硬拦截。

```
【修复前：旁路撕裂与安慰剂代理（引发 3012）】
  Account (TW) ──┬──▶ 模型对话 /messages ─────────▶ [TW 21200 住宅代理] ──▶ 上游 (OK)
                 ├──▶ 日活上报 event/report ──────▶ [VPS 原生机房 IP]  ──▶ 上游 (异地撕裂 3012!)
                 ├──▶ 验证码求解 solver.js ───────▶ [无 undici 静默绕过]  ──▶ 阿里 CDN (机房标记 3012!)
                 └──▶ 大区离线降级 ───────────────▶ [直连穿透裸奔]     ──▶ 上游 (直接爆 3012!)

【修复后：全链路归一与大区亲和闭环】
  Account (TW) ──▶ account_client_pool ──▶ [TW 21200 住宅代理]
                         ├─▶ 模型对话 /messages
                         ├─▶ 额度/领活动 billing/*
                         ├─▶ 日活上报 event/report (通过专属 client)
                         ├─▶ 验证码求解 solver.js (undici ProxyAgent 注入 TW 21200)
                         └─▶ 大区离线熔断：抛 RegionOfflineError，调度器换号，严禁直连！
```

---

## 2. 详细技术方案与 6 大加固项

### 2.1 依赖补齐与 Node 求解器真实代理能力 (解决安慰剂陷阱)
- **`captcha_node/package.json`**：显式添加 `"undici": "^6.21.0"` 依赖，确保 `require("undici")` 成功加载。
- **`captcha_node/solver.js`**：
  - 启动阶段使用 `new ProxyAgent(proxyUrl)` 注册为 `setGlobalDispatcher`；
  - 改造 `syncFetchBody`：当 `proxyUrl` 存在时，通过传入代理参数配置 Node 网络层，严禁任何子进程绕过代理直连阿里 CDN。

### 2.2 Token 大区亲和性隔离与按需求解 (解决跨大区污染)
- **`app/captcha.py:_Token` 数据结构扩展**：
  ```python
  class _Token:
      __slots__ = ("param", "region", "proxy", "born_at")
      def __init__(self, param: str, region: str | None, proxy: str | None) -> None:
          self.param = param
          self.region = region
          self.proxy = proxy
          self.born_at = time.monotonic()
  ```
- **按账号大区亲和匹配**：
  - `get_verify_param(account=None, proxy=None)`：
    - 若传入 `account`，计算该账号绑定的专属代理 `expected_proxy = account_client_pool.resolve_proxy(account)`；
    - 遍历池内 Token，**仅当 `token.proxy == expected_proxy` 且未过期时才出池复用**；
    - 若池中无同代理 Token，立即以 `expected_proxy` 执行同步现解（On-Demand Solve），并在后台触发同代理补货；
    - **铁律**：严禁将 HK 出口解得的 Token 发给 TW 账号使用！

### 2.3 业务全链路消费端透传实参
- **`app/claim.py:675`**：
  ```python
  # 改造前：verify_param, verify_region = await captcha_manager.get_verify_param()
  # 改造后：
  verify_param, verify_region = await captcha_manager.get_verify_param(account=account)
  ```
- **`app/routes/gateway.py:1320`**：
  ```python
  # 改造前：verify_param, verify_region = await captcha_manager.get_verify_param(port)
  # 改造后：
  verify_param, verify_region = await captcha_manager.get_verify_param(account=account)
  ```

### 2.4 大区离线熔断栅栏 (Fence Principle)
- **`app/client_pool.py:get_client` 熔断加固**：
  - 当账号具有显式的 `assigned_region` 时：
    - 若 `self.resolve_proxy(account)` 返回 `None`（该大区所有百位段端口均离线）；
    - **严禁降级为无代理直连 Client！**
    - 抛出结构化领域异常 `RegionOfflineError(f"大区 {assigned_region} 当前无可用健康出口")`；
    - 上层 `gateway.py` 捕获后，触发安全避让，切换池内下一大区的健康账号，彻底封死机房 IP 裸连后路。

### 2.5 旁路外呼全面收口
- **`app/telemetry.py`**：`post_activation_event` 强制接收可选 `client: httpx.AsyncClient`，`claim.py` 与 `install.py` 统一传入账号专属客户端。
- **`app/captcha.py:fetch_config`**：使用 `account_client_pool.get_any_healthy_proxy()` 建立代理客户端，杜绝直连 `zcode.z.ai`。
- **`app/install.py:run_install_sequence`**：进程级启动安装序使用健康大区代理拉取配置与打点，禁止原生机房出网。

---

## 3. 风险矩阵与防御策略 (Risk Matrix)

| 潜在风险 / 场景 | 二阶影响分析 | 架构防御机制 |
| :--- | :--- | :--- |
| **Node undici 缺失** | `solver.js` 代理静默跳过（历史致命缺陷） | `package.json` 显式锁版本，自动化测试校验 `require("undici")` 存在性。 |
| **跨大区 Token 混用** | HK 验证码在 TW 提交触发 3012 | `_Token.proxy` 亲和性强校验，无同代理 Token 立即现解。 |
| **大区百位段出口全挂** | 降级直连导致机房 IP 泄露封号 | 严格抛出 `RegionOfflineError`，前置熔断换号，宁停勿泄。 |
| **脱机单测执行** | 无外部代理导致本地单元测试阻塞 | 测试夹具注入 Mock 代理 Client 与本地回环虚拟端口，保持离线 100% 可测。 |

---

## 4. 验证与完成条件 (DoD)

1. **静态依赖闭环**：`captcha_node/package.json` 包含 `undici`，本地与 VPS 均完成 `npm install`。
2. **零旁路泄露**：全库不存在绕过 `account_client_pool` 的裸外呼代码。
3. **隔离性单测 100% 覆盖**：新增针对 `undici` 代理注入、Token 亲和性匹配、大区离线熔断的单元测试。
4. **全量回归 100% PASS**：全库测试套件（400+ 项）全部通过。
5. **生产热载与实测生效**：VPS 代码与 Node 依赖同步更新，`zcode2api.service` 稳定在线，账号外呼 100% 同源。
