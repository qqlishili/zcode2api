# Tasks

> 2026-10-16 核销复核：辅助函数与部分离线行为已确认，**整项未完成**。设计要求“等待结算”，当前代码和测试却为“等待生效”；此前按实现改写验收条件并勾选不成立，相关两项已撤回。另有 proposal 与 design 对 EXHAUSTED 的不同表述，需要对齐权威需求；不能用测试与代码一致代替符合需求。历史执行结果保留在 `execution.md`，当前状态见 [项目交接](../../../docs/HANDOFF.md)。

## 1. 新增 `_has_granted_entitlements` 辅助函数

- [x] 在 `app/quota.py` 新增模块级函数 `_has_granted_entitlements(plans) -> bool`：遍历 `plans[].entitlements[].grant_units > 0` 且套餐 `status=active`，返回是否存在已授予额度。

## 2. `fetch_quota` 的 `balances` 空分支区分文案

- [x] 按用户补充及 design 落实账号全部真实余额与权益语义：
  - 余额为空且有权益 → `EXHAUSTED`；权益全在未来生效时“额度已授予，等待生效”，已有生效或未知时间时“额度已授予，等待结算”，继续后台刷新。
  - 其他活动仍有真实余额 → 保持可选，按请求模型判断余额，不累加未到账授予量。
  - 否则 → 维持 `status=EXHAUSTED` + `last_error="额度已用完"`。

## 3. 测试

- [x] 验证未来/已生效/未知时间、权益撤销、多活动同模型/跨模型余额与 monitor 自动恢复；页面 JS 的 DOM 区分待生效/待结算、已到账余额和本号领取资格，不虚构 remaining 或比例。
- [x] 新增单测：`balances=[]` + `plans` 无 entitlements → `last_error == "额度已用完"`、`status == EXHAUSTED`。
- [x] 回归：`balances` 有值时的既有判定不变（has_remaining → ACTIVE、all_exhausted → EXHAUSTED）。

## 4. CI 与部署

- [x] `ruff check app tests`、`mypy app/constants.py`、`pytest` 全量通过。
- [x] 核对历史发布与当前部署版本；如需发布，遵循开发指南的 Git 快进流程，不以 `reset --hard` 覆盖未知改动。
- [ ] 真实验证：刷新对应账号（`balances=[]` + 有授予额度），应正确区分未来才生效与已经生效但尚未结算，且不把未结算额度当可消费余额；余额真实生成后的自动恢复尚待验收。
- [x] 对齐 proposal / design / tasks 的状态与文案要求，保留需求和当前实现的差异；未经确认不以当前实现改写验收条件。
