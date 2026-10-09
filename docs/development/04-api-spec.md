# 04 — API 规范

状态：与当前实现对齐。对外网关提供 Anthropic Messages（`/v1/messages`）、OpenAI Chat Completions（`/v1/chat/completions`）、OpenAI Responses（`/v1/responses`）与 `/v1/models`。

所有管理端点挂 `/admin/api/*`，需 `Authorization: Bearer <后台密码>`（连续失败达上限后 429 锁 5 分钟）；网关端点按「网关 Key」配置可选鉴权（`Authorization: Bearer` 或 `x-api-key`，未配置即放行——生产必须配置）。

## 1. 网关端点（对外）

### 1.1 `POST /v1/messages`（Anthropic Messages，Phase 1）

- 请求/响应：标准 Anthropic Messages API（含 `stream: true` 的 SSE 透传）。
- 上游转发目标由账号模式决定：JWT → `zcode.z.ai/api/v1/zcode-plan/anthropic/v1/messages`；API Key → `api.z.ai/api/anthropic/v1/messages`。
- 行为：多轮会话亲和选号 + 池内 round-robin 选号（`store.select`）→ 单账号失败按分类换号（≤`MAX_ACCOUNT_ATTEMPTS=5`，满号跳过不计）→ 验证码挑战原账号重试（≤3）。429/5xx/验证码等待期间释放该账号并发槽，醒后重新占槽或换号。
- 会话固定窗口为 600 秒、最多 20 次成功绑定；超过窗口或达到次数后，优先在满足模型优先级的可用账号中换号。没有可服务替代时允许原账号回退，不覆盖健康、额度、并发或失败排除约束。
- 模型名规范化：纯免费赠送池模式下，网关入口将客户端传入的任意模型名统一归一化为 `constants.DEFAULT_MODEL`（`GLM-5.3-Flash`），并按 `GLM-5.3-Flash` 思考契约转换 `thinking` / `output_config.effort`。

### 1.2 `GET /v1/models`

```json
{ "object": "list", "data": [ { "id": "GLM-5.3-Flash", "type": "model", "display_name": "GLM-5.3-Flash", "created_at": "…" } ] }
```

模型清单来自 `constants.AVAILABLE_MODELS`（当前公布唯一免费赠送池模型 `GLM-5.3-Flash`）。

### 1.3 `POST /v1/chat/completions`（OpenAI 兼容）

- 入站 OpenAI Chat 格式 → 翻译为 Anthropic 上游 → 翻译回 OpenAI 响应；`stream:true` 逐块翻译。
- usage/tool_calls 映射规则以移植对照表为准（`openai_compat.py`）。

### 1.4 `POST /v1/responses`（OpenAI Responses / Codex 兼容）

- 入站 OpenAI Responses 格式（`input` 字符串或多态 Item 数组、`instructions`、`tools`、`reasoning.effort`、`prompt_cache_key`）→ 直转为 Anthropic `messages` 上游 → 翻译回 `object: "response"` 或 `event: response.*` SSE 事件流（`responses_compat.py`）。
- 模型别名自动归一化：支持 Codex 客户端后台自检专用模型名 `codex-auto-review` 自动映射至 `GLM-5.3-Flash`，避免上游 3006（model not allowed）拒收。
- 流式首字节立发（TTFT 破除真空）：进入上游循环前立即向客户端 `yield conv.start()` 发送 `response.created` 与 `response.in_progress` 并提交 HTTP 200 Headers，彻底消除长推理或验证码求解期间（5~20s）的零字节静默，避免客户端超时 abort（499 客户端断开与惊群重试雪崩）。
- 网关保持无状态：多轮历史 `reasoning.encrypted_content` 与 Anthropic `thinking.signature` 双向透明回显；流式异常中断补发 `event: response.failed` 终态帧。
- `previous_response_id` 仅作会话亲和线索，无历史 LRU；缺少完整 `input` 时返回参数错误。
- 流生命周期解耦与终态防护（499 误判根除）：上游发送 `message_stop` 并生成 `response.completed` 后，转换器置 `is_finished=True` 并主动 `break` 退出上游读取循环，避免上游 HTTP Keep-Alive 未断连接时下游 Codex 客户端主动断开导致 Uvicorn 注入 `asyncio.CancelledError` 误记录为 499；即使在产出终态后客户端立即掐断连接，网关依据 `conv.is_finished` 仍准确判定为 200 成功。

