# 执行记录

## 2026-10-17 实施（进行中）

- 复用真实 accounts.html 内嵌 JS 与现有 happy-dom/Node test runner，固定时间红灯 4 失败/2 通过：已到生效时间仍显示未生效，旧数据回退缺失待结算展示。修复后 13 项 DOM 通过。
- 统一两条展示路径的授予行，逐权益比较时间；状态徽章区分待生效/待结算，其他活动有余额仍显示正常。保留本号 preview 资格过滤，不伪造 remaining、百分比或已到账。
- 页面 13 项 DOM、关联额度/模型回归、Ruff 和两方案 strict 已通过；本轮全量中发现的旧断言、测试共享 asyncio 和 Mihomo 客户端事件循环干扰已定位，修复测试夹具后集中复验。失败历史不删除、不改为通过。
- 真实管理 API 与此前已上线页面验证两个指定账号；Global Build 未来生效、其他套餐余额可见，账号均 active。当前新页面尚未发布，集中 CI 与发布验收继续进行。
- 与额度状态方案共用 `data/diagnostics/entitlements-20261017/` 及 VPS `/root/entitlements-verify-20261017-l9kcendz/`、`/root/entitlements-live-20261017/`。视觉和真实浏览器兼容性未验证，新活动发码/真实结算主线不由此核销。
