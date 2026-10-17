# 01 — 总体架构

状态：主体为 2.5.18 架构基线；2026-10-17 同步用途分流、配置管理与验证码实际出口语义，其余模块变化以源码及专项文档为准。

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
└──────────────┘    │  CaptchaManager · Install · Fingerprint        │
                    └───┬──────────────┬─────────────────────────────┘
                        │              │
                 zcode.z.ai        api.z.ai /
                 (Plan 通道+       open.bigmodel.cn
                  验证码/领取)      (API Key 通道)
```

## 2. 技术栈

| 层 | 选型 | 说明 |
|----|------|------|
| 运行时 | Python 3.11+ | 继承 zcode2api 底座 |
| Web | FastAPI + Uvicorn | 异步网关，SSE 流式透传 |
| HTTP 客户端 | httpx | 连接池 + 流式 + 超时细粒度控制 |
| 存储 | SQLite (WAL) | 账号池 / 设置（凭证明文）；领取历史待实现 |
| 验证码 | Node + happy-dom 子进程 | 复用 zcode2api 方案：无浏览器运行阿里云无痕 SDK |
| 前端 | 原生 JS + 轻量模板 | 继承 zcode2api 后台骨架，扩额度/领取面板 |
| 测试 | pytest + pytest-asyncio + respx | 单元 + 契约；Mock 上游见测试文档 |
| 部署 | Linux + systemd；源码与虚拟环境 | 保留既有服务、目录和数据，真实参数不入库 |

## 3. 模块清单与职责

```
zcode-hub/
├── app/
│   ├── main.py            # FastAPI 工厂 + lifespan（额度监控 / 验证码池 / 启动安装序）
│   ├── settings.py        # 环境变量（.env）
│   ├── models.py          # Account、Status；选号/回退/billing 门在账号对象上
│   ├── store.py           # SQLite WAL：账号 CRUD、round-robin select、设置 KV
│   ├── region_assign.py   # 账号大区发牌：入池即时分配 + 存量补配（读注册表避空区）
│   ├── egress_policy.py   # 不可变 chat/claim 策略、同区固定身份与完整对象校验
│   ├── client_pool.py     # 账号/用途/实际出口独立客户端、操作引用、物理退役与用途断路器
│   ├── routes/gateway.py  # /v1/messages + /v1/chat/completions 调度与错误分类
│   ├── routes/admin_api.py
│   ├── routes/pages.py
│   ├── openai_compat.py   # OpenAI ↔ Anthropic 翻译（含 SSE）
│   ├── agent.py           # 上游请求构建（身份头 / 透传头过滤 / 通道选择）
│   ├── identity.py        # 身份头仿真（每账号 DeviceProfile）
│   ├── fingerprint.py     # 每账号成套桌面 SKU（一号一台生成设备）
│   ├── install.py         # 全局 + 按账号安装序（全程透传账号 Client）
│   ├── quota.py           # billing/current+balance+usage 刷新
│   ├── claim.py           # billing/preview + claim 与出网代理物理错峰串行门禁（ClaimPacingGate）
│   ├── captcha.py         # 实际出口/代次亲和池；仅可选 Plan chat 预热，claim 按需求解；退避、单飞和定向清池
│   ├── evidence.py        # 落盘证据：请求与求解事件（data/evidence.db，白名单字段，保留 ≥7 天）
│   ├── oauth.py           # zai server-mediated CLI 流
│   ├── auth_admin.py      # 后台 / 网关鉴权（后台失败节流）
│   └── reqlog.py          # 内存环形请求日志
├── captcha_node/          # Node + happy-dom solver.js
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

出口配置入口为 `/admin/settings`。数据库完整策略优先于四项用途环境默认；Store 加载后冻结 active，保存/恢复只改变 saved，重启生效。对话/探针走 chat；领取、激活、JWT 额度、账号安装/登录及哨兵走 claim；匿名启动和非 ZCode JWT 链路保留旧全局策略。固定领取身份独立于对话 admission，参与原排空协议；保存删除但未重启不能提前释放引用。接口及配置项见 [出口配置管理](../egress_configuration.md)。

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
      3012/405 风控拒绝   → 保留账号隔离规则及 chat 出口断路器，换号；不推断永久封禁或 claim 同样拒绝
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
       │    ├─ 1005 名额用完 → 标记 claim_blocked_until（优先采用上游 data.plan.ends_at * 1000 + 5~60s Jitter，未返回时回退北京时间次日 00:05 + 0~300s Jitter 离散避让；消除虚假重置承诺，提示时序避让与下线自动跳过）
       │    ├─ 1003 已领取 / 1002 结束 → 记失败文案
       │    ├─ 3007 验证码失败 → 仅清失败实际出口/代次水库，本次止损并保留待领项
       │    └─ 401 → 标 INVALID
       └─ 领取成功 → 透传 starts_at / ends_at / server_time（秒→毫秒）并刷新额度
