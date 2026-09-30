# 多活动套餐归一化展示、待领活动感知与双轨验证优化设计方案（已审查修订版）

## 1. 背景与核心问题诊断

结合生产环境排查与 `zcode-switch` 对照，现有系统存在四大核心缺陷：

1. **套餐领域模型打平失真**：
   - 官方在 `billing/balance` 中返回多个 active 套餐（例如 `ZCode Start Plan` 常规体验包 与 `ZCode Trust Build` 节日活动包）。
   - 当前 `app/quota.py` 强行按模型名（如 `GLM-5.3-Flash`）将所有额度桶相加合并，抹除了套餐边界与不同的过期时间（如 1 亿 Token 今日 24 点到期 vs 500 万常态滚动）。
2. **三层资产状态混淆**：
   - 用户资产包含：① 当前可用余额（Active Balance）、② 待生效配额（Pending Effective）、③ 待领活动大礼包（Claimable Gift Plans）。
   - 现有系统在今日可用余额为 0 时直接将账号标记为 `exhausted` 并渲染全红，掩盖了账号名下悬挂着 1 亿待领 Token 的物理事实。
3. **刷新额度链路脱节**：
   - 当前单账号与全局“刷新额度”只请求 `current/balance/usage`，未联动 `preview_plans`。导致账号在外部领完或官方投放新活动时，界面无法感知待领活动。
4. **验证码与网络环境脆弱（机房 IP 与无头环境拦截）**：
   - `zcode-switch` 运行于桌面端真实 WebView2 内核，直接利用本地真实硬件上下文与国内宽带走通阿里云无痕验证；
   - `zcode2api` 运行于海外 VPS 机房 IP，缺少真实 GPU/Canvas，且机房 ASN 导致服务端无痕被拒、preview 接口被静默屏蔽返回空列表。

---

## 2. 架构设计与解决方案（第一性原理 + 归一化 + 二阶思维）

### 模块一：后端数据模型归一化（对齐 `zcode-switch`）

1. **数据结构定义（`app/models.py`）**：
   - 增加 `plan_slots: list[dict]` 字段持久化到 SQLite。
   - 每个 `PlanSlot` 包含：
     - `pid`: 套餐 ID（如 `zcode-v3-start-plan-trust-0928`）
     - `name`: 官方活动名称（如 `ZCode Trust Build`）
     - `expire`: 过期时间字符串/时间戳
     - `tier` / `tier_code`: 等级标识
     - `items`: 归属于该套餐的具体模型额度列表（`name`, `total`, `used`, `remaining`, `percent_used`）
   - 新增 `claimable_plans: list[dict]`：持久化该账号当前可领取的活动套餐元数据。
   - **保持核心状态枚举纯粹（遵循奥卡姆剃刀）**：
     - 严禁向核心调度状态 `Status` 引入 `EXHAUSTED_BUT_CLAIMABLE` 伪状态；底层状态严格保持 `EXHAUSTED`，确保 API 路由选择器不变；
     - 在 `Account.public_view()` 增加计算属性 `claim_badge: bool`（由 `len(claimable_plans) > 0` 派生），由前端视图层负责渲染金色待领横幅与高亮。

2. **聚合解析重构（`app/quota.py`）**：
   - 解析 `billing/balance` 的 `plans` 数组构建 `PlanSlot` 骨架；
   - 遍历 `balances` 额度桶，通过 `plan_id` 精准分发至对应的 `PlanSlot`；
   - **孤儿额度桶容错兜底（P2 修复）**：若某些额度项无 `plan_id` 或未匹配任何套餐，自动归入 `default_slot`（默认常规额度卡片），绝不丢弃任何模型配额；
   - 维护扁平全局 `quota` 映射用于网关高效路由选择。

### 模块二：刷新链路一体化（All-in-One 刷新与隔离降级）

1. **软解耦与隔离降级（P0 修复）**：
   - 配额更新（`current`/`balance`/`usage`）属于核心调度依赖，`preview_plans`（营销活动）属于展示层弱依赖；
   - 在 `fetch_quota(account)` 中，将 `preview_plans` 包装在独立协程中，配置独立短超时（8秒），且使用独立 `try-except` 捕获所有网络异常；
   - 若海外机房 IP 导致 preview 接口超时、403 或返回空，仅记录调试日志，绝不阻断正常配额与余额更新；
   - 全局批量刷新（`refresh_accounts`）只刷新核心额度；仅当用户单账号手动刷新、或后台活动哨兵定时巡检时，才触发执行带日活上报的 `preview_plans`，避免高频并发踩踏上游 WAF。

