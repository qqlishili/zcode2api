## Purpose

让管理者按套餐和模型识别真实余额与活动授予权益，分别表达待生效、待结算和已到账额度，并保持本号领取资格的来源可信，避免因某个活动的时间状态误判整个账号或可消费余量。

## ADDED Requirements

### Requirement: Entitlement timing and settlement are distinct
页面 SHALL 对没有对应真实余额桶的正数授予权益展示授予量；effective_at 在未来才标“未生效”，时间已到或未知时标“待结算”。授予量 MUST NOT 展示成剩余额度或使用百分比；已有槽位与旧数据回退都必须区分这两种状态。

#### Scenario: Future entitlement
- **WHEN** 权益生效时间晚于当前时间且尚未生成余额桶
- **THEN** 显示授予 token 数、生效时间和未生效标识

#### Scenario: Effective entitlement awaiting settlement
- **WHEN** 权益生效时间已到或未知，且尚无对应余额桶
- **THEN** 显示授予 token 数与待结算，不标未生效或额度未获取

### Requirement: Other plan balances remain visible
页面 SHALL 分别展示各套餐的真实余额与待生效权益。同套餐同模型已结算的授予量不重复显示。账号仍有其他活动真实余额时正常状态 MUST 保持；无真实余额的等待状态应显示待生效或待结算徽章。

#### Scenario: Same model in separate activities
- **WHEN** Global Build 的 Flash 尚未生效而 Weekend Build 的 Flash 仍有余额
- **THEN** 分别展示两套餐，真实余额不累加 Global Build 授予量，账号仍显示正常

#### Scenario: Settlement reflected on refresh
- **WHEN** 后续刷新取得对应套餐模型的真实余额桶
- **THEN** 显示真实剩余量且移除对应待结算授予行

#### Scenario: Legacy aggregate data
- **WHEN** 旧记录无套餐槽位且只有按模型合并余额
- **THEN** 保留合并余额，已有余额模型不重复显示已生效授予量；未结算且无对应模型余额的权益显示等待状态

### Requirement: Claim eligibility belongs to the account
页面 SHALL 仅依据本号 preview 返回的待领套餐展示领取入口，并排除本号已持有的套餐；池内其他账号持有某活动不能作为本号有资格的证据。

#### Scenario: No eligibility inferred from the pool
- **WHEN** 本号 preview 无可领套餐
- **THEN** 不因其他账号持有活动而显示可领取

#### Scenario: Already held plan
- **WHEN** 本号 preview 出现已经持有的套餐
- **THEN** 页面不重复显示该套餐为可领取
