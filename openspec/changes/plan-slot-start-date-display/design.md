# Design

## Context

见 proposal。开始秒值已在 plan_slots；现有 quotaCell 使用副本排序，但未知开始时间比较为相等，可能阻止已知已开始套餐正确升序。planGroupHtml 的开始文字目前实际上是额度生效起点。

## Goals / Non-Goals

补全套餐期与确定性排序。复用关联时间方案的解析/格式与临期结果，不改 API 原顺序、后台选号优先级或真实额度状态。

## Decisions

- starts_at / ends_at 保留原套餐值及排序用途，新增 activity_start_ts 专供展示。daily 取 starts_at；daily_night 取 effective_at，归零后严格关联同 user_plan_id、entitlement_id 的桶 period_start；one_time 取 effective_at，不用 period_start 覆盖。多权益起点不一致或缺依据显示 —，不取最早值冒充统一起点。结束始终取套餐 ends_at，常规孤儿卡不生成活动区间。
- pending 和余额项保留权益 period：daily 右下角为对应桶 expires_at（今日额度到期）；daily_night/one_time 为套餐 ends_at（活动截止），未到账授予量及状态保持原逻辑，生效信息保留在提示中。
- 排序键分三组：已知已开始、未知、未来。只在第一组比较 starts_at，其他组保持稳定原序。未知放在中间保证已开始时间比较满足一致顺序，避免比较器对未知始终返回 0。
- 不按 quota_start_ts 重新定义未开始：夜间活动套餐可能已开始而额度未生效。测试以两种时间不同的卡片锁住边界。
- 第一组补充既有 Node/happy-dom DOM 测试与后端断言；第二、三组与关联时间方案共享真实上线验证，不重复发相同上游请求。

## Risks / Trade-offs

- [同行区间占用空间] → 保留原1280px居中页面、两侧28px内边距与外部留白；六列宽度沿用原值，额度列由320增至400px、间距14改为8px，含边框和内边距合计1220px，落在1224px内容区内。卡头不换行，监控页保持1080px表格。更窄窗口仍保留横向滚动，真实浏览器视觉未验。
- [旧记录缺活动起止字段] → 显示未知，等待正常刷新；不借用权益或余额时间冒充。

## Migration Plan

与 normalize-plan-time-and-status-display 同批发布，复用其配置保护、回退与线上验收。只刷新两个指定账号，不创建生产套餐或领取活动。交接分别记录两个方案任务，保留独立未验事项，不归档。
