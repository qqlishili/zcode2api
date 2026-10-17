## Purpose

让管理者以统一北京时间区分活动有效期、模型额度到期与权益生效时刻，并依据对应完整时间判断提醒，避免不同层级时间混用。

## ADDED Requirements

### Requirement: Plan times use one timezone and format
管理页面 SHALL 将套餐开始、额度生效和结束时间统一显示为北京时间 `MM-DD HH:MM`。API MUST 保留对应完整时间戳用于比较；秒、毫秒和带时区 ISO 输入表示同一时刻时必须得到相同结果。缺失或无效时间 MUST 保持未知。

#### Scenario: Equivalent timestamps
- **WHEN** 上游分别提供秒、毫秒或 ISO 时间，且浏览器位于不同的本地时区
- **THEN** 页面显示相同北京时间，格式一致，不出现 Invalid Date

### Requirement: Activity period uses activity boundaries
卡片 SHALL 统一显示「活动有效期」，严格使用同一活动的 starts_at / ends_at，并以 — 表达缺失端点；MUST NOT 用权益 effective_at、余额到期或续期时间补齐。模型余额与权益行 SHALL 保留各自到期或生效时间；额度授予与真实余额状态继续遵守已上线规则。

#### Scenario: Night activity
- **WHEN** 套餐下午开始而额度在当日 23:00 生效
- **THEN** 活动有效期显示活动下午开始的时间，权益明细仍显示 23:00 生效；未到账授予量不计入余额，其他活动余额保持可见

#### Scenario: No entitlement effective time
- **WHEN** 权益无有效生效时间而套餐开始时间已知
- **THEN** 活动有效期仍直接使用活动起止，不生成卡片级额度生效期

### Requirement: Expiry warnings use complete timestamps
套餐结束提醒 SHALL 根据完整时间戳判断七天内到期，并保留 `MM-DD HH:MM`。旧数据仅有无年份短字符串时 MUST 保留原文字而不猜年份或虚构临期状态。

#### Scenario: Year boundary
- **WHEN** 当前为年末，套餐在次年七天内结束
- **THEN** 正确显示次年短时间并标临期，不以当前年份解析短文字

#### Scenario: Legacy short expiry
- **WHEN** 旧槽位只有 `MM-DD HH:MM` 结束文字
- **THEN** 原文字完整显示，无法确认的临期状态保持未知
