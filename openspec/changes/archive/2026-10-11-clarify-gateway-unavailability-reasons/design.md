# Design

## Context

动机见 proposal.md。源码证据：三个入口在 gateway.py:363/409/530 共用 `_dispatch`，失败 JSON 直接返回；Responses 此时尚未创建 SSE 响应，所以 stream=true 也能保持最终503。gateway.py:790–841 区分实际尝试、满槽跳过及无候选，却只输出合并文案。store.py:314–345 的筛选与 models.py:125–157 的健康/模型谓词是权威；`_NEXT_ACCOUNT` 目前没有原因载荷，reqlog.py:87–99 只保存文案与状态。

相邻三个变更已标完成，分别覆盖 SSE 显式错误、静默EOF、思考参数；不修改它们。主规范库存为空，新增平铺 capability `gateway-unavailability`。既有六项工作区修改涉及发布脚本及发布说明，与规划无重叠。项目无 AGENTS.md 或 current_task_handoff.md，按用户全局规则与 OpenSpec 配置执行，不为此次规划另建交接体系。

## Goals / Non-Goals

**Goals:** 用实际分支结果解释共享最终503，覆盖有证据的原子原因、停止覆盖、重试三态及监控一致性。

**Non-Goals:** 不重写轮询/粘性、重试次数、账号状态机、并发槽生命周期；不转换独立400/401/403/500或流内错误；不修改客户端、前端或自动恢复逻辑。

## Decisions

### 1. 与现有选号共用排除判定

将健康与模型的决定性排除结果放在 Account 现有谓词旁，共用一次权威判定；原 bool 方法保持签名与行为，Store.select 增加可选请求内观测容器（默认不采集），在锁内沿现有顺序报告排除/选择。skip_ids 仅表示本请求已访问，不能直接命名为病因；粘性、explicit preference、avoid_id 只是偏好，不是不可用原因。

终止时可用同一无副作用判定及 Store 的锁内快照核对未尝试范围，不再调用会推进 cursor 的 select。模型检查遵守 enabled→状态→skip→模型的原顺序。为重试分析额外核对临时阻塞候选的硬障碍，例如 COOLING+模型余量0，不改变真实选择次序。不建立另一套诊断调度器，不解析 last_error。

对于当前已跳过/已尝试账号，保留最后一次决定性观测；若终止快照显示旧障碍已解除而本轮未重试，公开移除旧障碍并标记覆盖不足，不能假称全池不可用。请求期间状态可变，观测带时间，不承诺事务级官方一致性。

### 2. 换号携带请求内证据

在 gateway.py 现有轻量类型旁定义失败结果，替换 `_NEXT_ACCOUNT` 无载荷返回与唯一消费点，不改成功 `_Upstream` 或直接 JSONResponse 的协议。结果包含原子码、origin、匿名请求内候选序号、阶段、观测时间、raw_http_status、effective_status、business_code、可选等待截止；裸 sentinel 或不支持的状态组合不得默认为成功分类。

复用 `_classify_business_error` 的 effective_status/business_code/is_concurrency_limit、`_is_exhausted`、`_is_risk_control` 和现有判定顺序。先排除挑战/风控，后处理额度/鉴权；并发业务码优先于 RATE_LIMIT_BUSINESS_CODES（该集合同时含3008/3009/3010）。普通限流须明确码，未分类429为 upstream_429_unknown。连接与预读取失败同为 transport，阶段仅后台区分。等待后重占槽失败只新增 local_concurrency_limit，已过去的等待失败保留为后台观测。

验证码获取异常直接500（gateway.py:1096–1106）不纳入；仅挑战次数耗尽的换号纳入 captcha_retry_exhausted。HTTP200业务错误可转换为402/429/500，因此必须分别保留原始与有效HTTP码，不能把 effective_status 当原始上游状态。

### 3. 原子码及证据映射

| 码 | 分支证据 | origin | 重试基础 |
| --- | --- | --- | --- |
| pool_empty | provider 配置池为空 | local_scheduler | false |
| account_disabled | enabled=false / DISABLED 实际排除 | account_state | false |
| credential_invalid | INVALID排除、凭证构建失败、明确鉴权换号 | account_state / local_scheduler / upstream | false |
| quota_exhausted | EXHAUSTED排除 / 本次已有额度分类命中 | account_state / upstream | false |
| model_quota_exhausted | 同名模型remaining<=0实际排除 | account_state | false |
| account_cooling | 有效未来截止实际排除 | account_state | true，核硬障碍 |
| local_concurrency_limit | 满槽或等待后重占失败 | local_scheduler | true，无释放时点 |
| upstream_concurrency_limit | 明确并发业务码换号 | upstream | true |
| upstream_rate_limited | 明确普通限流码且内部重试耗尽 | upstream | true |
| upstream_429_unknown | 未知429换号 | upstream | null；合法等待指示可支持true |
| upstream_transport_error | 连接/预读取失败换号 | transport | true，尊重随后冷却 |
| upstream_server_error | 5xx/服务器业务错误重试耗尽换号 | upstream | true，尊重随后冷却 |
| captcha_retry_exhausted | 验证码挑战连续失败换号 | upstream | null |
| account_risk_blocked | 本次明确风控换号 | upstream | false |

