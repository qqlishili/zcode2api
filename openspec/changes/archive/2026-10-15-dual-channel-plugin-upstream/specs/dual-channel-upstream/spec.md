# Spec: dual-channel-upstream

## 1. 契约与范围

本规范定义 ZCode2API 上游请求的**双通道**行为：同一客户端请求可由两条并列通路发出 ——
**通道 A `native`**（Python httpx 手写）与 **通道 B `plugin`**（magpie-plugins/zcode 经 host.js）。

**范围**：仅 `zcode.z.ai` Plan 请求（`/api/v1/zcode-plan/anthropic/v1/messages`）。不含登录/OAuth/团队套餐。

---

## 2. 需求与场景契约

### REQ-DUAL-01: 通道可配置选择
- `settings.REQUEST_CHANNEL`（env `ZCODE_REQUEST_CHANNEL`）取值 `native` | `plugin`，**默认 `native`**。
- **场景 1（默认）**：未设置时使用 `native`，行为与现网一致。
- **场景 2（切换）**：设为 `plugin` 且通道健康时，经 host.js + magpie-plugins 发请求。

### REQ-DUAL-02: 两通道对外契约同构
- 两通道实现同一 `UpstreamChannel` 契约；`gateway.py` 不感知差异。
- **场景 1（返回同构）**：`send()` 必返回 `httpx.Response`（含 status_code / headers / 可读 body）。
- **场景 2（两种消费方式）**：返回的 `httpx.Response` **必须同时支持** `await resp.aread()` 与 `resp.aiter_bytes()`。
- **场景 3（流式重组）**：plugin 通道收到 `head` + 多个 `chunk` 时，必须按序 base64 解码并重组。

### REQ-DUAL-03: 单请求只走一条通道
- **场景 1**：一个上游请求**严禁**同时发往两条通道（避免上游看到双倍流量）。
- **场景 2**：配置只取一个值，**不实现主备/并行**。

### REQ-DUAL-04: 通道只发一次，重试留 gateway
- **描述**：`send()` 只执行**一次**上游尝试；返回响应或抛 `httpx.HTTPError`。
- **场景 1（不换号）**：通道**不得**自行切换账号或重试。
- **场景 2（错误透传）**：上游 4xx/5xx 必须原样作为 `httpx.Response` 返回，由 gateway 的既有分支处理。

### REQ-DUAL-05: 失败包装为同构响应
- **场景 1（host 进程异常）**：host 不可用/中途崩溃时，必须返回 **HTTP 502** 的 `httpx.Response`，使 gateway 走既有 5xx 分支（重试 + 冷却）。
- **场景 2（网络异常）**：Bun 侧网络失败必须抛 `httpx.HTTPError` 语义兼容的异常，映射到 `upstream_transport_error`。
- **场景 3（禁止新增错误类型）**：**严禁**引入 gateway 不认识的新错误类型。

### REQ-DUAL-06: 出口为通道无关的公共层
- **场景 1（同账号同出口）**：同一账号无论走哪条通道，使用同一出口字符串。
- **场景 2（直连必须显式传）**：plugin 通道在 `fetch` 的 `params.proxy` 传代理 URL；**直连必须传 `"direct"`**（不可省略，否则 host 读环境变量）。

### REQ-DUAL-07: 账号池与额度判定不受通道影响
- **场景 1（选号不变）**：切换通道前后，同一请求序列选中同一账号。
- **场景 2（额度判定不变）**：`quota.py` 的判定逻辑完全不变；plugin 的 `usage` 仅作旁路对照。

### REQ-DUAL-08: 模型名完整透传
- **场景 1（透传）**：客户端原始 model 名（经大小写/别名归一后）原样传给上游，**含 `-Trial` 后缀**。
- **场景 2（Trial 语义）**：`-Trial` 后缀由插件识别，**剥离为原始名后发送**；上游响应中的 `model` 字段同样被归一（去掉 `-Trial`）。
  **注意**：不能说「`-Trial` 强制走活动额度」—— 本网关的派生凭证只有 `jwt`、没有 `key`，插件 `plansOf` 对 `!s.key` 的账号**本来就返回 `start: true`**，即走 `START_BASE` + JWT + `dress`。`-Trial` 的实际作用是在**发送前**检查该活动桶是否可用（不可用直接 429/400，不浪费上游请求）。（源码：`index.mjs:206-208` 与 `1550-1566`，见 design.md §2.10）
- **场景 3（不传不触发）**：客户端不传 `-Trial` 时**无任何副作用**。

### REQ-DUAL-09: 降级与回退
- **场景 1（Bun 缺失）**：`ZCODE_BUN_PATH` 不可执行时，记录告警，plugin 通道标记不可用，走 `native`。
- **场景 2（配置回退）**：`ZCODE_REQUEST_CHANNEL=native` + 重启，**必须 100% 恢复**改动前行为。

### REQ-DUAL-10: 槽位标识的可诊断性
- **场景 1（日志带账号名）**：凡在日志 / 异常 / evidence 中提及槽位，**必须同时包含 `account.name`**。
- **场景 2（禁止裸哈希）**：**严禁**只打印槽位哈希而不带账号名（运维无法定位）。
- **理由**：槽位为定长哈希付出了可读性代价，必须在日志层补偿。

---

## 3. 非功能需求

### REQ-DUAL-NF-01: 可观测
- 每次请求记录所用通道（evidence 表增列或日志字段）。
- host 生命周期事件（start / init / restart / close / error）落 evidence。

### REQ-DUAL-NF-02: 并发
- **上游单账号并发上限实测 = 3**（并发 4 起出现 429 `code:3008`）。
- 现有 `ACCOUNT_CONCURRENCY=2` **保持不变**（安全）。
- **扫描请求计入同一并发计数**。

### REQ-DUAL-NF-03: 无 Schema 破坏
- 不新增数据库表；`usage` 对照写既有 `meta` 表。

---

## 4. 验收标准

| 编号 | 验收 |
|---|---|
| AC-1 | `native` 模式全量单测通过（预期差异已显式更新） |
| AC-2 | `plugin` 模式真实请求 200 且响应含 `"model":"glm-5.3-flash"` |
| AC-3 | Bun 缺失时启动不崩，自动降级 native 且告警 |
| AC-4 | 同一账号两通道走同一出口、同一 device_mid |
| AC-5 | host 崩溃时 gateway 收到 502（走既有 5xx 分支） |
| AC-6 | 切回 `native` 后行为与改动前一致 |
