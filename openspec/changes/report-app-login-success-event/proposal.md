# Proposal

## Why

「夜间蹬蹬卡」活动（`/cn/rewards`）的「登录客户端」新手任务（上游 key `login_device`）长期无法完成。2026-10-14 现场实测（`lidmuw@chtsv.store`）确认：

- 上游任务状态接口 `GET /api/v1/incentive/referral/summary` 返回三个任务：`referral_bind_code`、`login_device`、`send_message`。
- `send_message` 与 `referral_bind_code` 均已 `completed=true`，唯独 `login_device` 恒为 `false`，奖励 `night_card` 卡在 `current=2/3`。
- 补发官方事件 `app_login_success`（`event_region="app_profile"`、`event_extra_detail={"login_provider":"z.ai"}`，带 `Authorization: Bearer <JWT>` 与完整公共头）后，`login_device` 立即 `true`，进度 `3/3`、`state=achieved`，套餐 `zcode-v3-start-plan-referral` 自动激活。

根因：本服务 `report_activation_events` 只上报 `app_launch` + `app_daily_active`，从不发 `app_login_success`。而 `app_login_success` 是官方客户端 OAuth 登录成功的**固有事件**，不属于任何单一活动；本服务在"伪造官方客户端登录态上报"时漏掉了这一环。

## What Changes

**只做最小通用能力修正，不引入活动专属逻辑。**

- `telemetry.py` 事件体与投递参数化：支持每事件独立的 `event_region`、`event_type`、`event_extra_detail`，以及可选 `Authorization` 与完整公共头。
- 新增 `app_login_success` 事件上报（`event_region="app_profile"`、`event_type="view"`、`event_extra_detail={"login_provider":"z.ai"}`），在 `report_activation_events` 内补发。
- `install.run_install_sequence` 首启安装序同步补发（保证任一入池入口都触发 `login_device`）。
- 修正 `post_activation_event` 注释与投递：登录态携带 `Authorization` 与公共头（对齐官方 `event-report-delivery-reliability.md` 投递合同），不再仅发 `Content-Type`。

**不引入**：蹬蹬卡任务的探测/领取/轮询编排。实测确认 `app_login_success` 发出后，上游服务端自动完成 `login_device` 判定并结算奖励，客户端无需"领取"。活动专属 key（`login_device`）、套餐（`zcode-v3-start-plan-referral`）不进入主代码。

## Capabilities

### New Capabilities

- `app-login-success-telemetry`：登录成功后上报 `app_login_success` 事件，携带 `app_profile` 事件域与 `login_provider` 明细。

### Modified Capabilities

- `activation-telemetry`：事件体与请求头参数化；补发 `app_login_success`；补齐登录态 `Authorization` 与公共头。`app_launch`/`app_daily_active` 行为不变。

## Impact

- 主要代码：`app/telemetry.py`、`app/claim.py`、`app/install.py`、`app/constants.py`。
- 关联契约：`app/quota.py`、`app/sentinel.py` 复用 `report_activation_events`，仅事件集扩展，不改调用签名。
- 测试：新增 `tests/unit/test_login_success_telemetry.py`；复用现有激活上报测试保证 `app_launch`/`app_daily_active` 行为不变。
- 无数据库迁移、无新服务、无第三方依赖、无活动专属死代码。
- 风险：登录态 `Authorization` 头补齐可能改变上游对既有 `app_launch` 事件的接受形态，需回归验证不触发 3012（实测完整头 + Bearer 未触发风控）。

## Validation

- 单元测试：事件体字段精确匹配官方契约；`app_login_success` 为 `app_profile` 域 + `login_provider`；`app_launch`/`app_daily_active` 保持 `app` 域。
- 真实验证：`lidmuw` 已通过补发 `app_login_success` 完成 `login_device`（`night_card state=achieved`）；落地后以新账号走 OAuth 授权 → 自动上报 → 校验 `referral/summary` 的 `login_device=true`。
- 事件上报为只读旁路，不调用 `/messages`，不消耗额度。
