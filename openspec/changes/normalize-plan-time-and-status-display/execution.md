# 执行记录

## 第一组：时间与临期

2026-10-17 在隔离数据下执行：`test_plan_slots.py` 新回归先 9 失败/28 通过，DOM（UTC）先 5 失败/14 通过。修复后第一轮套餐/权益/bonus 67 项与 DOM 19 项通过。补充无效结束时间与受影响模型/网关冷却后，本机 **109 项通过**；VPS 独立候选的相同 **109 项通过，Ruff 通过**。全部前端 **20 项**分别在 UTC / America/Los_Angeles 通过；相关页面 19 项。两方案 strict 与差分检查通过。第一组验收通过，线上组待执行，不将规划 complete 当成交付完成。

修复保留完整结束秒值用于临期；统一北京时间短格式，跨年不猜年份。套餐开始/额度生效分开展示，状态和全部真实余额规则保持。自审补充旧账号状态栏/余额项秒、毫秒和 ISO 输入，先 3 失败/19 通过，统一前端时间解析后 **22 项页面 DOM / 23 项全部前端**在两个时区均通过。最终 VPS 候选为 `/root/plan-time-verify-20261017-gadisf5d/`，109 项与 Ruff 再验通过；首次候选记录保留。

证据：`data/diagnostics/plan-time-20261017/` 的 backend-red.xml/log、dom-red.log、related-green.xml/log、dom-final-utc.log、dom-final-la.log；VPS `/root/plan-time-verify-20261017-kv2ym3qs/` 与 `/root/plan-time-verify-20261017.json`。真实浏览器视觉、兼容性、真实到账恢复仍未验。线上与公网验收通过后补记录，不预写结果。
