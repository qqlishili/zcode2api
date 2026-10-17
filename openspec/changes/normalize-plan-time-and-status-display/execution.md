# 执行记录

## 用户修订：活动有效期（2026-10-17）

与 `plan-slot-start-date-display` 共享修复与证据：活动起止只取同一活动 starts_at / ends_at，不再拼接额度生效区间，模型与 pending 明细保持。后端新增 2 失败复现，DOM 2 失败复现；独立候选相关 111 项、Ruff、前端全部 26 项及两规范 strict 通过，可用状态未改。证据目录本机 `data/diagnostics/activity-period-20261017/`、VPS `/root/activity-period-live-20261017/`；发布和上线验收尚在执行，详见关联方案新增记录。此前历史验收保留，不将其当作本轮验收。

## 第一组：时间与临期

2026-10-17 在隔离数据下执行：`test_plan_slots.py` 新回归先 9 失败/28 通过，DOM（UTC）先 5 失败/14 通过。修复后第一轮套餐/权益/bonus 67 项与 DOM 19 项通过。补充无效结束时间与受影响模型/网关冷却后，本机 **109 项通过**；VPS 独立候选的相同 **109 项通过，Ruff 通过**。全部前端初轮 **20 项**分别在 UTC / America/Los_Angeles 通过；相关页面 19 项。第一组当时仅完成隔离验收，后续真实验收见第二、三组，不将规划 complete 当成交付完成。

修复保留完整结束秒值用于临期；统一北京时间短格式，跨年不猜年份。套餐开始/额度生效分开展示，状态和全部真实余额规则保持。自审补充旧账号状态栏/余额项秒、毫秒和 ISO 输入，先 3 失败/19 通过，统一前端时间解析后 **22 项页面 DOM / 23 项全部前端**在两个时区均通过。最终 VPS 候选为 `/root/plan-time-verify-20261017-gadisf5d/`，109 项与 Ruff 再验通过；首次候选记录保留。

证据：`data/diagnostics/plan-time-20261017/` 的 backend-red.xml/log、dom-red.log、related-green.xml/log、dom-final-utc.log、dom-final-la.log；VPS `/root/plan-time-verify-20261017-kv2ym3qs/` 与 `/root/plan-time-verify-20261017.json`。两份候选目录仅保留日志/JUnit，本轮候选代码、压缩包和三个本机测试临时夹具目录已清理；回退和报告保留。真实浏览器视觉、兼容性、真实到账恢复仍未验。

## 第二组：提交、部署与真实页面

- 业务提交 `8b1750269a4b721967fe69d3aa4713fb54a34ff0` 已推送 main、VPS ff-only 上线。在途为 0 后仅重启网关 **0.372 秒**，PID `1713940` active/running、NRestarts=0；回退 `/root/plan-time-recovery-20261017-yp344yvj`。16 份发布文件 LF 哈希一致，远端工作树干净。
- 真实管理 API 仅刷新指定两号，include_claimable=false，2 成功/0 失败，仍 active 且其他真实余额可见；Global Build 的额度 10-17 23:00 生效、套餐结束 10-18 09:00，按当前数据核验，不沿用旧 proposal 的历史结束日期。
- 上线页面先与源码比对时因服务端 APP_VERSION 替换失败；核对 pages.py 后仅按既有版本模板替换比对，再执行真实 JS load/rowHtml，API 数据的套餐期、生效期、临期、pending 和排序全部通过。洛杉矶及 UTC 运行均通过。此为验收脚本问题，未为此改业务代码；不是浏览器视觉验收。
- 发布后首次保护核对：env、注册表、生命周期、Mihomo 配置哈希及 Mihomo/WARP PID 未变。最终全程哈希断言失败，保留 `final.json`；19:45:32–19:51:31 定时 mihomo-probe-updater 正常执行，三个文件 mtime 为 19:51:21–28，env 唯一变化为 `ZCODE_PROXIES`。当前 chat direct / claim proxy、HK21100/JP21303/TW21202 固定身份、active=saved、pending=false 均未变，Mihomo/WARP PID 和配置未变。记录自动更新差异，不回滚正常节点更新或宣称全程哈希不变。

## 第三组：公网插件

Windows → 公网 VPS plugin `/v1/messages`：Flash/GLM-5.3 各普通/SSE共 **4 项通过**，均 HTTP 200、请求原大小写一致、end_turn、错误 0；3 项逐字 `OK`，GLM-5.3 SSE 为 `OK.`，单独保留，未写成逐字全通过。plugin host_alive=true、stale=false。证据 after.json / after-dom.json / final.json / final-dom.json / public-after.json / deploy.json / updater-attribution.json。

此次只跑受影响回归，未重复上轮 1262 项全量；真实浏览器视觉/兼容性、真实无余额账号结算恢复、HK/JP 对话恢复等独立待办继续开放。不归档。
