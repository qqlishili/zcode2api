# 分流实现与验收记录

## 第 1 组：配置契约与失败复现（2026-10-17）

- 在本机独立数据目录执行，未调用生产上游、修改 VPS 配置或重启服务。原 15 份文档改动继续保留。
- `tests/unit/test_purpose_egress.py` 三项回归均先失败：传入代理被全局 direct 改成 None、operation 不支持 purpose、插件未确认仍执行 host.fetch。失败证据为 `data/diagnostics/split-egress-implementation-20261017/red.xml`；分别由第 2/3 组转绿。
- 首次 pytest 在默认临时目录遇 WinError 5，未进入断言，不算复现。按 mattpocock diagnosing-bugs 定位后改用本项目独立 basetemp；标准 venv SSL 故障沿用基础 Python 先加载 SSL 的已知验证入口，没有修复无关环境。
- 新增不可变用途策略及四项环境默认、完整对象校验；Store 在加载数据库之后冻结策略，CLI/网关共用。保存/恢复只改目标，原子提交成功才更新缓存；运行值不变，来源也参与 pending_restart 比较。
- 配置定向验证 **17 项通过**，覆盖默认/部分绑定、非法枚举/URL/身份/端口/未知字段、数据库优先、重启加载、恢复默认、写失败无缓存/数据库半保存、损坏对象拒绝启动。证据 `group1.xml`；差分空白检查通过。未运行全量测试，不把此结果当作路由或公网验收。
- 配置及生效说明见 `docs/egress_configuration.md`、`.env.example`。下一组接入连接池、固定绑定和退役引用。

## 第 2 组：连接池、固定绑定与退役

- 客户端缓存包含账号/用途/实际出口/代次；有效 None 不再表示未解析。同账号异步跨用途取得独立操作，producer 仅派生父操作的路由，账号关闭覆盖所有用途。
- 固定领取绑定校验同区、身份、来源和物理退役；chat draining 不改变，业务断路器按用途/物理身份隔离。生效绑定参与 fixed_ports 和 fixed ack，保存移除不会提前释放；退役文件变化即关闭新准入，不依赖注册表内容变化。
- 回归曾发现提前重新解析路由破坏已有缓存粘性，已修复：继承策略下仍有效的客户端不会因同区增加其它节点而重新哈希。断路器夹具同步新键结构，保持原行为断言。
- VPS 独立目录 `/root/split-egress-verify-20261017-a_kbzjat/repo` 使用隔离 data 与真实 Linux /proc；配置、连接池、操作/旧流/producer、断路器与消费者集成 **70 项通过，1 项插件负向用例留第 3 组**。证据为同目录上层 `group2.xml` / `group2.log`。
- Linux 多进程验证新增 claim-only draining 绑定：操作排空后固定引用仍阻止回收，重启移除绑定才允许回收。单进程另覆盖待重启删除保存的 fixed/operations/producers/caches 计数和新准入关闭。
- 本组未部署，生产 HEAD/三个服务 PID 与实施前一致；没有改生产账号、节点、配置或监听。

## 第 3 组：业务入口、验证码、配置 API / 页面