### 模块三：验证码与网络环境优化（双轨验证与出口网络一致性）

1. **双轨验证策略（Hybrid Verification）**：
   - **轨 A（服务端静默尝试）**：本地环境或已安装求解器时优先尝试无痕。
   - **轨 B（真实桌面浏览器原生滑块核销）**：
     - 用户在管理后台操作时，前端加载阿里云官方 SDK，在用户本机的真实 Chrome/Edge 浏览器环境中完成滑块验证；
     - 即使服务端海外节点被屏蔽无法 preview，也允许用户输入或一键选取已知活动 ID（`zcode-v3-start-plan-trust-0928`）完成前端滑动。
2. **上游出口代理支持（P1 修复）**：
   - 在系统设置及环境变量中增加 `UPSTREAM_CLAIM_PROXY`（专用于 billing/preview/claim 上游交互）；
   - 当部署在海外机房 IP 时，必须指定国内/住宅 HTTP 代理出口向上游 POST `/billing/claim`，确保前端滑块签发 IP 与后端核销 IP 属地网络一致，彻底破除 3007 跨国反作弊拦截。

### 模块四：前端视觉与交互重构（`frontend/admin/accounts.html`）

1. **待领活动横幅（`claim-strip`）**：
   - 只要账号存在 `claimable_plans`，在表格配额列上方醒目渲染：
     `🎁 [活动名称] · [模型名称] × [数量] [一键领取]`；
   - 点击直接调起原生滑块验证弹窗。
2. **多套餐卡片分组（`plan-grp`）**：
   - 若账号持有 $\ge 2$ 个套餐，分别渲染各个套餐的标题、到期时间、模型进度条（如 `ZCode Trust Build` 显示 Flash 100M 至次日 0 点；`ZCode Start Plan` 显示 5M/3M）；
   - 完整展示所有模型（不吞 `GLM-5.3`）。
3. **批量领取与刷新进度可视化（Antigravity 预设规范）**：
   - 顶栏配备高精度渐变流光进度条（`batch-progress-wrap`），实时反馈总数、当前正在处理的账号名称及百分比；
   - 按钮动态显示数量微动效：`刷新中 (i/N)...`、`领取中 (i/M)...`，表格行内对应账号同步高亮 `is-loading`；
   - 一键领取前置比对账号已持有套餐，已领取的账号直接短路跳过，彻底避免重复打码与上游 1003 报错；单账号刷新就地更新渲染。

---

## 3. 验收标准（Success Criteria）

1. **多活动隔离展示**：`国际-xzaq` 账户同时清晰展示 `ZCode Trust Build` 与 `ZCode Start Plan` 两个分组及各自的进度条。
2. **三态清晰不混淆**：当日 5M 额度用完但有 1 亿待领的账号，额度条显示 0/5M，同时高亮挂载金色待领横幅。
3. **刷新联动与隔离**：单账号刷新自动感知最新活动；preview 异常或超时不影响主额度更新。
4. **验证可用**：在真实浏览器中通过滑块成功兑换 `zcode-v3-start-plan-trust-0928`。
5. **单测覆盖**：335 项既有单测 100% 保持绿灯，新增多套餐解析单元测试。

---

## 4. 架构加固与对抗防线（v2.5.16 ~ v2.5.19 演进）

### 4.1 彻底切断自动呼起与提交死循环链
- **现象归因**：前序版本在提示吐司（`claimToast`）中捕获到 3012 风控时，隐式递归调用 `openClaimModal`。而弹窗内无感验证通过后又在 `success` 钩子中触发自动提交，在海外 IP 连续风控时导致 `Toast -> Modal -> Captcha -> Submit -> Toast` 的恶性正反馈死循环。
- **治理方案**：
  1. `claimToast` 坚决收敛为只读展示（`showToast`），抹去任何自激触发弹窗的隐藏路径；
  2. 自动提交钩子增加环境与状态守卫：仅当 `modal.classList.contains('open')` 且 `!claimSubmitting` 且验证凭据有效时延迟触发；
  3. 引入提交并发锁 `claimSubmitting`，并在 `closeClaimModal`、`finally` 与异常链路中严格重置锁状态。