无独立 ClaimScheduler 轮询；纯 API Key 账号跳过领取。
同次自动领取的激活、preview、验证码与 POST 持有同一 claim 操作；25 秒 billing 超时逐请求生效。显式 claim direct/proxy 下人工选择套餐使用服务端验证码，旧 /claim/manual 返回 409 / claim_verification_route_mismatch；inherit 保留浏览器路径。无活动/已持有继续短路，1003 只证明已领取业务通路，不代表新额度到账。
前台界面与服务端协同保障（v2.5.18 ~ v2.6.5）：
- 底层领取与状态同步拆分为单一职责原子原语（`format_claim_blocked_time`、`_norm_plan_id`、`filter_unclaimed_plans`、`sync_account_claimable_plans`、`sync_pool_claimable_plans`、`_ensure_claimable_account`、`_post_claim`）拼装组合；`preview_plans` 探查完成瞬间即调用 `sync_account_claimable_plans` 与 `sync_pool_claimable_plans` 将未持有活动持久化至当前账号及全池可计费账号的 `claimable_plans`；当账号已无未持有活动或活动全部下线时，自动自愈清除陈旧避让戳；已持有套餐优先放行返回 1003 已领而不被 1005 避让误伤；
- `ClaimError` 结构化携带上游 `code` 与 `next_at`（1005 时提取 `data.plan.ends_at * 1000`），格式化时间精确输出今日/次日/具体日期消除跨天歧义；前端 `claimToast` 自动展示恢复倒计时与客观避让提示；
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

**大区发牌**：上述四类入口全部经 `Store.add_account`，该函数在入池时调用 `_assign_region()` 即时分配大区，因此任一入口进入的账号都带稳定出口归属。发牌由 `app/region_assign.pick_region()` 实现：读当前出口注册表，只在**确有活跃出口**的 P0（HK/JP/TW）与 P1（SG/US）中发牌，取「实际占比相对目标占比欠额最大」者，避免总量变化时的全量重排漂移；P2（KR/EU/OTHER）不纳入发牌。已有大区的账号一律保留（幂等），注册表不可读或无可发牌区时留空，由启动补配重试。

`app/main.py` 的 `_backfill_regions()` 与既有 `_backfill_fingerprints` / `_backfill_install_ids` 并列，在 lifespan 中为「凭证有效但缺大区」的存量账号补发。备份导出携带 `assigned_region`，导入以备份为权威来源覆盖入池发牌结果，保证恢复不改变既有出口归属。

### 4.7 按用途操作保持网络出口一致（Egress Convergence）

- **账号身份与用途出口**：保留每账号设备身份和已有大区；客户端按账号、用途、实际代理及托管身份隔离。同次操作固定出口，显式 chat direct / claim 同区代理允许上游看到不同 IP，不承诺消除账号风控。
- **用途离线门禁**：要求代理的用途遇到来源/身份/退役/断路器异常时明确失败，禁止静默直连或跨区；claim 失败不阻止具有对话资格的 direct chat。显式 direct 是配置选择。
- **验证码实际出口亲和**：token/Singleflight 按实际代理及代次匹配，producer 继承父操作；已准入 None 明确是 direct，不能重新按账号地区选路。3007 只清失败实际出口/身份，direct 不能清全部池。
- **仅为可选 Plan chat 预热**：周期与消费补货使用有效 chat 策略，direct 为唯一 None 目标，按该实际水库水位判断并在最低水位已满时停止；claim 按需求解，不加入常驻预热或触发 claim-only 消费回补。每出口水位、CPU 槽和预算保持原约定。
- **端口级求解退避**：同端口连续 `CAPTCHA_PORT_FAIL_THRESHOLD`（默认 2）次求解失败即退避 `CAPTCHA_PORT_COOLDOWN`（默认 60s × 0.75~1.5 抖动）。退避期内水库命中照常返回，空仓则立即以 `solver_port_backoff` 失败换号，不新建 Node、不占求解槽，也不改写出口断路器（后者只能由真实网络/WAF 证据推进）。
- **稳定出口绑定**：继承区域代理保留既有稳定映射；显式 claim 固定 `(port,node_id,epoch)`，失败不自动换槽。chat draining 不自动拒绝有效 claim-only 绑定，物理退役关闭所有用途的新准入并等待固定/操作/producer/客户端/token 引用排空。
- **求解器代理与安全分类**：Node 只使用客户端实际出口注入的 `HTTP_PROXY` / `HTTPS_PROXY`，不继承另一环境出口。默认 stderr 输出有界 `SOLVER_DIAG=` 白名单摘要；Python 不透传原始 stderr 或参数片段。仅有本地求解失败不证明模型通道网络故障。
- **网关人机校验账号级容灾避让**：单账号因代理瞬时抖动引发人机校验失败时，严格遵守栅栏原则，将故障隔离在账号内部；支持 API Key 回退的账号原地平滑降级，纯 Plan 账号触发 `next_account("captcha_error")` 自动切换下一个健康大区可用账号，绝不向客户端抛出裸 500 熔断。

