# Design

## 目标

补齐「登录客户端」新手任务（上游 key `login_device`）所需的 `app_login_success` 事件上报。**只做通用能力修正，不引入活动专属编排**（实测确认：事件发出后上游服务端自动判定与结算，无需客户端领取）。

## 官方契约（逆向实证）

来源：`D:\Temp\lizi\ZCode`（官方仓库，v3.15.1）。

### 事件定义

`packages/ui/src/root/useRootOAuthEffects.ts`：

```typescript
elementName: "app_login_success",
eventRegion: "app_profile",          // 非常规 "app"
eventType: "view",
eventExtraDetail: { login_provider: loginProvider },  // "z.ai" / "bigmodel"
```

`loginProvider = resolveProviderTelemetryLabel(providerId)`：`zai` 系 → `"z.ai"`；`bigmodel` 系 → `"bigmodel"`。

### 事件体字段（telemetryCore.ts 统一 16 字段）

`app_login_success` 不携带 `talk_id` / `message_id`（会话类事件专属）。

### 请求头（event-report-delivery-reliability.md）

仅 App `/api/v1/event/report` 复用完整公共头（User-Agent、HTTP-Referer、X-Title、X-ZCode-App-Version、X-Platform、X-Release-Channel、X-Client-Language、X-Client-Timezone、X-Os-Category、X-Os-Version、X-Device-Mid），登录态且 token 非空时发 `Authorization: Bearer <JWT>`。

## 现有实现缺口

`app/telemetry.py` 的 `build_activation_event_body` 硬编码 `event_region="app"`、`event_type="view"`、`event_extra_detail={}`；`post_activation_event` 仅发 `Content-Type` 头。

`app/claim.py` 的 `report_activation_events` 只遍历 `constants.ACTIVATION_ELEMENTS = ("app_launch", "app_daily_active")`。

缺口：
1. 事件体字段不可配置，无法表达 `app_profile` 域与 `login_provider`。
2. 不带 `Authorization` 与公共头（注释"无 Authorization，官方端点不校验登录态"是错误结论）。
3. `ACTIVATION_ELEMENTS` 缺 `app_login_success`。

## 设计决策

### D1：事件体参数化，默认值不变

`build_activation_event_body` 增加可选参数 `event_region="app"`、`event_type="view"`、`event_extra_detail=None`。默认值与现状一致，保证 `app_launch`/`app_daily_active` 行为不变。

### D2：请求头补齐，Authorization 可选

`post_activation_event` 增加可选参数 `headers: dict | None`。调用方传入完整公共头与 `Authorization`；未传入时保持最小 `Content-Type`（兼容 `install.py` 匿名首启上报，`user_id=""` 不强制带 Authorization）。

`report_activation_events` 通过 `build_identity_headers(account)` + `Authorization: Bearer <jwt>` 构造完整头传入。

### D3：`app_login_success` 独立于 `ACTIVATION_ELEMENTS`

`app_login_success` 的 `event_region`/`event_extra_detail` 与 `app_launch`/`app_daily_active` 不同，不能简单追加到 `ACTIVATION_ELEMENTS` 循环。

方案：`constants` 新增 `LOGIN_EVENT = {"element": "app_login_success", "region": "app_profile", "type": "view", "detail": {"login_provider": "z.ai"}}`；`report_activation_events` 在循环后单发一次。

`login_provider` 按 account.provider 推导（**方案 B，严谨**）：`zai` → `"z.ai"`，`bigmodel` → `"bigmodel"`。推导函数收敛在 `telemetry.py`（`resolve_login_provider(provider)`），与官方 `resolveProviderTelemetryLabel` 同语义；当前账号池全为 `zai`，但保留 `bigmodel` 扩展点，避免未来加号时返工。

### D4：`app_login_success` 一次性，复用 `installed_at` 去重

`app_login_success` 语义是「账号在此设备完成 OAuth 登录」，**不应随高频刷新重复上报**（否则上游读作「账号反复登录」，构成多账号网关下的异常信号）。

复用既有 `Account.installed_at`（`None` = 未安装）作为天然去重标志：仅在 `installed_at is None` 时发一次。`install.run_install_sequence` 完成时会写入 `installed_at`，之后任何刷新/轮询/领取路径都不会重复发。

**不做**：新增数据库字段、新增去重标志位（减法优先，复用已有 `installed_at`）。

### D5：不引入活动编排

实测确认：`app_login_success` 发出后，上游 `referral/summary` 的 `login_device` 自动翻转为 `true`，`night_card` 自动 `achieved`，无需客户端探测/领取。故不新增活动任务探测、领取或轮询逻辑，避免活动下线后留下死代码。

### D6：分组推进与验收

按「实现一组 → 验收这一组 → 修复发现的问题 → 通过后进入下一组」推进，不逐行跑全量：

1. **组 1（telemetry 能力）**：`telemetry.py` 事件体/投递参数化 + `resolve_login_provider`。验收：新增单测 + 既有 `app_launch` 行为回归。
2. **组 2（事件接入）**：`constants.py` 常量 + `claim.report_activation_events` + `install.py` 补发，带 `installed_at` 一次性守卫。验收：新增单测覆盖一次性去重与事件字段。
3. **组 3（端到端）**：从插件/入池入口走完整链路，真实验证 `referral/summary` 的 `login_device=true`（用新账号，不消耗额度）。

## 风险与缓解

| 风险 | 缓解 |
|---|---|
| 补齐 Authorization 后 `app_launch`/`app_daily_active` 触发 3012 | 实测完整头 + Bearer 未触发风控；官方投递合同本就要求登录态带 Authorization。上线后监控 event/report 业务码。 |
| `install.py` 匿名首启被强制带 Authorization 破坏 | D2 设计为可选参数，匿名路径不传。 |
| `app_login_success` 高频重复上报 → 被读作「反复登录」 | D4 复用 `installed_at` 一次性去重，仅首次入池发一次。 |
| 多账号指纹关联（`device_mid` 不一致） | 严格复用 `profile_for(account)` 同一份指纹；不手拼 device_mid；与 `/messages` 的 `build_identity_headers(account)` 同源。 |
| `login_provider` 与账号实际 provider 不一致 | 方案 B 按 `account.provider` 推导，`zai`→`z.ai`、`bigmodel`→`bigmodel`。 |
