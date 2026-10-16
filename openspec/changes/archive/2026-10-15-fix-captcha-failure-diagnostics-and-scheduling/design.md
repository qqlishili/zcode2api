# Design

## Context

动机见 [proposal.md](./proposal.md)。诊断依据为本机 `data/diagnostics/20261011-503/report.md` 与同目录 `evidence.json`，均为 2026-10-11 当次快照，不是持续运行保证。

### 改动前已核验链路与实施范围

| 环节 | 当前行为与证据路径 | 拟改动 |
| --- | --- | --- |
| Node 参数提取 | `captcha_node/solver.js:2098` 严格校验；失败详情通过默认关闭的 DEBUG 输出，最后归为退出码 | 安全的类型化校验错误与单行摘要；保留成功输出与严格校验 |
| Python 求解 | `app/captcha.py:334` 单飞 Future；持一个槽完成整轮最多 4 次重试；全局 `_last_error` | 每次尝试占槽、任务局部诊断、共享任务及期限 |
| 补货 | `_refill_loop`、`_refill_batch`、`_refill_single_proxy` 共用前台入口，消费后另建任务 | 统一空闲容量准入与按代理补货合并 |
| 网关 | `_try_account` 在验证码前释放账号槽，失败走 `captcha_error`，上游连续挑战走 `captcha_retry_exhausted` | 请求内复用同出口失败证据，健康其它出口独立求解，附加管理证据 |
| 出口健康 | `get_verify_param` 把所有返回空结果的求解都记作代理失败 | 删除无依据的代理惩罚，不以本地求解失败推导出口离线 |
| 503 汇总 | `_AccountFailure` → `_unavailability` → `reqlog.finish_error`，现有证据最多 32 条 | 在验证码阶段证据中增加白名单详情 |
| 页面 | `frontend/admin/monitoring.html:173/:229` 仅用 status/error | 可见摘要、可展开诊断、兼容旧记录 |
| 其它消费端 | `app/claim.py` 调用公开获取接口及既有网络客户端 | tuple 与异常类型兼容，核对真实网络尝试的半开资格和结果归属 |
| 关闭 | `CaptchaManager.close` 当前主要关闭周期循环 | 等待清理自己拥有的补货、共享任务与 Node 子进程 |

图谱最新已核对代次 `2026-10-11T13:19:02Z`；相关实现和测试路径为 metadata_changed，已读取当前源码补证。OpenSpec 和 data 目录按设计不在图谱内，直接读取。

### 现有规范关系

- 已发布 capability 只有 `regional-captcha-waterfall`，本变更使用其原路径做增量修改。
- `clarify-gateway-unavailability-reasons` 是已勾选完成但尚未归档同步的历史变更；其中公开错误、匿名候选、32 条证据和重试三态的约束作为兼容基线。
- 当前代码和测试还明确区分本地验证码获取 `captcha_error` 与上游挑战重试 `captcha_retry_exhausted`；不得将本次观察到的“求解失败”统一改写为上游重试耗尽。
- 当前接口文档仍有“求解异常独立500”的历史文字，与当前网关换号后503行为不一致；本次文档同步只纠正这段已核验内容，不据旧文档修改运行行为。
- 较早 `multi-egress-account-isolation` 的无代理直连兜底，与较新的账号大区离线禁止直连存在历史文字冲突。本次遵循当前已实现的出口归一化和按账号亲和规则，不借此变更代理路由政策。
- 现有 Singleflight 规范要求同批前台等待者共享成功结果，本变更保留该行为。此次退出码 6 发生在参数提取阶段，不能把同 token 复用认定为本次根因。

## Goals / Non-Goals

**Goals:**

- 默认运行即可取得不含秘密的参数校验类别，按求解和请求关联，区分排队、执行、预算终止。
- 在原 CPU 上限内减少退避持槽和后台排队放大；同出口依然单飞。
- 取消一个请求不会破坏其它请求；任务关闭能真正回收子进程和槽位。
- 用户从监控页面即可区分验证码、额度、账号并发及未完成尝试，不必解析通用中文文案。
- 以行为证据和实际业务调用作为最终恢复验收。

**Non-Goals:**

- 不削弱参数/securityToken 校验，不增加账号、默认求解并发、重试次数或代理直连降级。
- 复用验证码 SDK、happy-dom、日志平台、数据库和账号状态机；只修正本次有证据的断路器输入归因，不设计新的网络健康服务。若真实参数失败证明 SDK 需要修改，任务 6.4 保持未完成并更新对应修复。
- 不保证外部验证服务永久成功；不把历史未知参数失败编造成某个 SDK 或代理根因。
- 不新增通用调度框架、独立指标服务或账号凭据持久化。

