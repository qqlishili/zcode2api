# Design

## Context

见 proposal。当前后端已产出 quota_start 与 starts_at，但结束时间仅保留短文字；前端 expireInfo 用完整日期解析短文字，无法可靠判断临期。fmtDate 使用浏览器本地时区，与后端北京时间可能不同。

## Goals / Non-Goals

统一展示与计算依据；保持旧字段兼容，不改账号状态、领取、出口和调度逻辑，不以本次展示验收关闭真实余额结算恢复。

## Decisions

- 复用 quota.py 的提取入口，抽取秒/毫秒/ISO 转秒与短格式 helper。starts_at、quota_start_ts 保留，新增可选 expire_ts / 模型项 expires_at_ts；保留 expire / expires_at 短文字。仅有短文字的旧记录不推测年份，保持未知提醒。
- 时间比较一律用完整秒值。前端用固定 UTC+8 输出已有 MM-DD HH:MM；不新增日期库或配置。拒绝通过给短文字补当前年份恢复提醒，因为跨年会误判。
- 保留旧额度字段兼容，卡片不再拼接额度生效区间；活动有效期严格取活动 starts_at / ends_at，不用 expire 的余额回填或 effective_at 替代。模型项自己的到期及待生效时间保留，可用状态不变。关联方案承接同一取值契约与排序。
- 验收依次为后端与 DOM、VPS 真实 API/上线 JS、公网插件完整链路；不重复全量验证不相关模块。

## Risks / Trade-offs

- [旧持久化槽位无完整结束时间] → 文字继续可见，临期未知；刷新余额后补齐，不迁移或重写全库。
- [短格式隐藏年份] → 只用于显示，比较用秒值，覆盖跨年测试。
- [DOM 无法证明真实浏览器布局] → 按用户确认的无浏览器口径，明确保留视觉与兼容性未验。

## Migration Plan

局部回归与静态检查通过后逐文件提交，推送 main 并在 VPS 原目录 ff-only；保全旧代码与配置哈希，在途为零后仅重启网关。刷新指定两个账号（include_claimable=false），验证真实 API、上线页面与插件普通/SSE。失败按旧提交回退代码，不回滚账号库。交接与 execution 同步，不归档。
