# Proposal

> 2026-10-17 继续实施：按用户补充，以账号全部真实余额判断可选性，不能因某个活动待生效而停用整个账号。下文统一采用 design 的不可消费约束；旧“保持原状态”要求被此规则取代。

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
- 若所有真实余额均为空且有授予额度，保持 `EXHAUSTED`（不可选）并继续后台刷新；按权益生效时间显示“等待生效”或“等待结算”，保留 `plan_slots` 展示授予量。
- 任一其他活动/套餐有真实剩余余额时，账号仍为 `ACTIVE`，按请求模型的实际余额选号；待生效的授予量不计入可用余额。
- 若不存在，维持原有行为（判 `EXHAUSTED`）。
- **不虚构剩余额度 / 使用百分比**（对齐 z-accounts-gateway 原则）：只表达"已授予、待结算"，`account.quota` 仍为空（因为物理上 `balances` 尚未结算，不编造 remaining）。

## Capabilities

### New Capabilities

- `quota-sync`：以全部真实余额判断账号可选性，区分授予权益的待生效/待结算展示，并在余额到账后自动恢复。

## Impact

- 主要代码：`app/quota.py`（`fetch_quota` 的 `balances` 空分支）。
- 关联契约：`plan_slots` 的构建（`_build_plan_slots`）需在 `balances` 空但 `entitlements` 有额度时也保留套餐层级，供前端展示。可能涉及 `_build_plan_slots` 的调用位置调整。
- 测试：覆盖余额空且有授予量时保持 EXHAUSTED 并区分等待生效/结算、无授予量时显示用完，以及其他活动有真实余额时保持 ACTIVE。
- 无数据库迁移、无新服务、无第三方依赖。
- 风险：`balances` 空但 `entitlements` 有额度的账号，其 `account.quota` 仍为空，可能影响依赖 `account.quota` 的下游（如选号逻辑）。需确认"待结算"账号不会被选号（避免用尚未结算的额度发对话）。

## Validation

- 单元测试：余额为空时，未来权益显示“等待生效”，已到生效时间或未知时间的权益显示“等待结算”；两者均 EXHAUSTED 并允许后台刷新。无权益仍显示“额度已用完”。
- 多活动/多模型：其他活动仍有余额时保持 ACTIVE，不将授予量累加为 remaining；只对已有真实零余量的对应模型执行既有排除。
- 真实验证：刷新用户指定的两个账号，Global Build 待生效与其他活动余额分别展示；同一权益真实余额生成后的自动恢复另行记录，不用 Mock 冒充。
- 页面使用真实 API + 页面 JS 的 DOM 验收；未来时间标“未生效”，已到时间但无余额标“待结算”，不虚构剩余比例。视觉与真实浏览器兼容性仍未验证。
