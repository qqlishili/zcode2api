## Purpose

让管理者识别每个活动本身的开始与结束时间，并先查看已开始的活动，再查看开始时间未知及未来活动；模型额度和待生效权益另行表达。

## ADDED Requirements

### Requirement: Plan period remains visible
活动卡片 SHALL 按 period 展示「活动有效期 start ~ end」：daily 开始取套餐 starts_at；daily_night 先取权益 effective_at，归零后取同 user_plan_id、entitlement_id 的余额桶 period_start；one_time 只取权益 effective_at。结束 SHALL 取同套餐 ends_at。未知、冲突或不唯一的开始 SHALL 显示 —，MUST NOT 跨实例、权益补值；未识别周期沿用套餐起止。名称与时间 SHALL 在同一卡头且不换行，在原居中页面内通过分配列宽容纳全部六列，并保留两侧留白。卡片级「套餐期」与「额度生效期」MUST NOT 再出现。

#### Scenario: Different start and effective times
- **WHEN** 套餐开始时间早于额度生效时间
- **THEN** daily 使用套餐开始，daily_night/one_time 使用权益生效起点；夜间生效后 effective_at=0 时仅使用同实例、同权益桶的 period_start，不暗示整号不可用

#### Scenario: Same period
- **WHEN** 套餐开始时间与额度生效时间相同
- **THEN** 只显示一行活动有效期

#### Scenario: All columns remain visible on desktop
- **WHEN** 活动名称和有效期同行使额度列加宽
- **THEN** 账号页保持原max-width:1280px、居中及两侧28px内边距；额度列400px、间距8px，六列含内边距/边框总宽1220px不超出原1224px内容区；卡头仍不换行，不通过全宽铺屏解决

#### Scenario: Missing plan start
- **WHEN** 对应周期所需的开始依据缺失而套餐结束已知
- **THEN** 开始显示未知，保留套餐结束，不借用其它活动或权益的桶

#### Scenario: Balance expires before activity
- **WHEN** 活动 ends_at 晚于某模型余额的 expires_at，或活动结束时间缺失
- **THEN** 活动有效期结束只取 ends_at；daily 模型右下角显示桶 expires_at，daily_night/one_time 显示套餐 ends_at，缺失显示未知

### Requirement: Started plans precede future plans
页面 SHALL 将开始时间不晚于当前时间的已知套餐按开始时间升序排列，其后稳定排列开始时间未知的套餐，最后稳定排列未来套餐。相同开始时间 MUST 保持输入顺序，页面排序 MUST NOT 修改 API 的原始数组或按额度生效时间替代套餐开始时间。

#### Scenario: Mixed input
- **WHEN** 输入含未来、未知、较晚已开始、较早已开始和恰好现在开始的套餐
- **THEN** 已开始按时间升序，再未知，再未来，原始数组不变

#### Scenario: Night activity is already started
- **WHEN** 套餐开始已过而额度到夜间才生效
- **THEN** 该套餐按实际开始时间排列，同时额度单独显示未生效
