# Design

## 目标

活动赠送套餐（如夜间蹬蹬卡）激活后，额度先出现在 `plans[].entitlements[].grant_units`，`balances` 结算延迟。`fetch_quota` 在 `balances` 为空时误判 `EXHAUSTED` + "额度已用完"，误导用户并触发"禁用 → 不再轮询 → 永远看不到额度"死循环。

本变更只做最小区分：`balances` 空但 `entitlements` 有已授予额度时，**不写"额度已用完"**，改为中性文案"额度已授予，等待结算"，保持 `EXHAUSTED`（不可选）并继续后台轮询。

## 关键事实（实测铁证）

- 账号 `国际-ok li`：`plans[0].plan_id=zcode-v3-start-plan-referral`、`status=active`、`entitlements[0].grant_units=100000000`、`balances=[]`。
- 该账号发对话返回 `1005 exceed quota limit`——说明 `entitlements` 只是"已授予资格"，`balances` 未结算时**实际不可消费**。
- 因此"待结算"账号**必须保持不可选**（否则选号后发对话会 1005 失败），但**文案不能误导为"额度已用完"**。

## 现有行为

`app/quota.py` `fetch_quota` 的 `balances` 空分支（第 442-449 行）：

```python
else:  # balances 空
    if account.status == Status.ACTIVE:
        account.status = Status.EXHAUSTED
        account.last_error = "额度已用完"
    account.plan_slots = []
```

问题：`balances` 空 → 无差别判 EXHAUSTED + "额度已用完"，无法区分"真用完"与"待结算"。

## 设计决策

### D1：`balances` 空分支区分「待结算」与「真用完」

在 `balances` 空分支，先检查 `plans`（`account.plans`，已由 `billing/current` 或 `billing/balance` 的 `plans` 填充）里是否存在**有效已授予额度**：

```python
def _has_granted_entitlements(plans) -> bool:
    """plans[].entitlements[].grant_units > 0 且套餐 active 即视为「已授予额度」。"""
    for p in plans or []:
        if str(p.get("status") or "").lower() != "active":
            continue
        for ent in p.get("entitlements") or []:
            try:
                if float(ent.get("grant_units") or 0) > 0:
                    return True
            except (ValueError, TypeError):
                continue
    return False
```

- 若 `_has_granted_entitlements(account.plans)` 为 True → `status=EXHAUSTED`（保持不可选）+ `last_error="额度已授予，等待结算"`。
- 否则 → 维持现状 `status=EXHAUSTED` + `last_error="额度已用完"`。

### D2：保持 EXHAUSTED（不可选），靠后台轮询等 balances 结算

- `selection_exclusion`：`status==EXHAUSTED` → 排除（不可选）✅ 无需改动。
- `allows_billing`：不排除 EXHAUSTED → 后台轮询继续刷新 ✅ 无需改动。
- `balances` 结算后 `has_remaining` → 自动转 ACTIVE（现有逻辑第 435 行）✅ 无需改动。

### D3：不虚构剩余额度（不做 plan_slots 展示增强）

`account.quota` 在 `balances` 空时仍为空（物理事实：balances 未结算，无 remaining/used）。本次**不做** `_build_plan_slots` 从 `entitlements` 提取 `grant_units` 的展示增强——那是锦上添花，会扩大改动范围。核心痛点是"误判 EXHAUSTED 误导用户"，由 D1 的 `last_error` 文案区分解决。`plan_slots` 清空逻辑保持不变（减法优先）。

## 风险与缓解

| 风险 | 缓解 |
|---|---|
| "待结算"账号被选号 → 发对话 1005 | 保持 EXHAUSTED（`selection_exclusion` 排除），不可选。 |
| "待结算"账号不再被轮询 → 永远等不到 balances | `allows_billing` 不排除 EXHAUSTED，后台轮询继续。 |
| 前端仍显示"额度已用完"误导 | `last_error` 改为"额度已授予，等待结算"，前端据 last_error 展示。 |