### 1.5 思考等级

免费模型 `GLM-5.3-Flash` 仅支持 `low` / `high` / `max`，合法档位原值映射到上游 `output_config.effort`，不折叠或降档。

| 接口 | 思考等级字段 |
|------|-------------|
| Messages | `output_config.effort` |
| Chat Completions | `reasoning_effort` |
| Responses | `reasoning.effort` |

省略等级沿用上游默认；`thinking.type=enabled/adaptive` 映射为 `enabled`。显式关闭、非法等级或参数形状、冲突等级、仅设置 `budget_tokens` 均在选号前返回 400 `invalid_request_error`，错误信息列出三档。多处等级须相同；明确等级附带预算时移除预算，不推算等级。

pi 在该模型项设置 `thinkingLevelMap` 仅启用 `low/high/max`（其他档位为 `null`），并设 `compat.forceAdaptiveThinking=true`。这限制可用选项；强制非法 CLI 档位仍可能由 pi 自行钳制，网关校验实际收到的值。

### 1.6 错误格式

```json
{ "error": { "type": "<机器可读类型>", "message": "<人读信息>" } }
```

| HTTP | type | 触发 |
|------|------|------|
| 400 | invalid_request / invalid_request_error | JSON 非法，或请求体不是对象 |
| 401/403 | （FastAPI HTTPException） | 网关 key 缺失/不符 |
| 500 | captcha_error | 验证码求解失败 |
| 500 | internal_error | 调度层未捕获异常（监控条目会收口） |
| 502 | upstream_error | 上游响应无法读取或格式异常 |
| 503 | no_available_account | 池空 / 全部不可用 / 并发已满 |

共享最终503（含三协议 `stream=true/false`）增加 `error.code`、`causes:[{code,origin}]`、`retryable`、`retry_after`、`request_id`；`X-Request-Id` 与监控 `req_id` 一致。`message` 仅供展示，不是分类依据。此契约不改变独立错误与流内错误。

| 原子原因码 | 来源 `origin` | 判定 |
|------------|---------------|------|
| pool_empty | local_scheduler | 当前 provider 无配置账号 |
| account_disabled | account_state | 未启用或禁用状态 |
| credential_invalid | account_state / local_scheduler / upstream | 失效状态、构建凭证失败或鉴权拒绝 |
| quota_exhausted | account_state / upstream | 耗尽状态或已有额度分类命中 |
| model_quota_exhausted | account_state | 同名模型窗口余量≤0；缺失/null不归零 |
| account_cooling | account_state | 有效未来冷却截止；缺失截止保留未知 |
| local_concurrency_limit | local_scheduler | 满槽或等待后重占失败 |
| upstream_concurrency_limit | upstream | 明确3008/3009/3010 |
| upstream_rate_limited | upstream | 明确普通限流码且内部重试耗尽 |
| upstream_429_unknown | upstream | 其它429；含额度字样也不据此踢号 |
| upstream_transport_error | transport | 连接或预读取失败 |
| upstream_server_error | upstream | 服务器错误重试耗尽 |
| captcha_retry_exhausted | upstream | 验证码挑战连续失败；求解异常仍独立500 |
| account_risk_blocked | upstream | 本次明确风控；历史禁用不反推风控 |

`causes` 按 `(code,origin)` 去重排序。覆盖完整时，单个不同原子码直接作为 `code`，多个为 `mixed_unavailability`（同码不同来源仍是单因）；未知或旧阻塞已解除但未重试时为 `unknown_unavailability`。达到尝试上限为 `dispatch_attempt_limit`，不表示全池耗尽。后续换号成功不附这些终态字段。

