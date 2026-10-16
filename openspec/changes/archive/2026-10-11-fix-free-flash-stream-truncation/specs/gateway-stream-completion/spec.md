# Spec Delta

## Purpose

定义免费赠送 GLM-5.3-Flash 请求的上游 SSE 完成条件，使静默 EOF 不再被请求监控、账号最近结果或客户端成功终态掩盖。保留已输出内容，避免重新消费额度，并维持正常完成、输出上限和取消行为。

## ADDED Requirements

### Requirement: SSE 成功必须确认正常终态

三种网关接口 MUST 仅在识别有效 JSON `type: message_stop` 后判定上游 SSE 成功。HTTP 200、文本 / 工具参数、usage 或 `message_delta.stop_reason` 单独出现均 MUST NOT 代替该终态。沿用现有单行 JSON 和完整 EOF 尾行兼容，不新增严格 SSE 帧边界或事件顺序校验。

#### Scenario: 空流或部分输出后静默结束

- **WHEN** HTTP 200 SSE 为空，或输出文本 / 工具参数后 EOF 且没有有效 `message_stop`
- **THEN** 请求和账号结果为失败，Chat 不补 `[DONE]`，Responses 不补 `response.completed` / `response.incomplete`

#### Scenario: 终态 JSON 被截断

- **WHEN** 尾部含未能解析成 `type: message_stop` 的残缺 JSON 后 EOF
- **THEN** 仍判失败，不因含有终态文字或已有停止原因而记成功

#### Scenario: 正常终态及完整尾行

- **WHEN** 收到有效 `message_stop`，包括 CRLF、跨分块和既有完整 JSON 尾行没有换行的形态
- **THEN** 保留正常完成；`max_tokens` 仍沿用 Responses 的 `response.incomplete`，不误记传输失败

### Requirement: 静默 EOF 必须按协议收口且不重新请求

静默 EOF 时网关 MUST 保留已输出内容，Messages 追加标准 `type: error` 帧，Chat 输出错误 JSON 帧，Responses 输出一次 `response.failed`。Messages 的追加错误 MUST 与残余上游字节有独立帧分隔。固定失败说明不得包含原始事件或凭据。网关 MUST NOT 重新请求或换号重发已输出内容。

#### Scenario: 已输出内容与失败终态

- **WHEN** 部分输出后静默 EOF
- **THEN** 已输出内容仍存在，随后收到对应错误，同一客户端请求只建立一次上游请求

#### Scenario: 原始错误仍按原契约透传

- **WHEN** 上游显式返回 `type: error`
- **THEN** 仍使用已有失败契约，Messages 不重复追加错误帧，三协议不补成功终态

### Requirement: 结果记录与资源释放必须保持一致

静默 EOF 的请求监控和账号最近结果 MUST 单次记失败，监控记录实际 HTTP 200，连接和并发槽 MUST 释放一次。已确认失败后取消不得覆盖失败；账号被删除或换凭据后不得写回旧请求结果。非 SSE / 非流式响应不适用新增终态要求。

#### Scenario: 失败终态输出时取消

- **WHEN** 客户端在静默 EOF 错误帧输出时取消
- **THEN** 监控和账号保持原失败，资源仍释放

#### Scenario: 正常完成和提前取消

- **WHEN** 正常流完成，或在终态前取消且尚未确认上游失败
- **THEN** 保留原正常结果及 499 取消语义，Responses 正常终态输出后的取消仍保留成功

#### Scenario: 非流式兼容

- **WHEN** 网关收到完整非 SSE JSON 或非流式请求
- **THEN** 沿用原读取和返回逻辑，不要求 `message_stop`