## Decisions

### 1. 默认安全失败协议，严格校验继续生效

增加 `captcha_node/solver_diagnostics.js`，仅导出参数校验与诊断构造的纯函数，无 SDK 初始化、网络或 `main()` 副作用；`solver.js` 调用这些函数。此模块的意义是用真实校验代码做离线行为测试，不是新框架。

失败时 stderr 始终输出一行，长度上限 2 KiB：

```text
SOLVER_DIAG={"v":1,"code":"param_too_short","stage":"extract","exit_code":6,"param_chars":76,"elapsed_ms":1830}
```

字段由程序构造，不能序列化原始错误对象。允许字段仅为 `v/code/stage/exit_code/param_chars/elapsed_ms`；无法取得的数字为 null，数字必须有限且非负，退出码按子进程实际值核对。stage 白名单为 `init/run/extract`；Python 外层阶段另允许 `config/queue/retry/cleanup`，未知阶段不能承载任意文本。

Node 初始化后可控制的失败路径必须输出此协议；语法错误、依赖加载前崩溃或被外部终止无法保证产生摘要，由 Python 的安全未知/进程不可用分类承接，不能将“所有失败有摘要”作为不可能实现的绝对保证。

| 类别 | 取得依据 |
| --- | --- |
| `param_empty` | 校验时未取得参数 |
| `param_too_short` | 参数长度低于现有最低长度 |
| `param_missing_security_token` | JSON 内缺失或过短的 securityToken |
| `param_not_base64_json` | 按现有规则解码/JSON 解析失败 |
| `solver_timeout / solver_stalled` | 程序控制的超时/失速分支 |
| `sdk_init_failed / sdk_failed / sdk_on_error` | 程序控制的 SDK 生命周期分支 |
| `solver_unclassified` | 无可靠结构化分类 |

在参数校验错误产生处附固定 code/stage/长度；仍按当前成功门槛拒绝无效结果。当前退出码映射与成功 stdout 的 `VERIFY_PARAM=` 保持兼容；新诊断 code 比退出码更细，不能仅由 6 猜出缺 token 或短参数。

校验异常中的完整 JSON、参数前 80 字符和 certifyId 必须移除；本次修改的失败摘要在 DEBUG 开关打开或关闭时都不能包含这些值。额外 guest 错误只允许固定类别或计数，不能拼接原始 guest 文本到本次摘要。完整历史 DEBUG 追踪不作为生产启用步骤，本变更不全盘重写与此次失败无关的追踪。

Python 只解析专用前缀、版本和白名单字段。无摘要、非法 JSON、超长、未知版本或 code 时保留退出码与 `param_invalid_unknown`（仅 exit=6）或 `solver_failed_unknown`，不落原始 stderr。没有 Node、进程无法启动、无输出等分别记录 `solver_unavailable / solver_no_output`。本次修复需更新现有“直接保留 ECONNREFUSED 地址字符串”的测试，改为验证安全类别；不能依靠普通字符串截断实现脱敏。

选择此方案而不是全局开启 CAPTCHA_DEBUG，原因是后者会泄露参数片段且无法给调用方稳定字段。

### 2. 诊断归属求解任务，不借用全局最后错误

`CaptchaSolveError` 保持原异常类型和可传入简短字符串的构造兼容，新增可选安全诊断属性。每个 producer 使用进程内 solve_id 及局部结果；`_last_error` 如为兼容保留，仅用于概要状态，不能决定本请求原因。

管理端验证码详情的固定形状：

```json
{
  "solve_id": "solve-17",
  "reason": "solver_queue_timeout",
  "last_solver_code": "param_too_short",
  "stage": "queue",
  "exit_code": 6,
  "param_chars": 76,
  "attempts": 2,
  "queue_wait_ms": 10000,
  "solve_ms": 3500,
  "elapsed_ms": 14800,
  "stop_reason": "queue_timeout"
}
```

这是字段说明用夹具，不是历史四条请求的具体细分类。终止 reason 与最后一次求解器 code 分开，避免把预算到期错误说成新的参数失败。source 仅取 `foreground/refill`，在 journal 中记录；proxy URL、账号标识、scene/prefix、secret、验证参数、请求内容均不进入新增诊断。

### 3. 每次 Node 尝试独立占槽，后台不进入等待队列