false 指本次不建议自动重复，不承诺永远无法恢复。缺失冷却截止为未知，不用默认值补齐。缺失/null模型窗口放行，不能命名为模型不支持。状态排除只记录决定性条件；同一失败的状态更新可作为后台佐证，不强制重复增加公开 cause。

### 4. 汇总与重试是两个维度

公开字段为 error.type/message/code/causes/retryable/retry_after/request_id；HTTP503/type不变。causes 去重及按(code,origin)排序。code 优先级仅用于停止覆盖：attempt_limit→dispatch_attempt_limit；否则覆盖不足→unknown_unavailability；否则按不同原子码个数取单码或mixed_unavailability。同一个码不同origin仍为单因。零原因且无法证明pool_empty为unknown。

每候选维护硬障碍、临时障碍、未知障碍和等待截止。attempt_limit一律retryable=null；no_candidate时，有一条已证实临时恢复路径且同候选无硬/未知障碍为true；完整覆盖且全候选均需外部变化为false；否则null。混合硬失败与另一条已证实临时路径可以为true，不把全池的原因集合误当单账号障碍。

只有true才计算retry_after。上游等待必须在收到响应时转换为绝对截止，支持有限非负秒数与合法HTTP-date，剩余秒数向上取整；不得使用现有 `_parse_retry_after` 的内部等待封顶/截断结果（gateway.py:287–298）缩短对外要求，不修改原内部等待策略。HTTP-date使用已取得的服务端Date作为时钟参照，缺失时本机时间；不可靠/不可解析时不生成数值。其它默认内部退避不当作上游重试期限。

同一候选取仍适用等待截止最大值，已证实临时候选间取最早值。任一潜在更早临时路径缺必要时点（例如本地并发）则返回null，无Retry-After；不以其它账号的冷却替它编造期限。有效到期时间允许0秒。额度 expires_at表示到期，不是恢复/重置时间，不用作retry_after。

### 5. 同一结果构造响应与监控

共享最终出口构造一次不可用结果，派生公开error与reqlog扩展字段，复用req_id设置X-Request-Id；有效retry_after才设置一致的Retry-After。reqlog.finish_error 增加可选结构化参数，旧调用无变化；finish_ok不附不可用终态。snapshot对新增嵌套字段复制，防止返回对象修改内存记录。

后台新增 evidence 仅含匿名候选序号、阶段/时间、原始与有效HTTP码、已取得的业务码、coverage_complete、未决范围计数、stop_reason。业务码仅保留已有分类常量中的枚举值或1–6位数字码，提取器可能返回的任意error.type字符串不原样记录，未取得或不安全值保持null。候选序号不来自账号ID/名称；轨迹最多32条，另给截断计数，原因聚合在截断前完成，轨迹截断本身不抹掉已完成覆盖结论。固定原因集合限制公开集合大小。旧account/preview等监控字段不扩散到公开错误，本轮不重构旧监控隐私协议。

## Risks / Trade-offs

- 观测与后续状态变化可能冲突 → 标记覆盖不足，保留时间，不额外刷新账号求一致。
- HTTP503可能仍被客户端自动重试 → retryable只表达服务端建议，不声称Pi/Codex已消费字段；不改客户端。
- 谓词提取可能改变选号 → 对比原select返回顺序、cursor、粘性和缺失窗口反例，保持现有bool入口。
- 换号结果改变可能影响槽释放 → 原 finally/close/park/reacquire保持，关联生命周期测试必须通过。
- 诊断正文可能泄露上游内容 → 只白名单枚举码，不记录任意原始message或异常字符串到新增结构。

## Migration Plan

本轮仅规划。实施先取得隔离红例，再按tasks执行；现有 fresh_app/tmp_path 与 Mock 上游夹具覆盖HTTP/TCP，不请求生产。通过后按既有API文档风格补最终503字段说明。发布、提交和部署另需授权；回退时移除新增诊断字段及请求内收集，不触及账号数据库或状态。OpenSpec目录当前被.gitignore忽略，保留既有约定，不强制入库。
