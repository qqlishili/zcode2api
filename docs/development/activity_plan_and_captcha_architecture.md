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

## 4. 架构加固与对抗防线（v2.5.16 ~ v2.6.2 演进）

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

### 4.8 指纹浏览器级种子驱动多维深度混淆（v2.5.20）
- **现象归因（对比主流开源指纹浏览器 `GeekezBrowser`、`RoxyBrowser`、`apify/fingerprint-suite`、`BotBrowser`、`fingerprint-chromium`）**：
  1. **2D Canvas `getImageData` 空白像素漏洞**：原 `make2DStub` 的 `getImageData` 直接返回全 0 透明 `ImageData`，若阿里云 `pe.*` 字节码 VM 执行 `fillText` 后通过 `getImageData` 提取像素哈希而非仅调 `toDataURL`，全 0 像素会立即暴露无头空壳 DOM；且若使用非确定性随机噪点，无法通过同一会话内的“二次重绘一致性校验”；
  2. **Audio 常数偏移一阶导数为零**：原 `OfflineAudioContext` 对所有采样点加同一个常数 `jitter`，相邻采样差分方差为 0，且 `AnalyserNode` 未填充频谱数据；
  3. **`userAgentData` 缺失 `"Google Chrome"` 品牌与按需高熵过滤**：原 `brands` 仅含 `Chromium` 与 `Not)A;Brand`，缺少 `toJSON()`、`bitness`、`wow64`、`formFactors` 及按 `hints` 数组过滤逻辑；
  4. **WebGL 未枚举常量泄露 `"Intel Inc."`**：原 `makeWebGLMock.getParameter` 对 `MAX_TEXTURE_SIZE (3379)` 等硬件能力参数未枚举时兜底返回 `"Intel Inc."`，在抽中 NVIDIA/AMD/Apple Silicon SKU 时造成跨层硬件矛盾；
  5. **`mediaDevices` / `speechSynthesis` 空数组与整数字体宽度**：桌面浏览器返回空音视频设备列表、空 TTS 语音列表及无亚像素小数的 `measureText`/`getBoundingClientRect` 均属于典型沙箱特征。
- **治理方案（Clean-Room 纯自研实现）**：
  1. **`SplitMix32` 会话种子驱动（跨进程唯一 + 同进程重放幂等）**：每个求解子进程生成 32-bit `fp.seed`，所有 Canvas、Audio、ClientRects、MediaDevice ID 均由 `splitMix32(fp.seed ^ salt)` 确定性派生，既保证每次生成的 `verifyParam` 全局唯一，又保证同一次会话内风控脚本多次调用同一 API 返回完全一致的哈希；
  2. **2D Canvas 轨迹感知非零光栅与稀疏微扰**：记录 `fillText`/`fillRect`/`arc`/`stroke` 等绘制状态哈希，`getImageData` 生成非零渐变基底并在约 `1/31` 稀疏像素位点注入 `±1~3` 确定性通道微移；
  3. **Audio 逐采样点确定性白噪微扰**：`OfflineAudioContext.startRendering` 按采样点索引 `i` 注入 `1e-7` 量级 `splitMix32` 微噪，`AnalyserNode` 同步填充确定性频谱/时域数据；
  4. **WebGL 全量硬件常量与 84 种桌面组合**：扩充至 12 套桌面 SKU × 7 个 Chrome 版本（`128~134`），补齐 `3379/34076/34024/3386/36347` 等 WebGL 硬件能力常量，彻底移除 `"Intel Inc."` 字符串兜底；
  5. **完整 Client Hints / 亚像素排版 / 媒体与语音设备自洽**：`sec-ch-ua` 与 `userAgentData` 补齐 `"Google Chrome"` 品牌、`toJSON()` 及 `getHighEntropyValues(hints)` 过滤；`measureText` 与 `getBoundingClientRect` 注入 `fp.rectJitter` 亚像素微偏置；`mediaDevices.enumerateDevices` 与 `speechSynthesis.getVoices` 返回与当前 OS 平台匹配的设备及系统语音列表。