保留原 `asyncio.Semaphore(max(1, cpu_count-1))`。把槽占用范围移到单次 Node 启动至退出/回收，重试退避在槽外。同步的配置缓存读取与配置拉取也不占 Node 槽。

前台需求计数覆盖空池等待者的整个生命周期，包括重试退避。删除独立的 10 秒占槽等待切片，前台准入只受本 producer 剩余时间约束；预算为零时不启动新的 acquire 或 Node。后台三个入口采用同一策略：
- 有尚未完成的前台需求或容量满时延后，不进入 semaphore 等待队列、不启动 Node、不记作验证码失败或出口失败；后台拥有的同代理 producer 已有前台加入时按前台模式继续。
- 已运行的后台 Node 不抢占终止；新前台只需等待当前一次尝试，后续后台尝试让位。
- 同代理 producer 被前台加入后，其下一次准入按前台规则执行；取消最后一个前台等待者后，仅剩补货需求则降回后台规则。
- 周期循环不因一个代理补货延后而阻塞其它代理；沿用现有周期再次评估，禁止忙轮询或新增无限补货队列。

后台用 `not semaphore.locked()` 与前台需求计数判断空闲，随即直接 `await semaphore.acquire()`，两者间没有其它 await。本机项目环境 Python 3.12.10 标准库实测：acquire 在未 locked 时同步减计数并返回，不挂起，locked 还考虑已排队等待者；因此可以复用现有信号量，不需要第二套 `_active_solves` 容量计数。禁止用 `wait_for(acquire(), 0)` 模拟非阻塞，也不读写 `_value/_waiters`。部署运行时另行核对，并以双核/多核竞争行为测试验证；前后台不得各建独立容量。

单次在途求解 11 秒时，拥有足够剩余时间的前台不得因原 10 秒切片失败。总预算仍限制过载等待；预算耗尽必须准确显示资源/预算停止，不能推导全部账号额度耗尽或出口失效。

### 4. 共享 producer 与等待者分别管理期限和取消

`_in_flight_solves` 从裸 Future 改为按代理持有受管 producer task 及少量状态：deadline、前台等待者数、补货需求。配置拉取移入 producer，使总期限覆盖配置获取、排队、执行与退避。

每个 producer 自创建起的绝对期限为 `loop.time() + CAPTCHA_SOLVE_TIMEOUT`，默认 40 秒。最多尝试次数保持 4；每次 Node timeout 及前台准入都受 producer 剩余时间约束，不再叠加 10 秒切片。该期限只属于本次求解，不能移作整个客户端请求或后备出口的期限。预算耗尽后不再联网或启动尝试，只做 kill/wait/release；清理耗时单独记录，不宣称截止瞬间已回收。与原单次执行设置相比，总预算可能提前结束第 2~4 次尝试，必须按实际 VPS 验收决定是否满足负载，不能靠这一预算宣称恢复。

每个等待者以 `shield(task)` 等待，并只取消自己的等待。加入同 producer 不重置其期限；不同出口的新 producer 有完整独立预算。前台取消后减少自己引用，无其他前台且无补货需求时取消 producer；还存在需求则继续。清理回调必须取回异常并删除映射，不能留下 Future 未完成或“exception was never retrieved”。保留公开 tuple 与参数签名，不新增跨账号 deadline 参数。

同代理补货任务有一份强引用，按代理合并；生产结果由一个归属点处理：
- 有前台消费者的结果按已有 Singleflight 广播契约返回，不再将同一枚结果同时插入水库。
- 仅补货消费者的成功结果最多入池一次。
- producer 结束与等待者退出、补货重试之间存在竞态时，也不得重复插入或重复释放。
- `close()` 取消并 await 周期循环、补货任务及 producer，最终 Node 全退出、槽恢复、映射为空。

### 5. 故障范围随出口和本次请求隔离

删除原方案的请求级 40 秒 deadline 及其跨账号传递。失败出口 A 用尽自己预算后，健康出口 B 的空池现解仍有完整 producer 预算；A 之前的模型连接等待、429 退避不消耗 B 的求解预算。后续上游挑战在现有 `MAX_CAPTCHA_RETRIES=3` 内取得新的求解机会。`MAX_ACCOUNT_ATTEMPTS=5` 和已配置上游重试仍为有限边界；本变更不新增整个 HTTP/SSE 总超时，也不承诺满载时请求无限等待。

