## Purpose

让管理者以统一北京时间区分活动有效期、模型额度到期与权益生效时刻，并依据对应完整时间判断提醒，避免不同层级时间混用。

## ADDED Requirements

### Requirement: Plan times use one timezone and format
管理页面 SHALL 将套餐开始、额度生效和结束时间统一显示为北京时间 `MM-DD HH:MM`。API MUST 保留对应完整时间戳用于比较；秒、毫秒和带时区 ISO 输入表示同一时刻时必须得到相同结果。缺失或无效时间 MUST 保持未知。

#### Scenario: Equivalent timestamps
- **WHEN** 上游分别提供秒、毫秒或 ISO 时间，且浏览器位于不同的本地时区
- **THEN** 页面显示相同北京时间，格式一致，不出现 Invalid Date

### Requirement: Activity period uses activity boundaries
卡片 SHALL 按权益 period 展示活动有效期：daily 取 starts_at 至 ends_at；daily_night 取 effective_at 至 ends_at，effective_at 归零后仅从同 user_plan_id、entitlement_id 的桶 period_start 取本轮开始；one_time 取 effective_at 至 ends_at。未知或冲突端点 SHALL 显示 —，MUST NOT 跨实例、权益补值。名称和有效期 SHALL 同行且不换行，额度列 SHALL 在原居中页面内加宽至400px，并通过8px间距完整容纳六列；页面 MUST 保持1280px上限与两侧留白。未识别 period 沿用套餐起止与原模型到期展示。

#### Scenario: Night activity
- **WHEN** 套餐下午开始而额度在当日 23:00 生效
- **THEN** 开始显示权益的 23:00；归零且已生成同实例同权益桶后仍显示该桶 23:00，右下角显示活动 ends_at。未到账授予量不计入余额，其他活动余额保持可见

#### Scenario: No entitlement effective time
- **WHEN** one_time 权益无有效生效时间而套餐 starts_at 或桶 period_start 已知
- **THEN** 活动开始显示未知，不用这些字段替代；daily 使用套餐 starts_at

### Requirement: Quota corner distinguishes daily expiry and activity deadline
daily 的余额行右下角 SHALL 使用对应桶 expires_at，标明今日额度到期；daily_night/one_time 的余额与授予行 SHALL 使用同套餐 ends_at，标明活动截止，缺失 SHALL 显示未知而不回退桶结束。额度数值、进度条、未生效/待结算判断与账号状态 MUST 保持既有逻辑。

#### Scenario: Different bucket and plan end
- **WHEN** Global 或 Weekend 的桶 expires_at 与套餐 ends_at 不同
- **THEN** 右下角仍显示 ends_at，Start 仍显示自己的 expires_at

### Requirement: Expiry warnings use complete timestamps
套餐结束提醒 SHALL 根据完整时间戳判断七天内到期，并保留 `MM-DD HH:MM`。旧数据仅有无年份短字符串时 MUST 保留原文字而不猜年份或虚构临期状态。

#### Scenario: Year boundary
- **WHEN** 当前为年末，套餐在次年七天内结束
- **THEN** 正确显示次年短时间并标临期，不以当前年份解析短文字

#### Scenario: Legacy short expiry
- **WHEN** 旧槽位只有 `MM-DD HH:MM` 结束文字
- **THEN** 原文字完整显示，无法确认的临期状态保持未知