`retryable=true` 表示至少一条无硬/未知障碍的临时恢复路径，`false` 表示完整覆盖且均需配置、启用、凭证或额度变化，`null` 表示无法判断（尝试上限固定为null）。冷却同时零额度不建议自动重试；未知429仅有可靠等待要求时可为true。此建议不是完整系统健康快照，也不代表客户端会自动消费。

只有true且有可靠时点才给非负整数 `retry_after` 和一致的 `Retry-After` 头。上游秒数/HTTP-date转绝对截止，扣除已等待时间后向上取整，不使用内部等待封顶；同候选取最大截止、候选间取最早截止。存在缺少时点的潜在恢复路径则不编造秒数。本地并发无释放时点；额度 `expires_at` 不是重置时间。

请求监控同源增加 `error_code/error_causes/retryable/retry_after/stop_reason`、`coverage_complete/unresolved_count`，及最多32条匿名 `evidence` 与 `evidence_truncated`。证据只含候选序号、阶段、时间、原因/来源、原始/有效HTTP码和白名单业务码（未取得为null）；不含新增账号身份、额度、凭据或原文。沿用内存500条保留范围，不额外刷新账号或写库。

账号级上游错误在**故障转移耗尽后**回传时：保留上游 status 与 content-type，body 为上游错误原文（转 JSON 失败则 500 字符截断文本）。

HTTP 200 SSE 中的 `type: error` 仍为失败：Messages 透传完整错误帧，Chat 返回错误帧且不补 `[DONE]`，Responses 返回 `response.failed`。请求监控和账号最近结果记为失败；已输出内容不重发，连接与并发槽正常释放。

SSE 在未收到有效 `message_stop` 时结束也记为失败：Messages 保留原字节并追加独立错误帧，Chat / Responses 沿用上述失败收口，不补成功终态。正常终态（含输出上限）、完整 JSON 尾行与非 SSE 响应保持兼容。

## 2. 管理端点（对内，均挂 `/admin/api/*` 且需后台密钥）

### 账号池

| 方法/路径 | 说明 |
|-----------|------|
| `GET /admin/api/accounts` | 全量列表（`public_view`：脱敏凭证 + 状态 + 配额/套餐 + 用量）+ 概览统计 |
| `POST /admin/api/accounts` | `{provider, tokens: [..], name?}` 批量入池（也接受多行字符串）；同凭证幂等；真新增触发安装序 + JWT 自动领取 |
| `PUT /admin/api/accounts/{id}` | 改备注 / 换凭证（按形态自动判 jwt/apiKey） |
| `POST /admin/api/accounts/{id}/enabled` | `{enabled}` 启用/禁用 |
| `DELETE /admin/api/accounts` | body `[ids]` 批量移除 |
| `POST /admin/api/accounts/refresh` | `{all?}` 或 `{ids}` 批量刷新额度（冷却/失效跳过 billing） |
| `POST /admin/api/accounts/{id}/refresh` | 立即刷新该账号额度 |
| `POST /admin/api/accounts/{id}/fingerprint/rotate` | 换发客户端指纹（下一套成套桌面 SKU + 新 device_mid） |
| `GET /admin/api/status` | provider 列表 + 网关 key 是否已配置 + 可选账号计数 |

### 凭证与登录

| 方法/路径 | 说明 |
|-----------|------|
| `POST /admin/api/login/start` | `{label?}` → `{flow_id, authorize_url, expires_in:300}`（zai server-mediated；前端展示链接，`label` 作账号名入池） |
| `GET /admin/api/login/poll/{flow_id}` | 轮询：`{status}` ∈ `pending`/`ready`/`failed`/`expired`；`failed` 附 `message`；`ready` 附 `account`（JWT 已入池；API Key 兑换/额度刷新后台回填）。官方 poll HTTP 4xx → `failed` 并摘会话；5xx/网络抖动 → `pending` 并打日志。ready/失败后或未知/超时 flow_id 一律 `expired`（会话一次性，防重入兑换链） |

### 凭证导入 / 导出