在 `_dispatch` 持有请求内 `captcha_failures` 字典，以实际求解的代理键关联已经终止的 `CaptchaSolveError` 安全证据，传给 `_try_account`。命中同出口已失败结果的候选复用该证据，不再次启动同一失败求解；不同出口、无需验证码的通道和现有 API Key 回退仍有自己的服务机会。只有共享 producer 的真实终态才写入；取消、前台离开、后台延后不得污染字典。字典随客户端请求结束丢弃，不写账号/数据库，不把一个请求的失败缓存到其它请求。

获取顺序必须保持“有效亲和缓存优先”。同出口已失败但后来有有效补货 token 时仍允许立即消费，避免请求内避让误挡真实恢复。给 manager 的现有获取方法增加一个可选 keyword-only `failures` 参数接收请求内字典；原 `port/account/proxy` 调用与 tuple/异常契约不变。入口先命中有效池，再检查请求内失败，最后加入/启动 producer；只有失败 producer 的终态可写入字典。不新增全局 3~5 秒失败缓存或配置旋钮，claim 不传该参数。

删除 `get_verify_param` 中“返回空求解结果就 record_proxy_result(False)”的惩罚调用。本地参数无效、SDK/Node 故障、排队/总预算停止与未知错误都不足以证明代理故障，不能推进 CLOSED/OPEN/HALF-OPEN；求解成功也不能凭空证明模型通道健康。只有经明确分类的实际网络失败或上游 WAF 证据才能进入既有断路器；不得根据原始 stderr 文本猜测。若新方案不取得网络层结构化证据，就让这些求解失败保持中性，不新增惩罚分支。

当前 `is_proxy_quarantined` 查询本身会消费 HALF-OPEN 探针资格，因此移除惩罚前必须修正查询、实际准入与结果归属。在既有 client pool 中将健康检查和客户端缓存工厂保持纯读取/复用，在实际网络请求边界持有唯一半开资格；正常/CLOSED 通道不增加等待队列。复用已有状态字典和 `half_open_active`，仅增加配对的准入/结束操作，由实际请求的 try/finally 调用；不得在只获取 client 时就永久占资格。客户端记录实际绑定的出口，成功/网络或WAF失败/取消均据此结算一次，非网络错误只释放资格；缓存客户端的实际请求仍须经过同一准入。验证码、上游请求与精准失效保持同一实际出口，禁止健康查询中重新解析并消费资格。

当前源码及图谱确认这项共享入口涉及五个调用点：gateway `_try_account`、claim `_billing_request`/`report_activation_events`、quota `fetch_quota`、install `run_install_sequence_for_account`。全部同步资格结算，不能只改 gateway 而由后台额度刷新留下半开占用。quota 的三个并发读取属于一个持有者、聚合一次结算，不释放三次；stream 的资格使用已有 `_Upstream` 清理/结果归属完成，不提前关闭仍被消费的连接。改动只涉及这五个现有调用点的网络生命周期，不改领取、额度、激活业务语义，不另造断路器框架。覆盖纯查询不占资格、缓存客户端复用、半开本地失败后可重入、两请求仅一个探针、成功关闭/明确网络失败重开及并发quota取消回收，避免删一行后永久 HALF-OPEN。

账户额度、认证、transport、region、已有风控状态保持各自分类，不因参数失败标记账号 exhausted/invalid，也不绕过既有出口亲和。诊断中的安全代理关联键仅用于进程内避让，不暴露 URL。
公开获取失败仍为 `captcha_error`，明确上游连续挑战仍为 `captcha_retry_exhausted`；汇总 code、coverage、retryable 和 Retry-After 的原有判定不在本次重写。可重试建议不意味着验证码一定恢复，页面不能改写成保证。

### 6. 复用现有请求证据与页面组件

`_AccountFailure` 新增可选安全 captcha 字段，`_unavailability.observe` 仅在验证码阶段拷贝白名单；总证据仍最多 32 条，existing truncation 计数继续正确。旧 `error` 与公开 causes 保留；公开响应不输出验证码参数长度、solve_id 或内部耗时。

最终 503 日志在诊断计算后使用 `logs.req_err` 输出 req_id、error_code、stop_reason、去重原子码；验证码失败处用 req_id + solve_id + 安全 reason 关联 producer。未知异常只输出固定未知类，不串其它请求的 `_last_error`。不新增上游请求或数据库诊断写入。

页面在原错误列首先显示短原因，如“验证码获取失败；部分账号额度耗尽”，随后以原生 `details/summary` 展开：request_id、error_code、原因、重试建议、网关停止原因、覆盖缺口与验证码阶段摘要。所有外来字符串经现有 `esc`；未知字段仍安全展示，缺少新增字段的旧记录回退现有 error。标题中的原始通用文字不能作为唯一诊断入口；键盘可展开，窄屏不覆盖相邻列。

