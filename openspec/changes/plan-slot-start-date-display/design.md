# Design

## Context

见 proposal。开始秒值已在 plan_slots；现有 quotaCell 使用副本排序，但未知开始时间比较为相等，可能阻止已知已开始套餐正确升序。planGroupHtml 的开始文字目前实际上是额度生效起点。

## Goals / Non-Goals

补全套餐期与确定性排序。复用关联时间方案的解析/格式与临期结果，不改 API 原顺序、后台选号优先级或真实额度状态。

## Decisions

- 套餐卡头显示「套餐期 start ~ end」；quota_start_ts 与 starts_at 不同时才另列「额度生效期」。缺一端以 — 表示，保留原卡片样式和 pending-line / warn-line；同一区间不重复。
- 排序键分三组：已知已开始、未知、未来。只在第一组比较 starts_at，其他组保持稳定原序。未知放在中间保证已开始时间比较满足一致顺序，避免比较器对未知始终返回 0。
- 不按 quota_start_ts 重新定义未开始：夜间活动套餐可能已开始而额度未生效。测试以两种时间不同的卡片锁住边界。
- 第一组补充既有 Node/happy-dom DOM 测试与后端断言；第二、三组与关联时间方案共享真实上线验证，不重复发相同上游请求。

## Risks / Trade-offs

- [新增区间占用空间] → 复用已有字体、间距与卡头，时间在独立行展示，长名称仍保留 title；不引入组件或依赖。真实视觉未验。
- [旧记录缺 starts_at] → 显示未知并稳定排序，不借用额度生效时间冒充。

## Migration Plan

与 normalize-plan-time-and-status-display 同批发布，复用其配置保护、回退与线上验收。只刷新两个指定账号，不创建生产套餐或领取活动。交接分别记录两个方案任务，保留独立未验事项，不归档。
