# 01 — 总体架构

状态：与 2.5.16 实现对齐（无 pool.py / gateway 子包 / bundle.py / zclient.py / `/v1/responses`）。

## 1. 系统定位

zcode-hub 是一个**自托管服务**，同时承担两个角色：

1. **API 供给端（网关）**：把池内 ZCode 账号的 Coding Plan / API Key 额度，以 Anthropic Messages（`/v1/messages`）和 OpenAI Chat Completions（`/v1/chat/completions`）暴露给任意 agent。
2. **账号运营端（控制台）**：账号池增删、额度监控、限时套餐领取、明文 JSON 导入导出、OAuth CLI 入池。没有本机 `~/.zcode` 快照切换，也没有 `.zsb` 封包。

```
                    ┌────────────────────────────────────────────────┐
   Claude Code ────▶│  Gateway  /v1/messages  /v1/chat/completions   │
   Codex CLI  ─────▶│           /v1/models                           │
   OpenAI agent ───▶│       （store.select 轮换 + 故障转移 + 流式）   │
                    └───────────────┬────────────────────────────────┘
                                    │
┌──────────────┐    ┌───────────────▼────────────────────────────────┐
│ 管理后台 Web │───▶│                FastAPI 核心进程                 │
│ /admin/*     │    │  Store(SQLite) · Quota · Claim · OAuth         │
└──────────────┘    │  CaptchaManager(jsdom) · Install · Fingerprint │
                    └───┬──────────────┬─────────────────────────────┘
                        │              │
                 zcode.z.ai        api.z.ai /
                 (Plan 通道+       open.bigmodel.cn
                  验证码/领取)      (API Key 通道)
```

## 2. 技术栈

| 层 | 选型 | 说明 |
|----|------|------|
| 运行时 | Python 3.12+ | 继承 zcode2api 底座 |
| Web | FastAPI + Uvicorn | 异步网关，SSE 流式透传 |
| HTTP 客户端 | httpx | 连接池 + 流式 + 超时细粒度控制 |
| 存储 | SQLite (WAL) | 账号池 / 设置 / 领取历史；单机自托管 |
| 验证码 | Node + jsdom 子进程 | 复用 zcode2api 方案：无浏览器运行阿里云无痕 SDK |
| 前端 | 原生 JS + 轻量模板 | 继承 zcode2api 后台骨架，扩额度/领取面板 |
| 测试 | pytest + pytest-asyncio + respx | 单元 + 契约；Mock 上游见测试文档 |
| 部署 | Docker / docker-compose；裸机用 supervisor | tebi 容器无 systemd，用 supervisor 约定 |

## 3. 模块清单与职责

```
zcode-hub/
├── app/
│   ├── main.py            # FastAPI 工厂 + lifespan（额度监控 / 验证码池 / 启动安装序）
│   ├── settings.py        # 环境变量（.env）
│   ├── models.py          # Account、Status；选号/回退/billing 门在账号对象上
│   ├── store.py           # SQLite WAL：账号 CRUD、round-robin select、设置 KV
│   ├── routes/gateway.py  # /v1/messages + /v1/chat/completions 调度与错误分类
│   ├── routes/admin_api.py
│   ├── routes/pages.py
│   ├── openai_compat.py   # OpenAI ↔ Anthropic 翻译（含 SSE）
│   ├── agent.py           # 上游请求构建（身份头 / 透传头过滤 / 通道选择）
│   ├── identity.py        # 身份头仿真（每账号 DeviceProfile）
│   ├── fingerprint.py     # 每账号成套桌面 SKU（一号一台生成设备）
│   ├── install.py         # 全局 + 按账号安装序
│   ├── quota.py           # billing/current+balance+usage 刷新
│   ├── claim.py           # billing/preview + claim
│   ├── captcha.py         # 求解器编排 + 预热池
│   ├── oauth.py           # zai server-mediated CLI 流
│   ├── auth_admin.py      # 后台 / 网关鉴权（后台失败节流）
│   └── reqlog.py          # 内存环形请求日志
├── captcha_node/          # Node + jsdom solver.js
├── frontend/              # 后台静态页（accounts / settings / login）
├── tests/
│   ├── unit/
│   ├── integration/
│   └── mock_upstream/
├── cli.py                 # serve / login / add-account / quota / export / import
└── docs/
```

### 模块间依赖规则