保持监控 500 条内存态及重启清零。查看诊断入口不清空记录、不调用验证码和上游；刷新继续现有 `GET /admin/api/monitoring`。

## Verification

### 最小充分的离线与本地验证

| 行为 | 证据与现有测试落点 |
| --- | --- |
| 真实参数校验与摘要安全 | 新 `tests/unit/test_solver_diagnostics.py` 调用 Node 纯模块；空/短/无token/非法JSON/有效参数，DEBUG 两态，无真实 SDK 网络 |
| 跨语言错误协议 | 扩展 `test_captcha_proxy_affinity.py` 或新 `test_captcha_solver_diagnostics.py`；前缀版本、非法字段、退出码6未知、无Node/无stdout、恶意原始stderr |
| 单槽争用与截止 | 新 `test_captcha_scheduling.py` 用 asyncio Events 和可控时间；A 退避释放、B 先取得槽、最大活跃1；合法 11 秒占槽不因10秒失败；同 producer 有界、其它出口预算独立 |
| Singleflight 与取消 | 扩展 `test_proxy_affinity_pool.py`；三个请求一份 producer，取消一者其余成功、全部无人取消清理、共享失败不串代理、只补货入池一次 |
| shutdown 与 subprocess | 使用不联网的本地临时 Node 子进程夹具验证 kill/wait，执行/排队取消、shutdown 后新获取不受漏槽影响 |
| 网关与监控 | 扩展 `test_captcha_failover.py`、`test_gateway_unavailability.py`、`test_gateway_monitoring.py`、`test_reqlog.py`；三协议×stream真假、换号成功无最终错误污染、混合故障正确、无本地并发臆断、证据上限与深拷贝 |
| 故障栅栏与重复避让 | 真实 manager/client pool；本地失败不惩罚 CLOSED/HALF-OPEN、资格释放、相同请求同出口不重解、不同请求不残留、有效缓存仍可成功 |
| 页面 | 本地隔离 app + 注入脱敏夹具，通过浏览器实际打开监控；校验新旧记录、超时与参数失败、混合原因、恶意HTML、键盘操作与窄屏 |

测试环境复用 `tests/conftest.py` 的 tmp_path 数据库与真实 TCP Mock upstream；新调度测试必须使用真实 CaptchaManager，不能把待测方法直接 mock 为“已经成功”。配置读取、Node进程和上游边界可隔离，事件顺序和资源回收必须可观察。

