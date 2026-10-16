# Proposal

## Why

活动赠送额度（如「夜间蹬蹬卡」套餐 `zcode-v3-start-plan-referral`）激活后，其额度**先出现在 `plans[].entitlements[].grant_units`，`balances` 记录稍后才生成**（上游结算延迟）。

2026-10-14 实测铁证：账号 `国际-ok li` 的 `billing/balance` 返回：

```
plans[0].plan_id = zcode-v3-start-plan-referral（ZCode Global Build）
plans[0].status = active
plans[0].entitlements[0].grant_units = 100000000（1 亿，GLM-5.3-Flash，period=daily_night）
balances = []   ← 空！
```

而 `app/quota.py` 的 `fetch_quota` 只解析 `balances`，`balances` 为空时直接判 `Status.EXHAUSTED` + "额度已用完"。这导致：

1. 新领取的活动套餐，在 `balances` 结算前的窗口期内被误判为"额度已用完"。
2. 用户看到"额度已用完" → 禁用账号 → 禁用后不再轮询 → `balances` 结算后额度也永远看不到（死循环）。

参考 `z-accounts-gateway/src-tauri/src/quota.rs:1408` 的既有处理：当 `balances` 为空但 `plans[].entitlements` 有 `grant_units` 时，展示"已授予额度"，但不虚构剩余额度或使用百分比。

## What Changes

- 在 `fetch_quota` 的 `balances` 为空分支，先检查 `plans[].entitlements[].grant_units` 是否存在有效额度（> 0 且套餐 active）。
- 若存在，则**不判 `EXHAUSTED`**，保持当前状态（不写 "额度已用完"），保留 `plan_slots` 供前端展示"已授予额度"，并继续由后台轮询直到 `balances` 结算。
- 若不存在，维持原有行为（判 `EXHAUSTED`）。
- **不虚构剩余额度 / 使用百分比**（对齐 z-accounts-gateway 原则）：只表达"已授予、待结算"，`account.quota` 仍为空（因为物理上 `balances` 尚未结算，不编造 remaining）。

## Capabilities

### Modified Capabilities

- `quota-sync`（`fetch_quota` 的额度真相同步）：`balances` 为空但 `entitlements` 有已授予额度时，不再误判 `EXHAUSTED`，保持待结算状态并继续轮询。

## Impact

- 主要代码：`app/quota.py`（`fetch_quota` 的 `balances` 空分支）。
- 关联契约：`plan_slots` 的构建（`_build_plan_slots`）需在 `balances` 空但 `entitlements` 有额度时也保留套餐层级，供前端展示。可能涉及 `_build_plan_slots` 的调用位置调整。
- 测试：新增单测覆盖"balances 空 + entitlements 有 grant_units → 不判 EXHAUSTED"与"balances 空 + entitlements 无 → 仍判 EXHAUSTED"。
- 无数据库迁移、无新服务、无第三方依赖。
- 风险：`balances` 空但 `entitlements` 有额度的账号，其 `account.quota` 仍为空，可能影响依赖 `account.quota` 的下游（如选号逻辑）。需确认"待结算"账号不会被选号（避免用尚未结算的额度发对话）。

## Validation

- 单元测试：`balances=[]` + `entitlements[grant_units>0]` → status 不变（不 EXHAUSTED）、`last_error` 不写"额度已用完"；`balances=[]` + 无 entitlements → 仍 EXHAUSTED。
- 真实验证：`国际-ok li` 当前 `balances=[]` 但 `entitlements` 有 1 亿，落地后刷新该账号应不再显示"额度已用完"，而是"已授予额度待结算"。
- 选号安全：确认"待结算"账号不被选号（或明确其选号行为）。
