## Purpose

让管理者同时识别每个套餐的开始与结束时间，并先查看已开始的套餐，再查看开始时间未知及未来套餐；额度生效时间独立表达，避免把领取后的套餐开始误认为额度已可消费。

## ADDED Requirements

### Requirement: Plan period remains visible
套餐卡片 SHALL 展示已知的套餐开始与结束时间，并以「套餐期」与不同的「额度生效期」区分。两者相同时 SHALL 只展示一份时间区间。未知的一端显示未知，不能拿额度生效时间冒充套餐开始。

#### Scenario: Different start and effective times
- **WHEN** 套餐开始时间早于额度生效时间
- **THEN** 同时显示套餐期与额度生效期，额度未来生效的样式不暗示整号不可用

#### Scenario: Same period
- **WHEN** 套餐开始时间与额度生效时间相同
- **THEN** 显示套餐期且不重复显示相同额度生效期

#### Scenario: Missing plan start
- **WHEN** 套餐开始时间缺失而结束时间已知
- **THEN** 开始显示未知，保留结束时间

### Requirement: Started plans precede future plans
页面 SHALL 将开始时间不晚于当前时间的已知套餐按开始时间升序排列，其后稳定排列开始时间未知的套餐，最后稳定排列未来套餐。相同开始时间 MUST 保持输入顺序，页面排序 MUST NOT 修改 API 的原始数组或按额度生效时间替代套餐开始时间。

#### Scenario: Mixed input
- **WHEN** 输入含未来、未知、较晚已开始、较早已开始和恰好现在开始的套餐
- **THEN** 已开始按时间升序，再未知，再未来，原始数组不变

#### Scenario: Night activity is already started
- **WHEN** 套餐开始已过而额度到夜间才生效
- **THEN** 该套餐按实际开始时间排列，同时额度单独显示未生效
