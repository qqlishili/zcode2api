# 01 — 总体架构

状态：与 2.5.18 实现对齐（无 pool.py / gateway 子包 / bundle.py / zclient.py / `/v1/responses`）。

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

后台任务按 `quota_refresh_interval`（默认 60s，周期附加 `±10%` 随机抖动防机器时钟特征，meta 表可改，0 = 关）刷新池内 JWT 账号；冷却 / invalid / 风控禁用 / 手动停用不打 billing。单轮巡检（`QuotaMonitor.check_once`）前置共享 `last_checked_at` 去抖跳过近期（`< min(0.8 * interval, BILLING_REFRESH_MIN_INTERVAL)`）已被网关对话 `_safe_refresh` 或手动刷新过的账号，并对待刷账号随机打散顺序、以 `max_concurrency=2` + `stagger_sec=0.4`（`0.3~0.5s` 离散间隔）错峰平滑请求，彻底消除整点脉冲尖峰。每个账号探测 `billing/current` + `billing/balance` + `usage`。上游 `billing/balance` 响应 200 且合法解析时以物理事实强制同步覆盖：若 `balances` 为空（套餐自然到期或未开通），彻底清空 `account.quota` 避免残留历史快照，并置为 EXHAUSTED（原因 `no_active_quota` / `额度已用完`）；若返回非空窗口，优先深度提取 `capabilities` 中的 `model:<id>` 协议标签（若含 `flash` 则无条件归一化为 `GLM-5.3-Flash`，并兜底检查 `show_name` / `model` 是否含 `flash`），过滤 `expires_at <= now` 的过期残留窗口后，确保周期性日窗与异名活动赠送池（如 `GLM-5.3-Flash 体验版`）安全累加合并至标准的 `GLM-5.3-Flash` 单一窗口；耗尽判定优先以主免费池模型 `DEFAULT_MODEL`（`GLM-5.3-Flash`）的 `remaining <= 0` 判定 EXHAUSTED（避免闲置的 `GLM-5.3` 余量阻塞耗尽状态或引发误恢复；无主模型窗口时回退按全窗口判定）；额度恢复且非冷却 → 回 ACTIVE。废 JWT / 风控禁用绝不能因额度数字复活 Plan 通道。成功对话后的 billing 刷新有 `BILLING_REFRESH_MIN_INTERVAL`（默认 60s）去抖。

### 4.4 活动领取

```
入池（Web/CLI）或后台「领取」：
  JWT 且 allows_billing →
  ├─ 处于 1005 避让期（is_claim_blocked） → 直接短路跳过，不打上游 HTTP，不打码
  └─ 未避让 → GET billing/preview（Bearer JWT + 每账号 X-Device-Mid）
       ├─ 无可领套餐 → 结束
       ├─ 有可领 → 取验证码 verifyParam → POST billing/claim
       │    ├─ 1005 名额用完 → 标记 claim_blocked_until（优先采用上游 data.plan.ends_at * 1000 + 5~60s Jitter，未返回时回退北京时间次日 00:05 + 0~300s Jitter 离散避让）
       │    ├─ 1003 已领取 / 1002 结束 → 记失败文案
       │    ├─ 3007 验证码失败 → 换码重试一次
       │    └─ 401 → 标 INVALID
       └─ 领取成功 → 透传 starts_at / ends_at / server_time（秒→毫秒）并刷新额度
无独立 ClaimScheduler 轮询；纯 API Key 账号跳过领取。
前台界面与服务端协同保障（v2.5.18 ~ v2.6.4）：
- 底层领取与状态同步拆分为单一职责原子原语（`_norm_plan_id`、`filter_unclaimed_plans`、`sync_account_claimable_plans`、`sync_pool_claimable_plans`、`_ensure_claimable_account`、`_post_claim`）拼装组合；`preview_plans` 探查完成瞬间即调用 `sync_account_claimable_plans` 与 `sync_pool_claimable_plans` 将未持有活动持久化至当前账号及全池可计费账号的 `claimable_plans`（哪怕下一步 `POST /billing/claim` 抛 3012/1005 异常，管理面板也立即可见待领活动 Icon 与滑块手动补领入口；领取成功或命中 1003 则自动剔除）；
- `ClaimError` 结构化携带上游 `code` 与 `next_at`（1005 时提取 `data.plan.ends_at * 1000`），前端 `claimToast` 自动展示预计恢复倒计时；
- 全链路领取成功（哨兵自动补领、入池自动领取、后台一键领取、浏览器滑块手动领取）统一接入 `notify_claim_outcomes` / `schedule_claim_notification` 异步推送 Bark 战报；
- 前端视图层差集过滤已持有套餐（claimStripHtml），入口/切换/提交三道门禁拦截已持账号；
- 彻底阻断 Toast 自激呼起弹窗死循环，无感验证通过后受控单次自动提交；
- 服务端求解器采用 `SplitMix32` 会话种子驱动多维深度混淆（Canvas 轨迹非零光栅、Audio 逐采样微噪、84 种桌面组合），且 `hostinfo` 支持 Windows 10/11 三段式内核版本（`10.0.xxxxx`）归一。
```

