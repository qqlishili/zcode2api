# 执行记录

## 真实验证（先于落地，铁证根因）

- 账号 `国际-ok li` 实测：`billing/balance` 返回 `plans[0].plan_id=zcode-v3-start-plan-referral`（夜间蹬蹬卡）、`status=active`、`entitlements[0].grant_units=100000000`（1 亿 GLM-5.3-Flash，period=daily_night），但 `balances=[]`（空）。
- 该账号发对话返回 `1005 exceed quota limit`——证明 `entitlements` 只是「已授予资格」，`balances` 未结算时**实际不可消费**。
- 旧逻辑 `fetch_quota` 在 `balances` 空时无差别判 `EXHAUSTED` + "额度已用完"，误导用户禁用并陷入死循环。

## 分组实施与验收

- 新增 `_has_granted_entitlements` 纯函数 + `fetch_quota` 的 `balances` 空分支区分「待结算」vs「真用完」。
- 单测：5 项纯函数边界 + 2 项 fetch_quota 集成（balances 空 + 有/无 entitlements）。
- 全量单元回归通过（exit 0）。

## CI 与部署

- `ruff check app tests`、`mypy app/constants.py`、`pytest` 全量通过。
- 提交 `45d00dd`，push 到 origin；VPS `reset --hard` + `systemctl restart zcode2api`，服务 active。

## 真实验证（部署后）

- 刷新 `国际-ok li`：`last_error` 从「额度已用完」变为「额度已授予，等待结算」，`status=EXHAUSTED`（不可选），`quota={}`（不虚构剩余额度）。

## 剩余限制

- 未验证 `balances` 结算后「待结算 → ACTIVE」的自动恢复闭环（需等该账号 balances 实际生成，属上游时序，非代码可加速）。
- `billing/balance` 带 `?app_version=`（P1）未做，留待后续确认是否有差异后再决定。
