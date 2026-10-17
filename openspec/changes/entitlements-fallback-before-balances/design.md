# Design

## 目标

活动赠送套餐（如夜间蹬蹬卡）激活后，额度先出现在 `plans[].entitlements[].grant_units`，`balances` 结算延迟。`fetch_quota` 在 `balances` 为空时误判 `EXHAUSTED` + "额度已用完"，误导用户并触发"禁用 → 不再轮询 → 永远看不到额度"死循环。

本变更按账号全部真实余额判定状态。余额为空但有授予权益时不写“额度已用完”：全部权益在未来生效则“额度已授予，等待生效”，任一权益已经生效或时间未知则“额度已授予，等待结算”；保持 `EXHAUSTED`（不可选）并继续后台刷新。其他活动仍有真实余额则保持 `ACTIVE`，不受待生效活动影响。

## 关键事实（实测铁证）

以下为 2026-10-14 的历史余额空样本；2026-10-17 用户指定的两个账号已有其他活动余额，不能沿用该历史状态停用整号。

- 账号 `国际-ok li`：`plans[0].plan_id=zcode-v3-start-plan-referral`、`status=active`、`entitlements[0].grant_units=100000000`、`balances=[]`。
- 该账号发对话返回 `1005 exceed quota limit`——说明 `entitlements` 只是"已授予资格"，`balances` 未结算时**实际不可消费**。
- 因此全部真实余额为空的"待结算"账号必须保持不可选（否则选号后发对话会 1005 失败），但文案不能误导为"额度已用完"。

## 历史行为（原始方案基线）

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

- 若 `_has_granted_entitlements(account.plans)` 为 True → `status=EXHAUSTED`（保持不可选）；利用同次响应构建的槽位权益时间区分“等待生效”与“等待结算”。
- 否则 → 维持现状 `status=EXHAUSTED` + `last_error="额度已用完"`。

### D2：保持 EXHAUSTED（不可选），靠后台轮询等 balances 结算

- `selection_exclusion`：`status==EXHAUSTED` → 排除（不可选）✅ 无需改动。
- `allows_billing`：不排除 EXHAUSTED → 后台轮询继续刷新 ✅ 无需改动。
- `balances` 结算后 `has_remaining` → 自动转 ACTIVE（现有逻辑第 435 行）✅ 无需改动。

### D3：保留已实现的授予量展示，不虚构可用余额

`account.quota` 在真实余额为空时保持 `{}`。槽位已有的 `pending` 保留授予量和生效时间，不等同于可用余额；页面逐权益判断时间，旧数据回退也展示已授予待结算，避免“额度未获取”。账号徽章按本次已确认的待生效/待结算文案区分，不改数据库状态枚举。

### D4：多活动余额与恢复边界

全部余额仍沿用现有逻辑：任一模型有余量即保留/恢复 ACTIVE，所有桶用完则 EXHAUSTED；待生效的同名模型权益不能遮蔽其他活动的真实余额，也不能增加 remaining。后台 monitor 刷新 EXHAUSTED，真实余额生成后自动转 ACTIVE 并清除旧文案；冷却、无效凭证和手动停用门禁保持。权益移除后清除过时的待结算文案。真实到账时序不能由夹具代替。

## 风险与缓解

| 风险 | 缓解 |
|---|---|
| "待结算"账号被选号 → 发对话 1005 | 保持 EXHAUSTED（`selection_exclusion` 排除），不可选。 |
| "待结算"账号不再被轮询 → 永远等不到 balances | `allows_billing` 不排除 EXHAUSTED，后台轮询继续。 |
| 前端仍显示"额度已用完"误导 | `last_error` 改为"额度已授予，等待结算"，前端据 last_error 展示。 |