| 方法/路径 | 说明 |
|-----------|------|
| `GET /admin/api/export` | 明文 JSON（name/mode/secret；仅后台密钥保护，安装字段不出网） |
| `POST /admin/api/import` | export 同构 JSON；新账号触发安装序 + JWT 自动领取 |

仅支持明文 JSON，不支持 `.zsb` 封包或桌面凭证解密；存储格式见 `03-data-formats.md`。

### 额度与领取

| 方法/路径 | 说明 |
|-----------|------|
| `GET /admin/api/claim/preview` | 立即拉取当前可领套餐（`?account_id` 单账号；先上报激活事件） |
| `POST /admin/api/claim` | `{account_ids?, plan_id?}` 自动领取（服务端求解验证码；3007 清空预解池后单次止损，回执保留业务码与待领状态） |
| `POST /admin/api/claim/manual` | `{account_id, captcha_verify_param, captcha_region?, plan_id?}` 浏览器滑块人工领取 |
| `GET /admin/api/claim/captcha-config` | 前端滑块 SDK 初始化参数（scene/region/prefix/enabled） |

### 监控与设置

| 方法/路径 | 说明 |
|-----------|------|
| `GET /admin/api/monitoring` | 内存环形请求日志（最新在前，KEEP=500，重启清零） |
| `POST /admin/api/monitoring/clear` | 清空监控 |
| `GET /admin/api/settings` | 不回明文密钥。返回 `admin_key_set` / `admin_key_masked` / `admin_key_is_default`、`gateway_key_set` / `gateway_key_masked`、`quota_refresh_interval`、`account_concurrency`；另含 `bark_server_url`、`bark_device_key_set` / `bark_device_key_masked`、`sentinel_interval`、`sentinel_auto_claim` |
| `PUT /admin/api/settings` | 改密钥、刷新间隔、并发上限（改后即生效，落 meta 表；并发 0 = 不限）；支持 `bark_server_url`、`bark_device_key`（裸 Key 或链接归一化）、`sentinel_interval`（0 停用）、`sentinel_auto_claim`。前端回填的掩码（含 `…` 或 `••••`）忽略不覆盖；网关 key 传空字符串表示关闭校验 |
| `POST /admin/api/settings/bark/test` | 可选 `{device_key, server_url}` 测试推送，不保存配置；空或掩码 Key 沿用已存 Key，未传服务器沿用已存地址或 Key 链接中的地址。成功返回 `{ok: true, message}`；无 Key 返回 400，推送失败返回 502 |

监控保留上游 `input_tokens` / `output_tokens`，另列可空的 `cache_read_input_tokens` / `cache_creation_input_tokens`。缺失或异常类型为 `null`，0 保留；SSE 累计更新不重复求和，不改变三协议客户端 usage 契约。页面显示已知 / 未知样本数；原始输入输出合计不代表完整输入或套餐扣费。

### 探活

| 方法/路径 | 说明 |
|-----------|------|
| `GET /meta` | `{version}`（无鉴权，部署脚本探活用） |

## 3. 幂等与并发约定

- 入池按 `{provider}:{credentialString}` 幂等（重复添加返回既有账号）。
- 领取操作同一账号同时只有一个在途（调度器互斥）。
- 请求调度单账号并发上限默认 2（`account_concurrency` 设置，0 = 不限）；满号不排队，直接跳下一个可选账号，全部满/无号返回 503。
- `POST /admin/api/accounts` 与 OAuth 回调并发入池：以 SQLite 写锁 + upsert 保证一致。
- 网关转发无上游超时（与 ZCode 桌面行为一致）；客户端断开 → 取消上游流（`httpx` stream close）。

## 4. 客户端接入示例

```bash
# Anthropic 兼容（Claude Code 等）
export ANTHROPIC_BASE_URL=http://127.0.0.1:3000
export ANTHROPIC_AUTH_TOKEN=<gateway_key>
npx claude

# OpenAI 兼容
curl http://127.0.0.1:3000/v1/chat/completions \
  -H "Authorization: Bearer <gateway_key>" -H "Content-Type: application/json" \
  -d '{"model":"glm-5.3","messages":[{"role":"user","content":"hi"}],"stream":true}'
```
