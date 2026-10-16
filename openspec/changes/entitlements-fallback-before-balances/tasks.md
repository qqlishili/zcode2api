# Tasks

## 1. 新增 `_has_granted_entitlements` 辅助函数

- [ ] 在 `app/quota.py` 新增模块级函数 `_has_granted_entitlements(plans) -> bool`：遍历 `plans[].entitlements[].grant_units > 0` 且套餐 `status=active`，返回是否存在已授予额度。

## 2. `fetch_quota` 的 `balances` 空分支区分文案

- [ ] 在 `balances` 空分支（`else`，约第 442 行）：
  - 若 `_has_granted_entitlements(account.plans)` 为 True → `status=EXHAUSTED` + `last_error="额度已授予，等待结算"`。
  - 否则 → 维持 `status=EXHAUSTED` + `last_error="额度已用完"`。

## 3. 测试

- [ ] 新增单测：`balances=[]` + `plans` 含 active 且 `entitlements[grant_units>0]` → `last_error == "额度已授予，等待结算"`、`status == EXHAUSTED`。
- [ ] 新增单测：`balances=[]` + `plans` 无 entitlements → `last_error == "额度已用完"`、`status == EXHAUSTED`。
- [ ] 回归：`balances` 有值时的既有判定不变（has_remaining → ACTIVE、all_exhausted → EXHAUSTED）。

## 4. CI 与部署

- [ ] `ruff check app tests`、`mypy app/constants.py`、`pytest` 全量通过。
- [ ] 本机 commit + push；VPS `git fetch && reset --hard` + `systemctl restart zcode2api`。
- [ ] 真实验证：刷新 `国际-ok li`（`balances=[]` + `entitlements` 有 1 亿），应显示"额度已授予，等待结算"而非"额度已用完"，且仍不可选。
