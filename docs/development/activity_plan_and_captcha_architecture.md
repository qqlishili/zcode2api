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
