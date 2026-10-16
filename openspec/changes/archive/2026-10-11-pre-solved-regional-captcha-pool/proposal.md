# 变更提案：基于大区与代理出口的多轨预解水库、三态断路器与活动错峰门禁 (pre-solved-regional-captcha-pool)

## 1. 提案背景与动机

在智谱/ZCode 线上服务中，阿里云 WAF 对免费 Plan 通道（`zcode.z.ai/api/v1/zcode-plan/anthropic/v1/messages`）强制下发 3007 人机校验挑战。
经 Antigravity 主任架构师审查，现有系统存在以下核心矛盾：
1. **热路径同步现解（On-Demand Solve）与阿里 IP 强绑定**：阿里云 WAF 强校验“求解 IP = 发送请求 IP”。当前若水库粗粒度混用同大区不同端口的 Token，将导致报文被 WAF 判定为中间人篡改（3007/F001）。
2. **3007 挑战全局株连（Blast Radius）**：旧网关遭遇 3007 时无差别清空全局池，导致某一区域的拒接连带误杀其他健康大区的有效 Token。
3. **单核 CPU 暴毙与惊群风险**：突发流量时多协程并发拉起 Node 进程将打爆单核 VPS 算力（CPU 100%），进而导致 SSE 长流传输中断。
4. **活动领取（Claim）并发挤兑风险**：多账号若在同一住宅节点上同秒并发领额度，会触发阿里 WAF `1005`（今日名额已领完）或 `3012`（机房异常流量拦截）甚至同 IP 连坐封号；但若无超时上限排队，又会导致客户端 499 挂死。

## 2. 变更目标与范围

1. **代理出口亲和预解水库（Proxy-Affinity Reservoir）**：
   - 预解池以 `(region, proxy_url)` 为物理隔离单元（或大区单主活跃端口），严格保证“求解出网 IP == 请求出网 IP”。
   - 底层使用带时间戳的高效 `deque` 存储，后台保鲜协程维持每个出口 2~3 个新鲜 Token（TTL 75~90s）。
   - 请求到达时 \(O(1)\) 亚毫秒直接弹栈，热路径求解耗时归零。
2. **靶向精准清池（Scoped Invalidation）**：
   - 彻底废除全局清池，重构为 `invalidate(region, proxy)`，某一节点发生 3007 时仅清空该节点缓存，绝不株连其他大区。
3. **Singleflight 单飞防惊群合并**：
   - 水库临时空仓时，同一代理同一时刻仅允许 1 个现解任务在飞，并发请求挂起共享首个结果并异步补仓，废除易打爆算力的无脑双路并发。
4. **收敛至 ClientPool 的三态断路器（CLOSED / OPEN / HALF-OPEN）**：
   - 区分网络瞬态抖动（允许 3 次失败）与确定性风控封禁（2 次熔断）；
   - 隔离 10 分钟期满后进入 `HALF-OPEN` 半开探针模式，放行单次探测，成功则复活，失败则重置冷却，防止惊群复活又瞬间全灭。
5. **带 45s 快速失败的错峰串行门禁（Staggered Pacing Gate with Fail-Fast）**：
   - 同一代理出口两次 `claim` 强制间隔 20 秒，每个出口独立分配 `asyncio.Lock`；
   - 预估排队等待超过 45 秒时立即快速失败并提示稍后重试，坚决不吊死客户端。
6. **自适应 CPU 并发限流守卫**：
   - 动态识别 CPU 核数：单核 VPS 强制并发度为 1，多核 `max(1, cpu_count - 1)`，获取信号量加注超时保护。

## 3. 非目标（Out of Scope）

- 不改变账号数据库持久化 Schema（复用现有的 `assigned_region` 架构）。
- 不破坏 API Key 免码回退通道（继续作为最外层防 500 的安全网）。
- 严禁盲目堆积账号数量代替住宅节点扩容。

## 4. 破坏性变更说明（Breaking Changes）

- `app.captcha.CaptchaManager` 接口重构：废除无参数的 `invalidate()`，强制要求传入 `(region, proxy)` 靶向作用域。
- `app.settings` 废弃单纯全局 `CAPTCHA_POOL_MIN`，由 `CAPTCHA_REGIONAL_POOL_MIN` 与 `CAPTCHA_SOLVE_CONCURRENCY` 替代。