验证码单飞由按出口受管的 producer task 持有配置、绝对预算与 Node 生命周期，等待者通过 `shield` 等待。同批前台仍广播成功结果，补货专用结果只入池一次；前台消费的结果不同时存入水库。每次 Node 启动到退出/回收占一个 CPU 槽，配置和退避不占槽。前台需求覆盖整个空池等待生命周期，后台只在空闲容量准入；已运行后台可结束，后续尝试让位。周期和消费补货按出口合并，不增加无限等待队列。`close()` 取消并 await 自己拥有的周期、补货和 producer，kill/wait 子进程后释放槽，重复关闭安全。

每个 producer 自创建起使用 `CAPTCHA_SOLVE_TIMEOUT`（默认25秒，依据实测失败耗时 13–20s 收敛，避免白占唯一求解槽），涵盖配置、排队、执行及退避；删除原10秒排队切片。不同出口与有限挑战重解有独立预算。请求内同出口终态失败仅在有效亲和池为空时复用，不消耗新的实际账号尝试；有效补货、其它出口与 Key 回退仍可服务，不持久化或跨请求缓存失败。模型连接、429退避与 SSE 不受求解预算限制。

客户端池记录每个实例实际出口，验证码、上游和精准失效共用该出口。健康查询与客户端创建不占 HALF-OPEN 资格；gateway、claim billing/activation、quota、install 在实际网络边界配对 `begin_request/end_request`。取消和本地错误中性释放，只有明确网络/WAF证据推进对应用途失败；取消半开请求释放恢复资格。quota 三个并发查询共用一次准入并聚合结算；流式资格由 `_Upstream` 成功/传输失败/关闭回收。代理用途的离线禁直连门禁继续生效，客户端不采用环境隐式代理。

### 4.8 周期出口探测与生产发布

`scripts/mihomo_auto_probe.py` 在独占 `flock` 内执行恢复、订阅读取、扫描与发布。扫描复用已安装 Mihomo 二进制，但使用私有临时目录、独立 Unix Controller 和一条回环 mixed 监听；所有组切换、模型筛选与 Trace 均经该实例，HTTP 客户端不继承环境代理。启动就绪限 10 秒，扫描预算为 `10 + 12 × 候选数` 秒；正常、超时、取消与 SIGTERM 都回收子进程，terminate 等待 5 秒后升级 kill/wait。子进程输出丢弃到系统空设备，摘要仅记录阶段、计数、状态码和耗时。

首次采用一次维护暂停，统一部署操作引用协议和固定回环 mixed 槽；长期只有一个生产实例。基础候选完整复制已有配置，保留未知监听、DNS、规则和鉴权。每槽独立 select 组只匹配 file 提供器中一个带精确端口前缀的节点或 reject 占位，空槽不得直连。日常只刷新提供器，禁止整份 `/configs` 加载、生产 stop/restart 或删除连接；基础配置哈希和运行启动身份为不变前提。

每来源的最后完整有效节点快照保存在版本化 `node_lifecycle.json`。成功差分只移除该来源引用，超时、破损或无法证明完整的空响应保留旧快照；配置中明确移除来源才撤销该来源。完整连接定义只剔除显示名再规范化求摘要，未知连接扩展和值类型也参与身份；改名和排序不影响身份，认证/TLS/传输改变按新身份处理。旧部分指纹迁移匹配完整定义，歧义停止。成员、健康、准入和物理占槽分开；观察超时不删除成员或回收槽。无同区替代时保留仍健康旧节点为 fallback，失效沿用地域离线，禁止跨区/直连。

