# Tasks

按组推进：实现一组 → 验收这一组 → 修复问题 → 通过后进入下一组。每组独立验收，不逐行跑全量。

## 组 1：telemetry 能力（事件体/投递参数化 + login_provider 推导）

- [ ] `app/telemetry.py`：
  - `build_activation_event_body` 增加可选参数 `event_region="app"`、`event_type="view"`、`event_extra_detail=None`，默认值保持现状（`app` 域 + `view` + 空 detail）。
  - `post_activation_event` 增加可选参数 `headers: dict | None`，传入完整公共头与 `Authorization`；未传入时保持最小 `Content-Type`。
  - 新增 `resolve_login_provider(provider)`：`zai` → `"z.ai"`，`bigmodel` → `"bigmodel"`（对齐官方 `resolveProviderTelemetryLabel`）。
  - 修正 docstring 中「无 Authorization，官方端点不校验登录态」的错误结论。
- [ ] 验收：新增 `tests/unit/test_login_success_telemetry.py` 覆盖事件体字段、`event_region`/`login_provider` 参数化、Authorization 头；既有 `app_launch`/`app_daily_active` 行为回归（`event_region="app"`）。

## 组 2：事件接入（constants + claim + install，带 installed_at 一次性守卫）

- [ ] `app/constants.py`：新增 `LOGIN_EVENT`（`element="app_login_success"`、`region="app_profile"`、`type="view"`）。
- [ ] `app/claim.py`：`report_activation_events` 循环后，仅当 `account.installed_at is None` 时补发一次 `app_login_success`（用 `build_identity_headers(account)` + `Authorization: Bearer <jwt>` + `resolve_login_provider` 构造 detail）。
- [ ] `app/install.py`：安装序在 `ACTIVATION_ELEMENTS` 循环后，登录态（`user_id` 非空）时补发一次 `app_login_success`；匿名首启（`user_id=""`）不带 Authorization。
- [ ] 验收：新增单测覆盖 `installed_at` 去重（首次发、已安装跳过）、事件字段、Authorization 头、匿名路径不带 Authorization。

## 组 3：端到端链路验证

- [ ] 从入池入口走完整链路，用新账号 OAuth 授权后校验 `GET /api/v1/incentive/referral/summary` 的 `login_device=true`。
- [ ] 验证不消耗额度（事件上报为只读旁路，不调 `/messages`）。
- [ ] 回归：确认后台轮询（`include_claimable=False`）不触发 `app_login_success`。

## 收尾

- [ ] `ruff check app tests`、`mypy app/constants.py`、`pytest` 全量通过（分组验收累积后最终跑一次全量）。
- [ ] 本机 commit + push；VPS `git fetch && reset --hard` + `systemctl restart zcode2api`。
- [ ] 更新 `execution.md` 记录分组验收、真实验证结果与剩余限制。