### 4.2 已持有活动感知与三道门禁纵深拦截
- **现象归因**：历史探测到的 `claimable_plans` 未同账号已持有的 `plans` / `plan_slots` 做差集剔除，导致已领成功的账号在前端仍渲染黄色待领条，且重复提交触发上游报错。
- **治理方案**：
  1. **视图层过滤**：`claimStripHtml(a)` 中通过 `held = getHeldPlanIds(a)` 对 `claimable_plans` 做全量归一化过滤，已持有活动完全不渲染，待领条彻底收起；
  2. **入口门禁**：`openClaimModal` 检测账号是否已持有目标套餐，已持有则立即 Toast 提示「账号名」已持有该活动套餐，无需重复领取，直接阻断弹窗与 SDK 初始化；
  3. **切换门禁**：下拉框切至已持有账号时，容器降级展示友好提示并阻断验证码实例化；
  4. **提交门禁**：`submitManualClaim` 最终兜底校验目标套餐持有状态，已持有则关闭弹窗并提示。

### 4.3 Node 端求解器硬件脱敏与多态自洽指纹池（v2.5.16 & v2.5.19）
- **现象归因**：
  1. 早期版本硬编码了 `SwiftShader` 软渲染器与 1x1 单像素图，且虽在 `generateFingerprint()` 中将 `navigator.platform` 改为 `"Win32"`，但在 `injectRequestHeaders`、`createDom` 与 `navigator.userAgentData` 中仍残留 `"sec-ch-ua-platform": '"Linux"'` 与 `platform: "Linux", platformVersion: "6.5.0"`，形成跨层平台特征精神分裂；
  2. 每次求解均复用完全相同的静态 `canvasImage` Base64 字符串与单一硬件配置，导致全池多账号短时间集中领取时，上游解密 `verifyParam` 发现所有验证码来自同一 Canvas 哈希与单一静态浏览器环境。
- **治理方案**：
  1. **跨层平台特征 100% 归一化自洽**：彻底清除 `"Linux"` 硬编码，`sec-ch-ua-platform`、`navigator.userAgentData.platform`、`platformVersion`、`architecture`、`hardwareConcurrency`、`deviceMemory`、`screen.availWidth`、`devicePixelRatio` 全部统一从当前抽样的 `fp` 实例动态读取；
  2. **多态桌面硬件 SKU 池（`DESKTOP_SKUS` × `CHROME_VERSIONS`）**：涵盖 Chrome `127~131`、Windows 10/11（NVIDIA RTX 3060 / 4060 / 3070 / GTX 1660 SUPER、AMD RX 6700 XT、Intel Iris Xe）与 macOS Sonoma/Sequoia（Apple M2 / M3 / M2 Pro）共 45 种自洽桌面组合，每次子进程求解随机抽样；
  3. **一码一指纹动态微噪点（`generateCanvasPngDataUrl` + `audioJitter`）**：每次求解通过 `crypto.randomBytes` 与 `zlib.deflateSync` 实时合成带随机微噪点的唯一 32×32 RGBA PNG DataURL 及离线音频微抖动，彻底消除多账号验证码 Canvas 哈希碰撞。

### 4.4 模态框生命周期与实例清理
- 模态框遮罩点击统一收敛至 `closeClaimModal()`；
- 关闭时主动清空容器 DOM（`box.innerHTML=''`）拔除阿里 SDK 节点，解绑 `window.AliyunCaptchaConfig` 全局句柄，防止多轮验证码实例化闭包泄漏。

### 4.5 哨兵自动抢领状态归一化与漏领自愈闭环（v2.5.17）
- **现象归因**：
  1. `Sentinel` 原先硬编码 `a.status == Status.ACTIVE`，导致昨日额度耗尽处于 `Status.EXHAUSTED` 的账号（最急需领取免费活动包恢复战斗力的账号）在新活动发布时被直接跳过；
  2. `Sentinel` 原先采用单次边沿触发（`plan_id not in seen_ids`），一旦某账号因 `EXHAUSTED` 或网络抖动在首次边沿漏领，后续巡检因 `not new_plans` 直接退出，无法自动补领；
  3. `auto_claim_all_plans` 原先未前置过滤 `account_held_plan_ids` 且领取后未联动调用 `fetch_quota`，导致已领账号重复打码报 1003、新领账号无法立即恢复 `ACTIVE` 状态。
