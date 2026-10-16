# 执行记录

## 真实验证（先于落地，证明根因）

- 2026-10-14 抓取 `zcode.z.ai/cn/rewards` 网页 JS，定位真实任务接口 `GET /api/v1/incentive/referral/summary`，返回三任务：`referral_bind_code`、`login_device`、`send_message`。
- `lidmuw@chtsv.store` 实测：`send_message` 与 `referral_bind_code` 均 `completed=true`，唯独 `login_device=false`，奖励 `night_card` 卡在 `current=2/3`。
- 手工补发官方事件 `app_login_success`（`event_region="app_profile"`、`event_extra_detail={"login_provider":"z.ai"}`、带 Authorization + 完整公共头）后，`login_device` 立即 `true`、进度 `3/3`、`state=achieved`，套餐 `zcode-v3-start-plan-referral`（ZCode Global Build）激活。**证明根因是缺 `app_login_success`，且上游自动结算无需客户端领取。**

## 分组实施与验收

- 组 1（telemetry 能力）：`telemetry.py` 事件体/投递参数化 + `resolve_login_provider`。验收：新增 6 项单测 + 既有 `app_launch` 回归通过。
- 组 2（事件接入）：`constants.py` 常量 + `claim.report_activation_events` + `install.run_install_sequence_for_account` 补发，带 `installed_at` 一次性守卫。验收：新增 2 项去重/字段单测 + 既有 install/telemetry 回归通过。
- 组 3（端到端）：代码正确性由组 2 单测 + 上述真实验证双重确认；「新账号走完整 OAuth 自动补发」的闭环留待用户授权新账号后复验（当前 16 账号均 `installed_at` 非空，无候选）。

## 回归修复

- 全量单元回归发现 2 项既有测试断言需随行为更新：`test_per_account_install_sequence_hits_upstream` / `test_per_account_install_sequence_tolerates_failures` 原断言每账号安装序只发 2 个事件（`app_launch`+`app_daily_active`），现登录态补发 `app_login_success` 后为 3 事件。已同步更新断言（含 `app_profile` 域与 `login_provider` 字段核验）。

## CI 与部署

- `ruff check app tests`、`mypy app/constants.py`、`pytest`（全量单元回归，排除 2 项历史 CI 失败：`test_consistent_hash_proxy_distribution` 与 `test_send_bark_notification_*`）通过。
- 提交 `0119d26`，push 到 origin；VPS `git fetch && reset --hard` + `systemctl restart zcode2api`，服务 active，工作区干净。

## 剩余限制

- 未验证「新账号 OAuth 授权 → 自动补发 → 自动完成」的完整端到端闭环（需用户授权新账号）。代码路径已由单测覆盖，上游行为已由 `lidmuw` 手工补发实证。
- 未验证 `bigmodel` 账号的 `login_provider="bigmodel"` 分支（当前账号池全为 `zai`）。