`AccountClientPool.acquire_operation()` 在同一锁内读取完整注册表、取得客户端和记录实际身份引用，嵌套同账号操作透传上下文。注册表沿用 `updated_at`/`regions`，增加内容摘要 generation 及每槽 node_id/epoch/region/admission；读内容而非仅信 mtime。有效绑定不因其它节点新增而重建；失效缓存撤出新业务入口，旧操作继续完成。网关引用覆盖验证码、重试、正文/SSE，交给 `_Upstream.close()` 实际关闭后释放；breaker 的 begin/end 只结算网络结果。idle 和账号关闭不能关闭在途客户端。

验证码 producer 自己持有引用，等待者取消不提前排空；任务在协程开始前取消时，由等待者收尾或 manager 关闭在任务结束后幂等释放，不能只依赖 producer 的 finally。已准入父操作可在退役后派生同身份 producer，新补货和无父引用的求解仍关闭准入。延后补货携带入队身份/代次，旧 token 定向清理，端口复用不继承 token、client 或 breaker。领取排队、激活、安装、额度及全局配置均纳入操作边界；显式固定代理不自动改写，未解除时阻止槽回收。

新增替代先写完整提供器并核验所有槽身份；提供器、生命周期、scan、ENV 和注册表逐文件替换，注册表最后提交。file 提供器 interval=0 仍有 watcher，候选校验及私有恢复副本必须在首次落盘前完成。多文件不是原子事务：`.mihomo-publish` 保留固定集合旧/新哈希、权限/缺失状态、基础哈希及进程启动身份；提交前恢复旧快照并撤回新增，提交后向前核验，不重新开放旧准入。外部冲突/身份变化保留记录并失败。

应用 lifecycle 启动本地轻量排空协调任务，私有消费者注册和确认绑定 PID、系统启动标识、boot_id、协议、本次 request_id、generation 与槽身份。无前台流量也清理旧缓存/token并推进确认。物理删除是独立后置阶段：全部当前消费者关闭准入且操作/producer/cache/token/固定引用归零，真实 Mihomo 连接可归属且归零后才刷新为 reject，持久记账后下一轮允许同区新 epoch 复用。未知消费者、过期确认及连接无法归属都阻止回收；有界等待留下 pending_retirement，不杀业务、不回滚已提交路由。

无候选、无探测账号、设施失败、全来源未验证或零可发布出口均保留最后完整路由，不宣称旧快照健康。节点筛选沿用历史 `fake_param` 请求，有效验证码和正常模型终态由实际业务另验；实际额度和公开错误协议不由更新器修改。首次维护暂停与日常连续性分别记录，接口成功和监听打开均不代替真实 TCP/SSE 证据。

节点可达性判定复用网关 `_classify_business_error` 归一化：额度耗尽（`code=1005`，归一为 402）、限流（429）与验证码挑战（403）等业务响应都证明出口链路端到端打通（连接建立、上游受理、鉴权通过），必须计为可达；只有连接失败、TLS 失败、超时与归一后仍为 5xx 的上游故障才计入节点失败。探测身份取池内首个凭证有效且启用的账号，不要求处于可服务状态——额度耗尽账号的凭证依然有效，且 `max_tokens=1` 探测请求不产生额度消耗，是成本最低的探测身份；仅当池内无任何有效凭证时才视为无探测前提并整轮未提交。单节点业务请求超时取 `MIHOMO_PROBE_TIMEOUT`（默认 10 秒），`trace` 定位超时独立为 3 秒；单节点在计入失败前允许一次网络重试，首次抖动不判死节点。

## 5. 与来源项目的边界

| 能力 | 取自 | 不采用的部分 |
|------|------|--------------|
| 网关底座、captcha、admin 骨架 | zcode2api | 明文导出（换 .zsb）、简陋余额轮询（换 quota v2） |
| 额度模型、领取、enc:v1、.zsb、切换器 | zcode-switch | Tauri GUI / 托盘 / Win32 进程管理 / i18n 机制 |
| 池化故障转移设计、翻译层形态、签名/路由协议知识 | zcode-api（含本 fork 已实现的号池补丁） | TypeScript 运行时（翻译层用 Python 重写）、其无许可证代码（只借鉴设计与协议） |
