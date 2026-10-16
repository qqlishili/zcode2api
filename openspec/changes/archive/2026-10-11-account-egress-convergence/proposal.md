# Proposal: 全生命周期账号网络出口严格归一化方案 (Account Egress Convergence)

## Why

在近期完成的 `geo-pinned-egress-ports` 架构升级中，系统实现了基于市场供给的大区百位段隔离（HK 21100+、TW 21200+、JP 21300+）以及模型推理（`/messages`）和额度查询（`/billing/quota`）的账号永久粘性绑定。然而，全库网络出口排查暴露了严重的**旁路外呼代理盲区**：

1. **同账号秒级跨国 IP 撕裂（Cross-Geo Telemetry Collision）**：
   - `app/telemetry.py` 在执行日活与激活上报（`post_activation_event`，上报 `app_launch` 和 `app_daily_active`）时，私自裸建 `httpx.AsyncClient`，直接经由 VPS 原生机房网络接口出网（如 US RackNerd AS36352）。
   - 在活动领取（`app/claim.py`）或设备初始化（`app/install.py`）时，系统刚在第 0 秒通过美国机房 IP 上报了该账号的设备日活，紧接着在第 1 秒却拿着同一个 JWT 凭证从台湾或香港住宅代理（21200）发起 `POST /billing/claim` 领取。
   - 上游风控引擎和阿里 WAF 抓到同一账号在 1 秒内跨越中美两地活跃，直接判定为“异地多 IP 劫持/脚本薅羊毛”，触发 3012（`unusual activity`）硬拦截。

2. **验证码求解器裸机房打点（Headless Solver Egress Leakage）**：
   - 阿里云无痕验证码虽然在 `captcha_node/solver.js` 中内置了 `process.env.HTTP_PROXY` 支持，但在 Python 编排层 `app/captcha.py:259` 启动 Node 子进程时，**未注入任何代理环境变量**。
   - 导致 Node 在 happy-dom 环境中连接阿里 CDN 与风控服务器（`o.alicdn.com`, `cf.aliyun.com`）时，全部裸走 VPS 原生机房 IP。阿里服务端在验签阶段记录的求解 IP 是机房 IP，与后续住宅出口业务提交产生指纹冲突，加剧 3012 判定。

3. **违反单一事实源与物理实体映射**：
   - 第一性原理：**1 个账号 = 1 台物理设备 = 1 个固定物理出口 IP**。
   - 现状：代码架构中存在多个私自创建的裸 HTTP 客户端，各自为战，缺乏物理层收口。

本方案旨在执行破坏性更新，将全库所有涉及账号生命周期的外呼出口（日活事件、设备安装、配置拉取、验证码求解）全面收口至 `account_client_pool` 单一事实源，彻底根除跨国 IP 撕裂与 3012 隐患。

## What Changes

- **全链路收口至 `account_client_pool`**：
  - 确立铁律：全库严禁任何业务模块私自实例化无代理的 `httpx.AsyncClient`。
  - 单账号所有外呼操作（包括 `event/report`、`client/configs`、`billing/*`、`/messages`）统一复用 `account_client_pool.get_client(account)`。
- **日活上报代理化 (`app/telemetry.py`)**：
  - 重构 `post_activation_event(profile, user_id, element, client=None)`，支持外部注入专属客户端，优先走账号绑定的大区代理出口。
- **设备安装序代理化 (`app/install.py` & `app/claim.py`)**：
  - `run_install_sequence_for_account` 与 `report_activation_events` 在调用前获取 `client = await account_client_pool.get_client(account)`，全程透传给底层网络调用。
- **验证码求解器大区出口注入 (`app/captcha.py`)**：
  - `_run_solver` 接收 `proxy: str | None`，在拉起 `node solver.js` 子进程时显式注入 `env={"HTTP_PROXY": proxy, "HTTPS_PROXY": proxy}`。
  - `get_verify_param` 接收 `account: Account | None` 或 `proxy: str | None` 参数；针对具体账号求解时，通过 `account_client_pool.resolve_proxy(account)` 获取同出口代理。
  - 后台通用预解池补充循环从在线活跃代理列表中轮询选取代理出口，杜绝裸走 VPS 机房 IP。
- **出口解析契约归一 (`app/client_pool.py`)**：
  - 强化公开方法 `resolve_proxy(account)`，作为全库单账号物理代理 URL 的权威单一事实源。

## Capabilities

### New Capabilities
- `strict-egress-convergence`: 单账号全生命周期网络外呼（遥测上报、配置拉取、验证码求解、额度查询、活动领取、模型对话）100% 严格收敛至同一物理大区出口，杜绝异地 IP 撕裂。

### Modified Capabilities
- `client-pool`: 扩展支持非 HTTPX 上下文（Node 子进程环境变量）的单账号代理字符串权威解析。

## Impact

- **代码模块**：
  - `app/client_pool.py`：公开导出单账号代理 URL 解析能力。
  - `app/telemetry.py`：废除无代理裸 Client，支持外部 Client 注入。
  - `app/install.py`：全流程复用 `account_client_pool.get_client(account)`。
  - `app/claim.py`：激活事件上报接入账号专属 Client。
  - `app/captcha.py`：Node 子进程启动注入 `HTTP_PROXY`，预解池与按需解绑定代理。
- **风控收益**：
  - 彻底终结“同账号秒级跨国 IP 异地活跃”的致命风控特征。
  - 阿里无痕验证码生成端与使用端处于同一住宅网络环境，3012 触发概率大幅衰减。
- **外部配置与依赖**：
  - 零外部依赖变更，零数据库 Schema 破坏，现有 13 账号 `assigned_region` 资产完全保留。
