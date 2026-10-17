## Purpose

让管理者识别每个活动本身的开始与结束时间，并先查看已开始的活动，再查看开始时间未知及未来活动；模型额度和待生效权益另行表达。

## ADDED Requirements

### Requirement: Plan period remains visible
活动卡片 SHALL 只展示一行「活动有效期 start ~ end」，取同一上游活动记录的 starts_at / ends_at。未知的一端 SHALL 显示 —，MUST NOT 借用权益 effective_at、余额到期时间或续期时间。卡片级「套餐期」与「额度生效期」MUST NOT 再出现；模型余额到期及待生效权益明细 SHALL 独立保留。

#### Scenario: Different start and effective times
- **WHEN** 套餐开始时间早于额度生效时间
- **THEN** 活动有效期仍以套餐开始为起点，权益明细单独表达未生效，不暗示整号不可用

#### Scenario: Same period
- **WHEN** 套餐开始时间与额度生效时间相同
- **THEN** 只显示一行活动有效期

#### Scenario: Missing plan start
- **WHEN** 套餐开始时间缺失而结束时间已知
- **THEN** 开始显示未知，保留结束时间

#### Scenario: Balance expires before activity
- **WHEN** 活动 ends_at 晚于某模型余额的 expires_at，或活动结束时间缺失
- **THEN** 活动有效期只使用活动 ends_at，缺失时显示 —，模型余额仍显示自己的到期时间

### Requirement: Started plans precede future plans
页面 SHALL 将开始时间不晚于当前时间的已知套餐按开始时间升序排列，其后稳定排列开始时间未知的套餐，最后稳定排列未来套餐。相同开始时间 MUST 保持输入顺序，页面排序 MUST NOT 修改 API 的原始数组或按额度生效时间替代套餐开始时间。

#### Scenario: Mixed input
- **WHEN** 输入含未来、未知、较晚已开始、较早已开始和恰好现在开始的套餐
- **THEN** 已开始按时间升序，再未知，再未来，原始数组不变

#### Scenario: Night activity is already started
- **WHEN** 套餐开始已过而额度到夜间才生效
- **THEN** 该套餐按实际开始时间排列，同时额度单独显示未生效