### 4.5 Bark 活动监控与智能领券哨兵（Sentinel）

```
Sentinel 后台巡检循环（默认 1800 秒 ± 10% 抖动，具备北京时间 00:00 跨零点破冰感知，单例持有强引用防 GC）：
  ├─ 调度时钟（_next_sleep_seconds）：
  │    ├─ 跨北京时间 00:00:00 零点自动截断睡眠至 00:00:15 ~ 00:01:00 准时唤醒破冰，消除最多 30 分钟的跨日盲等；
  │    ├─ 凌晨 00:00 ~ 00:10 黄金窗口内若上游尚未上架今日新活动，自动收敛为 90 ~ 150 秒快速兜底复查，发现后立即恢复常规间隔；
  │    └─ 池内存在 1005 避让账号（claim_blocked_until）时，自动对齐最早解封时间 + 5~20s 唤醒补领。
  ├─ 巡检流程拆分为小步骤原子方法组合（_prune_expired_cooldown → _billable_candidates → _probe_upstream_plans → _run_catchup_claims / _claim_new_plans_across_pool + _deliver_and_commit_new_plans）
  ├─ 动态挑选 1 个可计费（allows_billing，含 ACTIVE 与 EXHAUSTED）JWT 账号作为探针（单轮上限 3 次，遇 401 标记失效并轮换下一位，防死锁）
  ├─ 探针前置上报当日激活事件（report_activation_events，5s 超时隔离）以解锁跨日活动投放资格，随后调用 preview_plans（探查结果自动广播同步至全池未持有账号的 claimable_plans）
  ├─ 差量比对 SQLite meta 表已见套餐（sentinel_seen_plans，探针候选在同优先级账号间随机打散轮转分摊压力）：
  │    ├─ 无全新 plan_id：执行存量漏领自动补领闭环（Catch-up）——对比当期活动/账号待领列表与已持有套餐，为漏领账号（尤其是 EXHAUSTED 耗尽账号）自动补领并异步推送 Bark 补领成功战报，失败账号进入 6 小时冷却避让防死循环撞击 3012
  │    └─ 发现全新 plan_id：
  │         ├─ 先通过 sync_pool_claimable_plans 将新活动写入全池未持账号的 claimable_plans；sentinel_auto_claim 开启时按单并发顺序串行 + 同优先级随机打散 + 0.6~1.5s 离散随机抖动延时，为全池可计费 JWT 账号触发 auto_claim_all_plans（失败项准确记录原因并直接记入 6 小时冷却）
  │         ├─ 汇总活动详情与全池抢领战报（含已持有账号与失败原因识别），格式化构建消息
  │         ├─ POST JSON 投递 Bark（POST https://api.day.app/push，超时 12s + 2 次瞬态退避重试，4xx 立即熔断防封 IP）
  │         └─ 时序安全落库：仅当 Bark 投递确认成功（或未配置 Bark / 连续 3 轮失败兜底）后，才将新 plan_id 持久化写入 sentinel_seen_plans（彻底根除单次 APNs 超时导致的静默丢单）
```

### 4.6 凭证进入池内的路径

| 路径 | 流程 |
|------|------|
| Web OAuth | `POST /admin/api/login/start` → 浏览器授权官方 callback → `GET login/poll` ready 入池 JWT 并后台刷新额度与自动领取套餐 |
| Web / CLI 粘贴 | `POST /admin/api/accounts` 或 `cli.py add-account`（JWT 或 Key） |
| JSON 导入 | `GET/POST /admin/api/export|import` 或 `cli.py export/import`（明文 name/mode/secret） |

入池后：按账号安装序（configs + 激活事件，批量入池经 `_install_lock` 串行错峰 `0.3~0.8s`）+ JWT 自动领取（经 `_auto_claim_lock` 串行错峰 `0.6~1.5s`）。CLI 与 Web 同序。官方 callback 在 `zcode.z.ai`，hub 只 poll 结果，收不到授权 code。

## 5. 与来源项目的边界

| 能力 | 取自 | 不采用的部分 |
|------|------|--------------|
| 网关底座、captcha、admin 骨架 | zcode2api | 明文导出（换 .zsb）、简陋余额轮询（换 quota v2） |
| 额度模型、领取、enc:v1、.zsb、切换器 | zcode-switch | Tauri GUI / 托盘 / Win32 进程管理 / i18n 机制 |
| 池化故障转移设计、翻译层形态、签名/路由协议知识 | zcode-api（含本 fork 已实现的号池补丁） | TypeScript 运行时（翻译层用 Python 重写）、其无许可证代码（只借鉴设计与协议） |