- **治理方案**：
  1. **状态判定归一化**：将探针候选与自动抢领目标统一收敛为 `a.allows_billing()`（同时覆盖 `ACTIVE` 与 `EXHAUSTED`）；
  2. **存量漏领自动补领闭环（Catch-up）**：在每轮巡检 `not new_plans` 分支中，比对池内活跃活动（`pool_active_plans`）及账号 `claimable_plans` 与 `account_held_plan_ids`，对漏领账号自动执行补领；
  3. **二阶防风控冷却避让**：补领失败的账号记录 6 小时冷却（`_catchup_cooldown`），杜绝因 3012 拦截在每 30 分钟巡检中死循环撞击上游；
  4. **领后闭环刷新**：`auto_claim_all_plans` 前置差集剔除已持有套餐，并在领取成功或命中 1003 已领时自动触发 `await fetch_quota(account)`，使 `EXHAUSTED` 账号领完即刻满血恢复 `ACTIVE`。

### 4.6 Bark 推送可靠投递与全链路领取战报闭环（v2.5.18）
- **现象归因**：
  1. **单次 8s 短超时 + 失败仍落库导致“发现新活动”丢单**：`api.day.app/push` 后端需同步连接 Apple APNs 下发推送，跨洋链路在凌晨整点偶发耗时超过 8s；原实现无退避重试，且在 `send_bark_notification` 返回 `ok=False` 时依然无条件将 `new_plans` 写入 `sentinel_seen_plans`，导致单次网络抖动即永久丧失该活动推送；
  2. **领取成功路径缺失 Bark 推送**：原架构仅在 Sentinel 首次发现 `new_plans` 时调用 Bark，而在“哨兵存量漏领自动补领（Catch-up）”、“新号入池自动领取”、“后台一键领取”与“浏览器滑块手动领取”成功时均无任何 Bark 通知。
- **治理方案**：
  1. **传输层瞬态退避重试（`app/notify.py`）**：默认超时提升至 `12.0s`，内置 2 次指数退避重试（仅针对超时/网络异常/5xx 重试；遇 4xx 立即熔断防触发 Bark 官方 IP 封禁）；
  2. **确认送达后落库（`app/sentinel.py`）**：仅当 Bark 推送成功（或未配置 Bark / 连续 3 轮巡检失败兜底）时才将 `plan_id` 写入 `sentinel_seen_plans`，且重推轮次自动将已领账号识别为“已持有(成功)”；
  3. **全链路领取成功战报归一化（`notify_claim_outcomes` / `schedule_claim_notification`）**：统一提取非 `skipped` 的真实新领成功项，覆盖哨兵自动补领、入池自动领取、后台一键领取与手动滑块领取四大场景，Web 接口采用后台强引用 Task 异步非阻塞投递。

### 4.7 哨兵探针轮转与批量领取节流防限流（v2.5.19）
- **现象归因**：
  1. `Sentinel.check_once()` 原先仅按状态排序而未打散同优先级账号，导致每 30 分钟一次的 `preview_plans` 探针永远固定由列表第 1 个 `ACTIVE` 账号独自承压（全天 48 次），且抢领顺序永远固定；
  2. `POST /admin/api/claim` 批量接口在多账号循环中从预解池瞬时取出多枚验证码后，以 0ms 间隔背靠背瞬发 `POST /billing/claim`。
- **治理方案**：
  1. **同优先级随机轮转**：`Sentinel.check_once()` 在探针筛选与全池抢领前先执行 `random.shuffle` 再按 `ACTIVE` 优先稳定排序，使巡检探针与抢领顺序在池内均匀分摊；
  2. **批量领取离散抖动节流**：`POST /admin/api/claim` 在多账号连续向上游发起 `do_claim` 之间自动注入 `0.6~1.5s` 随机抖动延时，与哨兵抢领节流策略完全对齐。