- 网关/三协议/native/plugin/调度/探针显式 chat；领取、JWT 额度、账号安装/登录、哨兵显式 claim。自动领取整次尝试持有同一操作，异步额度刷新不能串用 chat。管理删除账号已接入所有用途关闭并保留在途引用。
- 插件删除静默直连；继承代理未确认时明确不可用，显式本地代理按实际客户端透传，宿主 vendor 未改。验证码 producer 继承父用途，claim 不触发消费回补；有效 direct 不重新解析账号代理，定向清池不株连其它出口。
- 配置管理扩展原 settings 页及导航、独立表单和保存/恢复；服务端鉴权、整对象校验、过期选择 409、原子保存和 active/saved 展示已接通。显式 claim 的人工选套餐改服务端求解，旧浏览器 token 返回 409 稳定标识，inherit 保留旧入口。
- VPS 独立夹具覆盖相关单元及真实 TCP Mock 集成，**520 项通过**（与第 2 组有重叠，不相加）；证据 `group3.xml` / `group3.log`。三项原始分流失败已转绿。完整受影响及 Linux 回归留第 4 组集中验收。
- 首轮 14 失败中，6 项在未修改 HEAD 的独立 baseline 同样失败（baseline.xml / baseline.log）：旧激活数量/鉴权断言、重复 JWT 身份被误当新增、已移除的跨账号资格共享。仅修正测试夹具/断言到已有契约，未恢复共享或改业务门禁。其它失败为旧接口夹具及候选包遗漏 solver.js，已修复后复验。
- 配置页真实 JS 的 happy-dom 行为验证通过：加载、独立保存、当前/目标、待重启、重载、保存失败保留、旧节点不自动改绑、恢复默认、旧表单保留。运行 `node tests/frontend/egress_settings.test.mjs`；复用现有依赖，无新增包。HTML 内嵌脚本语法及 OpenSpec strict 通过。
- **真实浏览器尚未验证**：当前 CUA 返回 apps/browsers 空，创建 iab 返回 Browser is not available。已向用户说明 API/DOM 与真实插件验收的覆盖边界；未将 DOM 冒充真实浏览器，不核销 4.6。代码和接口验证继续推进。
- 原 15 份文档 SHA256 全部保持；本组尚未发布。

## 第 4 组：集中检查与发布验收（进行中）

- 扩展至 52 份相关测试文件（全部 integration 与相关用途/设备/安装/额度单元），初轮 799 通过、10 失败；其中 9 项在未修改 HEAD 的 baseline4 也复现，剩余一项是本次 purpose / 未解析代理参数的旧 Mock 断言。只修正夹具/断言：真实不同 JWT 身份、显式对应额度模型、给探针替身补凭证、声明用途；没有改生产业务来迎合测试。
- 修复后定向复验 85 项通过，剩余一个假 JWT 的 Mock 前缀碰撞经真实失败输出定位，修正测试 token 前缀后相关 15 项通过。没有每个小修改都重跑整批。`group4-latest.xml` 合并每个 testcase 的最新结果，**809 个用例无失败**；不是各批次数量相加。
- 发布前自审增加嵌套 billing 超时回归：在外层 claim 操作内实际 request 的 read timeout 变成 None，先复现失败（本机 nested-timeout-red.xml）。修正为逐请求默认 25 秒，避免长流客户端吞掉 preview 限时；相关用途/节流/领取 **59 项通过**，Ruff app / tests 全部通过。
- 纳入新增超时场景后，**810 个 testcase 的最新结果均通过**，合并证据为 `data/diagnostics/split-egress-implementation-20261017/group4-latest.xml`；各原始 JUnit/日志保留在 VPS 独立验证目录。DOM 行为、两页面 JS 语法及 change strict 已通过；真实浏览器仍未覆盖。
- 发布前从 Windows 显式无代理访问公网网关：普通对话 HTTP 200 / OK / stop；SSE HTTP 200 / OK / stop / [DONE]，流内错误 0。证据 public-before-deploy.json。临时认证文件已两端删除，不打印凭证。
- 回退材料已保全于 `/root/split-egress-recovery-20261017-wsos0gff`（目录 0700）：代码归档/HEAD、原出口 meta 是否存在及值、.env、注册表/生命周期和 Mihomo 配置哈希。原数据库无出口覆盖；回退只恢复该 meta，不回滚整个账号库。
- 04:08 左右生产只读核对仍为 HEAD 027aafe、网关 PID 1132673、plugin + direct。enabled JWT 大区 HK 6 / JP 5 / TW 3；来源有效且未 retiring/reclaimed 的候选分别 5 / 24 / 7，尚未作为领取成功证明。未部署、未保存生产分流配置。