实施先使关键退避持槽、取消传播、默认退出码6无摘要场景呈现红，再完成最小修复，更新既有原始 stderr 字符串断言，并运行上述受影响测试。只在暴露具体疑点时扩大检查。建议命令：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_solver_diagnostics.py tests/unit/test_captcha_solver_diagnostics.py tests/unit/test_captcha_scheduling.py tests/unit/test_captcha_proxy_affinity.py tests/unit/test_proxy_affinity_pool.py tests/unit/test_captcha_failover.py tests/unit/test_unavailability_result.py tests/unit/test_reqlog.py tests/integration/test_gateway_unavailability.py tests/integration/test_gateway_monitoring.py
```

命令中的新文件由任务创建，使用隔离数据和本地Mock；实施结果和实际VPS请求分别记录在 `execution.md`。

### 107 VPS 实际业务恢复验收

执行清单明确版本、重启窗口和请求数，并核对已有授权覆盖后：
1. 通过 Netcatty 核对进程配置、Python/Node/依赖、当前有额度账号及实际出口。目标为私有运行配置中的实际网关入口 `/v1/responses` 与实际客户端路径，不用 VPS-local curl 代替客户端到网关的验证；真实地址和端口不写入规划或提交。
2. 设 E 为当前需要验证的独立出口数。每个出口一次最小验证码求解、一次 `GLM-5.3-Flash` 模型请求；需要临时、仅进程内的确定性选号，禁止通过修改账号持久状态来凑覆盖。使用最小提示词、限制输出；每条都必须有非空真实文本和协议正常完成终态，缺一个出口则覆盖未完成。
3. 后台补货保持启用，再跑总计 10 条 Responses 请求、并发严格 2 的业务批次。正常有额度/出口条件下要求 10/10 正常完成，503、429、`solver_queue_timeout` 和验证码预算失败均为 0；按 req_id 核对实际输出、监控、journal、等待/执行耗时和最大活跃 Node=1。任何失败均保留证据并继续根因修复，不能只换文案或删除记录。
4. 离线真实 TCP 场景须证明：A 求解耗尽后 B 仍可成功；同出口失败换号只执行一轮 producer；本地参数失败不触发代理隔离；合法 11 秒持槽后 B 可继续。无需在生产注入破坏性故障。
5. 记录这一批的实际延迟，样本不足不宣称稳定 P95<9 秒。关闭/取消资源回收由真实本地子进程验证；VPS 正常负载后前台任务归零、后台补货能恢复，持续满载不能以提高并发绕过 CPU 上限。
6. 真实请求数为 E 次验证码、E+10 次模型请求，另加一次实际客户端最小请求（可计入逐出口成功但不能虚报路径）。执行前填入 E、具体账号/出口控制方法与总数；上游额度不足或触发真实限流时保持失败而不作伪成功。
7. 只有指定路径业务成功、资源回收、脱敏和回退方案都验收后才能写“已恢复”。历史退出码6细分类仍未知须照实记录；本批成功不能证明未来永不受外部限流。

## Risks / Trade-offs

- [单 producer 总预算缩短长重试] → 明确其只限制一次共享求解，健康后备出口不会继承；记录 VPS 实际负载耗时，不用固定预算代替根因修复。
- [故障转移总耗时增加] → 每个 producer、账号数、挑战数和上游重试均有界；同请求同出口失败不重复。完整 HTTP 总超时不是本次新增目标，实际客户端等待能力在部署清单与验收记录中明确。
- [本地失败误伤出口/半开资格泄漏] → 网络证据才惩罚；健康查询纯读取，实际准入拥有资格，取消/本地失败释放，覆盖 HALF-OPEN 恢复测试。
- [后台饥饿与空池增加] → 缓存命中不占槽；无前台等待时恢复补货；用“持续负载结束后水库恢复”测试验证，不承诺持续满载下预热。
- [取消/入池竞态] → producer 单归属、计数成对、shield 与 shutdown await；覆盖任务刚结束、最后等待者退出、同代理补货/前台交接。
- [安全摘要丢失旧调试细节] → 已知类显式构造，未知保留类别和退出码，不承诺猜出旧日志中丢失的异常。
- [外部 SDK/验证服务仍拒绝] → 实际失败必须继续对应根因修复；观察改进与业务恢复分开勾选。
- [历史退出码 6 缺少细分类] → 此证据缺口不影响已确定的诊断与调度实现；若新证据显示 SDK、配置或代理需要额外改动，先明确影响并更新方案，实际业务验收保持未完成，不能臆造修复分支。
- [历史 Singleflight token 共享风险] → 保留现契约；未取得独立证据前不变更为单次消费队列，禁止借本次参数提取失败推导该根因。

### 规则折旧

删除 10 秒槽等待切片、跨账号/挑战共享 40 秒请求期限、无网络证据的求解失败代理惩罚及通用文案作为唯一展示。纠正文档“求解异常独立500”。复用 semaphore、账号重试上限、日志/监控接口和当前亲和栅栏；不因允许破坏性更新而删除数据库、额度数据或实际协议消费者，也不借历史文字扩大到所有直连行为重构。

## Migration Plan

1. 本地实现、受影响测试后，人工核对四条历史证据与新输出契约；此时仅可声明本地修复通过。页面交互实测单独记录，按用户要求不阻塞VPS实际业务验证，未验证的页面行为不得据服务成功勾选。
2. 用户明确批准后，采用项目既有发布方式同步同一代码版本与新 Node 本地模块，记录之前版本与当前配置；不重新生成账号库、出口注册表或凭据。
3. 在批准的维护窗口滚动到修复版。单实例 service 重启会丢失内存请求监控与预解 token，需事先导出所需脱敏证据并允许短时中断；无数据库迁移。
4. 用上述实际请求验收；失败保存安全证据后回退。部署前保留完整旧代码、Node 模块及 service 启动参数的可恢复副本和哈希，以文件/版本切换回退，禁止假设 `git revert` 已获授权。回退后核对旧版本运行和一条真实客户端请求；数据库和期间新增的真实额度变化保留，不恢复旧账号快照。实际恢复耗时记录，不无依据承诺30秒。
5. 只清理本次明确归属的夹具/子进程，保留诊断报告。实施与实际验收结论记录在本变更任务文件及执行记录，未取得授权的部署/真实验证保持未完成。