- 选号在 `store.select` + `Account.is_selectable`，没有独立 `pool.py`
- `routes/gateway.py` 直接改账号状态（INVALID / DISABLED / EXHAUSTED / COOLING）并 `store.update_account`
- `quota.py` / `claim.py` 复用 `captcha.py` 取验证码 token
- 删号后 `update_account` 拒绝写回，避免后台任务 `INSERT OR REPLACE` 救活已删行

## 4. 核心流程

### 4.1 网关请求（含账号池故障转移）

```
client → 鉴权 → [循环: attempt ≤ MAX_ACCOUNT_ATTEMPTS=5]
   1. 选号：优先命中同会话粘性绑定账号（固定窗口 600s / 单轮最多 20 次请求，禁止滑动续期以防单号过热；
      保多轮对话缓存与 thinking 签名连续），到期/满步数/不可用/首轮则走 store.select
      （round-robin 单调游标；跳过 exhausted / 冷却中 / invalid / 风控 disabled / 手动停用 / 目标模型无余量；
      优先选择显式持有目标模型余量的账号）
   2. 并发槽：该号在飞 ≥ account_concurrency 则跳号（不排队；跳过不计 attempt）
   3. 构建上游请求（每账号指纹 + 透传头过滤 + Plan/Key 通道）
   4. 响应分类：
      ok                 → 透传/翻译返回；释放槽在流关闭时
      402/额度关键词      → EXHAUSTED，换号
      429                → 不冷却，原地等 Retry-After 后重试（等待期间放槽）；重试耗尽换下一个账号
      401/403(非验证码)   → INVALID，换号
      3012/405 风控       → ban_for_risk（DISABLED），换号
      403(验证码挑战)     → 刷新验证码原账号重试（≤3 次）
      5xx                → 重试后 COOLING，换号
      其它 4xx           → 原样回传客户端
   5. 池内无可选号 / 全满 → 503 no_available_account
```

### 4.2 账号健康状态机

```
            ┌─────────┐  额度耗尽（quota 刷新探测恢复）    ┌───────────┐
   login───▶│ ACTIVE  │◀──────────────────────────────────│ EXHAUSTED │
            │         │  5xx/连接失败（冷却 300s）         └───────────┘
            │         │◀──────┐        ┌─────────┐
            │         │       ├────────│ COOLING │
            │         │       └───────▶└─────────┘
            │         │  401/403 非验证码        ┌─────────┐
            │         │────────────────────────▶│ INVALID │ （直到重新登录）
            │         │  3012/405 风控           ┌─────────┐
            │         │────────────────────────▶│ DISABLED│ （手动启用）
            └─────────┘                         └─────────┘
   成功响应可清除 COOLING/EXHAUSTED（不得洗掉 INVALID/风控 DISABLED）。
   免费赠送池模式下不启用 OAuth 0 余额 API Key 回退，INVALID/DISABLED 账号直接隔离下线。
```

参数：`MAX_ACCOUNT_ATTEMPTS=5`、`COOLING_SECONDS=300`（仅 5xx/连接失败）、`RETRY_429_TIMES=5`、`ACCOUNT_CONCURRENCY=2`（0 = 不限）。额度耗尽由后台 `quota` 刷新探测恢复，没有独立 `exhausted_retry_seconds`。

### 4.3 额度监控

后台任务按 `quota_refresh_interval`（默认 60s，meta 表可改，0 = 关）刷新池内 JWT 账号；冷却 / invalid / 风控禁用 / 手动停用不打 billing。每个账号探测 `billing/current` + `billing/balance` + `usage`。上游 `billing/balance` 响应 200 且合法解析时以物理事实强制同步覆盖：若 `balances` 为空（套餐自然到期或未开通），彻底清空 `account.quota` 避免残留历史快照，并置为 EXHAUSTED（原因 `no_active_quota` / `额度已用完`）；若返回非空窗口，优先深度提取 `capabilities` 中的 `model:<id>` 协议标签（若含 `flash` 则无条件归一化为 `GLM-5.3-Flash`，并兜底检查 `show_name` / `model` 是否含 `flash`），过滤 `expires_at <= now` 的过期残留窗口后，确保周期性日窗与异名活动赠送池（如 `GLM-5.3-Flash 体验版`）安全累加合并至标准的 `GLM-5.3-Flash` 单一窗口；耗尽判定优先以主免费池模型 `DEFAULT_MODEL`（`GLM-5.3-Flash`）的 `remaining <= 0` 判定 EXHAUSTED（避免闲置的 `GLM-5.3` 余量阻塞耗尽状态或引发误恢复；无主模型窗口时回退按全窗口判定）；额度恢复且非冷却 → 回 ACTIVE。废 JWT / 风控禁用绝不能因额度数字复活 Plan 通道。成功对话后的 billing 刷新有 `BILLING_REFRESH_MIN_INTERVAL`（默认 60s）去抖。