### 4.9 上游 OAuth 2026-09 协议、Claim 3.11.2 语义与 Win32 内核版本归一（v2.6.1）
- **现象归因**：
  1. **OAuth CLI 2026-09 协议演进**：上游 `POST /oauth/cli/init` 新增在 `data.poll_token` 下发服务端签名的轮询令牌（若继续使用客户端自造 `poll_token` 会被新网关拒绝），且轮询超时在 HTTP 4xx body 中返回 `code == 3004`；
  2. **Claim 3.11.2 结构化回执与 `1005` 精确窗口**：上游在 `POST /billing/claim` 返回 `code == 1005`（本时段名额已领完）时，于 `data.plan.ends_at`（秒级时间戳）给出下一场开放时间；成功响应附带 `starts_at` / `ends_at` / `server_time`；
  3. **Windows 宿主 `platform.release()` 单段值被拒**：Windows 下 `platform.release()` 返回 `"10"` 或 `"11"`，不满足三段式内核版本正则，若放宽正则会导致非规范字符串混入设备指纹。
- **治理方案**：
  1. **OAuth 协议对齐（`app/oauth.py`、`app/routes/admin_api.py`）**：`ZaiAuthFlow.init()` 校验 `code != 0` 抛错，并优先采用 `data.poll_token`（未下发时回退本地随机 token）；`login_poll` 识别 `code == 3004` 返回 `status: "expired"`；
  2. **结构化 `ClaimError` 与精确 `next_at` 避让（`app/claim.py`、`accounts.html`）**：`ClaimError(message, *, code=-1, next_at=None)` 携带业务码与 `next_at`（毫秒），`_mark_claim_blocked` 优先采用 `next_at + 5~60s` 离散抖动（缺失时回退北京时间次日 00:05 兜底），成功回执透传 `starts_at`/`ends_at`/`server_time`，前端 Toast 展示恢复倒计时；
  3. **Windows 内核版本归一与哨兵优雅停服（`app/hostinfo.py`、`app/fingerprint.py`、`app/sentinel.py`）**：新增 `_windows_kernel_version()` 通过 `sys.getwindowsversion()` 合成 `"10.0.xxxxx"`，恢复 `fingerprint.py` 严格三段式 `_RELEASE_SHAPE` 校验门；`Sentinel.stop()` 引入 `STOP_GRACE_SECONDS = 5.0` 优雅停机超时保护。

### 4.10 后台额度监控去抖错峰、验证码补货微抖动与批量入池串行平滑（v2.6.2）
- **现象归因**：
  1. **`QuotaMonitor` 整点脉冲与重复刷新**：原 `QuotaMonitor` 每 60s 整点通过 `Semaphore(4)` 零间隔并发刷新全部账号（单秒突发 $3N$ 个 `/billing/*` 请求），且未检查 `last_checked_at`，导致刚被网关对话 `_safe_refresh` 刷过的账号被重复刷新；
  2. **验证码预解池连解与批量入池惊群**：`CaptchaManager._refill_batch` 在冷启动或清池后 0ms 背靠背连拉多个子进程；批量添加/导入账号时同秒并发创建 $2N$ 个安装与自动领取协程。
- **治理方案**：
  1. **额度监控去抖与错峰平滑（`app/quota.py`）**：`QuotaMonitor.check_once()` 自动跳过近期已刷账号（`< min(0.8 * interval, BILLING_REFRESH_MIN_INTERVAL)`），随机打散待刷账号顺序，并以 `max_concurrency=2, stagger_sec=0.4`（`0.3~0.5s` 离散间隔）错峰平滑请求，周期等待附加 `±10%` Jitter，`stop()` 接入 5s 超时保护；
  2. **预解池连解微间隔（`app/captcha.py`）**：`_refill_batch` 在单批多枚连解（`solved > 0`）之间注入 `0.4~1.0s` 随机微抖动；
  3. **批量入池串行错峰锁（`app/routes/admin_api.py`）**：`_schedule_install` 与 `_schedule_auto_claim` 内置串行锁与连续任务间随机退避（`0.3~0.8s` 与 `0.6~1.5s`），单号入池 0ms 立即执行，批量入池自动削峰排队。

### 4.11 哨兵跨零点准时破冰、凌晨黄金窗口快速复查与探针日活前置（v2.6.3）
- **现象归因**：
  1. **纯相对时长 `sleep(1800)` 导致跨零点盲等**：上游每日活动套餐（如 `zcode-v3-start-plan-trust-MMDD`）与每日名额重置均锚定在北京时间 `00:00:00` 切换，而 `Sentinel._loop` 原先仅按进程启动相对时间盲睡 `1800s`，导致跨零点后可能滞后最多 30 分钟才发起首轮活动探测；
  2. **哨兵探针缺失前置日活上报与失败待领同步**：`Sentinel.check_once()` 裸调 `preview_plans` 而未像 `fetch_quota` / `auto_claim_all_plans` 那样前置调用 `report_activation_events`；且当个别账号因 `3012` 风控在自动抢领中失败时，既未将发现的未领套餐同步落库至 `account.claimable_plans`（导致前端不显示手动滑块入口），又在战报中误标为 `"无新配额"`；
  3. **`systemd` 管道块缓冲导致日志时间戳滞后**：`app/logs.py` 的 `print` 未开启 `flush=True`，导致 `journalctl` 中日志时间戳比实际执行时间滞后数十分钟。
