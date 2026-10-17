## Purpose

以当前上游结算的全部模型余额作为账号可消费额度的依据，区分已授予但尚未生效或尚未结算的权益，持续刷新并安全恢复调度资格，避免将活动授予量误作可用余额。

## ADDED Requirements

### Requirement: Account availability follows all settled balances
系统 SHALL 根据账号全部真实结算余额判断可选性；任一模型仍有余额时保持或恢复可选，不因另一个活动尚未生效而停用整个账号。待生效或待结算的授予量 MUST NOT 计入可用余额，模型余额排除仍遵循既有规则。

#### Scenario: Another activity has usable balance
- **WHEN** Global Build 尚未生效，账号其他活动同模型或其他模型仍有真实余额
- **THEN** 账号保持 ACTIVE，真实余量不包含 Global Build 授予量，请求模型按已有余额规则筛选

#### Scenario: All settled buckets are exhausted
- **WHEN** 账号全部真实余额桶的剩余量均为零
- **THEN** 账号不可选，不能用授予量使其恢复可选

### Requirement: Unsettled entitlements remain refreshable
系统 SHALL 在真实余额为空且 active 套餐有正数授予量时保持账号不可选并继续后台刷新；权益全部在未来生效时提示“额度已授予，等待生效”，任一权益时间已到或未知时提示“额度已授予，等待结算”。无授予权益时提示“额度已用完”。

#### Scenario: Entitlements are not effective yet
- **WHEN** 余额为空且全部有效授予权益的生效时间在未来
- **THEN** 账号为 EXHAUSTED、quota 为空、保留授予量展示且提示等待生效

#### Scenario: Effective time reached but balance absent
- **WHEN** 余额为空且任一有效授予权益已经生效或未提供生效时间
- **THEN** 账号为 EXHAUSTED、quota 为空并提示等待结算，后台仍刷新该账号

#### Scenario: Entitlement disappears
- **WHEN** 待结算账号刷新后余额及有效授予权益均为空
- **THEN** 清除过时的待结算文案和授予槽位，显示额度已用完

### Requirement: Confirmed balance restores eligibility
系统 SHALL 在后台刷新确认真实余额生成且有剩余量后自动恢复 EXHAUSTED 账号，并清除过时的等待文案与已结算权益的待结算展示。已有冷却、凭证失效、风控及手动停用门禁 MUST 保持。

#### Scenario: Background settlement recovery
- **WHEN** 待结算账号的后续后台刷新得到正数真实余额
- **THEN** 账号恢复 ACTIVE、可选且清除等待文案，不再重复展示同套餐同模型的待结算授予量

#### Scenario: Protected account cannot be revived
- **WHEN** 账号处于有效冷却、凭证失效或手动停用状态
- **THEN** 新余额不绕过既有门禁使其恢复调度