### 4.4 活动领取

```
入池（Web/CLI）或后台「领取」：
  JWT 且 allows_billing →
  ├─ 处于 1005 避让期（is_claim_blocked） → 直接短路跳过，不打上游 HTTP，不打码
  └─ 未避让 → GET billing/preview（Bearer JWT + 每账号 X-Device-Mid）
       ├─ 无可领套餐 → 结束
       ├─ 有可领 → 取验证码 verifyParam → POST billing/claim
       │    ├─ 1005 名额用完 → 标记 claim_blocked_until（北京时间次日 00:05 + 0~300s Jitter 离散避让）
       │    ├─ 1003 已领取 / 1002 结束 → 记失败文案
       │    ├─ 3007 验证码失败 → 换码重试一次
       │    └─ 401 → 标 INVALID
       └─ 领取成功 → 刷新额度
无独立 ClaimScheduler 轮询；纯 API Key 账号跳过领取。
前台界面与服务端协同保障（v2.5.16）：
- 前端视图层差集过滤已持有套餐（claimStripHtml），入口/切换/提交三道门禁拦截已持账号；
- 彻底阻断 Toast 自激呼起弹窗死循环，无感验证通过后受控单次自动提交；
- 服务端求解器硬件指纹脱敏（RTX 3060 D3D11 + Win32），剥离虚拟机特征对抗 3012 风控。
```

### 4.5 Bark 活动监控与智能领券哨兵（Sentinel）

```
Sentinel 后台巡检循环（默认 1800 秒，单例持有强引用防 GC）：
  ├─ 动态挑选 1 个活跃健康 JWT 账号作为探针（单轮上限 3 次，遇 401 标记失效并轮换下一位，防死锁）
  ├─ 调用 preview_plans（无验证码、只读零开销）
  ├─ 差量比对 SQLite meta 表已见套餐（sentinel_seen_plans），无新增则休眠
  └─ 发现全新 plan_id：
       ├─ sentinel_auto_claim 开启时：单并发顺序串行 + 0.6~1.5s 离散随机抖动延时，
       │  为全池可用 JWT 账号触发 auto_claim_all_plans（严格避开验证码池与 Solver 拥堵）
       ├─ 汇总活动详情与全池抢领战报，格式化构建消息
       ├─ POST JSON 投递 Bark（POST https://api.day.app/push，超时 8s，全量异常捕获）
       └─ 时序安全落库：投递完成后将新 plan_id 持久化写入 sentinel_seen_plans（防静默丢单）
```

### 4.6 凭证进入池内的路径

| 路径 | 流程 |
|------|------|
| Web OAuth | `POST /admin/api/login/start` → 浏览器授权官方 callback → `GET login/poll` ready 入池 JWT 并后台刷新额度与自动领取套餐 |
| Web / CLI 粘贴 | `POST /admin/api/accounts` 或 `cli.py add-account`（JWT 或 Key） |
| JSON 导入 | `GET/POST /admin/api/export|import` 或 `cli.py export/import`（明文 name/mode/secret） |

入池后：按账号安装序（configs + 激活事件）+ JWT 自动领取。CLI 与 Web 同序。官方 callback 在 `zcode.z.ai`，hub 只 poll 结果，收不到授权 code。

## 5. 与来源项目的边界

| 能力 | 取自 | 不采用的部分 |
|------|------|--------------|
| 网关底座、captcha、admin 骨架 | zcode2api | 明文导出（换 .zsb）、简陋余额轮询（换 quota v2） |
| 额度模型、领取、enc:v1、.zsb、切换器 | zcode-switch | Tauri GUI / 托盘 / Win32 进程管理 / i18n 机制 |
| 池化故障转移设计、翻译层形态、签名/路由协议知识 | zcode-api（含本 fork 已实现的号池补丁） | TypeScript 运行时（翻译层用 Python 重写）、其无许可证代码（只借鉴设计与协议） |