- **治理方案**：
  1. **跨零点破冰与黄金窗口调度（`app/sentinel.py`）**：新增 `Sentinel._next_sleep_seconds()`，常规周期附加 `±10%` 抖动；若睡眠跨越北京时间 `00:00:00`，自动截断至 `00:00:15 ~ 00:01:00` 准时唤醒；在 `00:00 ~ 00:10` 窗口内若尚未探测到当日新活动，按 `90 ~ 150s` 快速复查（发现后立即恢复常规 `1800s`）；若池内存在 `1005` 避让账号（`claim_blocked_until`），自动对齐最早解封时间 `+ 5~20s` 唤醒补领；
  2. **探针日活前置与失败待领持久化（`app/sentinel.py`、`app/claim.py`）**：哨兵探针在 `preview_plans` 前以 5s 超时前置调用 `report_activation_events`；新活动抢领失败时准确输出失败原因并直接记入 `_catchup_cooldown`；`auto_claim_all_plans` 结束时将未领完的套餐同步写入 `account.claimable_plans`；
  3. **日志实时刷盘（`app/logs.py`）**：全量 `print` 开启 `flush=True`，消除 `systemd/journald` 块缓冲延迟。

### 4.12 探查即落库全池待领同步与底层原子方法拼装重构（v2.6.4）
- **现象归因**：
  1. **探查活动与保存待领状态职责割裂**：前端 `claimStripHtml(a)` 唯一依赖 `a.claimable_plans` 渲染待领活动 Icon；后端原先仅在手动刷新额度 `fetch_quota(include_claimable=True)` 时写回 `account.claimable_plans`，而 `preview_plans(account)` 只返回局部变量不落库。导致单账号/批量 `do_claim`（点击礼盒领取遇 `3012` 抛错中断）、`GET /admin/api/claim/preview`（查看预览弹窗）、或哨兵单账号探针巡检时（其余处于 `3012` 冷却 / `1005` 避让的漏领账号未进入 `auto_claim_all_plans`），数据库中 `claimable_plans` 始终为空，页面看不到活动领取 Icon；
  2. **底层方法大段重复与职责耦合**：`claim()` 与 `_post_claim()` 重复实现请求发送与 `1005` 避让标记；`pool_active_plans()` 对 `plans` 与 `claimable_plans` 重复编写两套循环；`claim.py`、`quota.py`、`sentinel.py`、`admin_api.py` 多处手写 `plan_id` 大小写归一化与 `claimable_plans` 差集过滤落库；`Sentinel.check_once()` 将冷却清理、候选筛选、有界探针、存量补领、全池抢领、战报格式化与时序安全落库揉在单个近 200 行函数内。
- **治理方案**：
  1. **「探查即落库 + 全池经验广播」单一事实源（`app/claim.py`）**：提炼 `_norm_plan_id`、`filter_unclaimed_plans`、`sync_account_claimable_plans`、`sync_pool_claimable_plans` 四个原子函数。在 `preview_plans(account)` 完成探查瞬间即同步持久化当前账号的 `claimable_plans`，并将非基础大促活动通过 `sync_pool_claimable_plans` 广播同步至池内所有未持有该活动的可计费 JWT 账号；`_auto_pick_plan` 在发起 `POST /billing/claim` 前确保待领项已入库，`_post_claim` 在成功（`code == 0`）或已领（`code == 1003`）时自动剔除该项，遇 `3012`/`1005` 失败则天然保留待领 Icon；
  2. **底层方法原子化拆解与拼装（`app/claim.py`、`app/sentinel.py`、`app/quota.py`、`app/routes/admin_api.py`）**：
     - `claim()` 与 `claim_with_captcha()` 共享 `_ensure_claimable_account()` 前置栅栏与 `_post_claim()` 单次提交原语，`claim()` 仅负责外层 `3007` 换码重试闭环；
     - `Sentinel.check_once()` 拆解为 `_prune_expired_cooldown`、`_record_cooldown`、`_billable_candidates`、`_probe_upstream_plans`、`_run_catchup_claims`、`_claim_new_plans_across_pool`、`_format_bark_report`、`_deliver_and_commit_new_plans` 8 个职责单一的小步骤方法组合编排。

