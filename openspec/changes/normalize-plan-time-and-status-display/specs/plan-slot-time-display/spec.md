## Purpose

让管理者以统一的北京时间识别套餐与额度的生效区间，并依据完整时间判断到期提醒，避免领取时间、额度生效时间、浏览器时区和无年份字符串混用造成错误判断。

## ADDED Requirements

### Requirement: Plan times use one timezone and format
管理页面 SHALL 将套餐开始、额度生效和结束时间统一显示为北京时间 `MM-DD HH:MM`。API MUST 保留对应完整时间戳用于比较；秒、毫秒和带时区 ISO 输入表示同一时刻时必须得到相同结果。缺失或无效时间 MUST 保持未知。

#### Scenario: Equivalent timestamps
- **WHEN** 上游分别提供秒、毫秒或 ISO 时间，且浏览器位于不同的本地时区
- **THEN** 页面显示相同北京时间，格式一致，不出现 Invalid Date

### Requirement: Quota effective time is distinct from plan start
额度生效期 SHALL 以权益最早有效的 effective_at 为起点，无有效值才回退套餐开始时间，以套餐结束时间为终点。未知起点 MUST NOT 被猜测为当前时间；额度授予与真实余额状态继续遵守已上线规则。

#### Scenario: Night activity
- **WHEN** 套餐下午开始而额度在当日 23:00 生效
- **THEN** 额度生效期显示 23:00，未到账授予量不计入真实余额，其他活动余额保持可见

#### Scenario: No entitlement effective time
- **WHEN** 权益无有效生效时间而套餐开始时间已知
- **THEN** 额度生效期回退套餐开始时间

### Requirement: Expiry warnings use complete timestamps
套餐结束提醒 SHALL 根据完整时间戳判断七天内到期，并保留 `MM-DD HH:MM`。旧数据仅有无年份短字符串时 MUST 保留原文字而不猜年份或虚构临期状态。

#### Scenario: Year boundary
- **WHEN** 当前为年末，套餐在次年七天内结束
- **THEN** 正确显示次年短时间并标临期，不以当前年份解析短文字

#### Scenario: Legacy short expiry
- **WHEN** 旧槽位只有 `MM-DD HH:MM` 结束文字
- **THEN** 原文字完整显示，无法确认的临期状态保持未知
