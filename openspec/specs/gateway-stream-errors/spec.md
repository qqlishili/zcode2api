# gateway-stream-errors Specification

## Purpose
定义免费赠送 GLM-5.3-Flash 请求的流式失败契约，使客户端、请求监控和账号最近结果能够一致识别上游错误，同时保留已输出内容并正确释放请求资源，避免失败被正常终态掩盖或触发重复消费。

## Requirements

### Requirement: 上游流内错误必须成为失败终态

网关 MUST 识别 HTTP 200 SSE 中的 `type: error`。Messages 保留上游错误帧，Chat 输出 `data: {"error": ...}`，Responses 输出一次 `response.failed`；网关 MUST NOT 在该错误后补发正常结束帧或重新请求上游。已提交的 HTTP 200 保持不变。

#### Scenario: 首个上游事件即报错

- **WHEN** 三种流式接口收到首个上游事件 `error`
- **THEN** 客户端收到对应错误，监控记录 `ok=false`，不存在网关补发的 `message_stop`、`[DONE]` 或 `response.completed`

#### Scenario: 部分内容输出后报错

- **WHEN** 上游先输出文本或工具调用，再发送 `error`
- **THEN** 已输出内容保留，随后进入失败终态，同一客户端请求只调用上游一次

#### Scenario: 错误详情不完整

- **WHEN** 上游 `type: error` 缺少合法错误对象或消息
- **THEN** 网关仍判定失败，使用通用错误类型和说明，不将整个事件序列化为日志

### Requirement: 请求结果与资源关闭必须一致

网关 MUST 将流内错误记录为请求失败和账号最近结果失败，每次流式请求只记录一个账号终态；错误、正常结束和取消均 MUST 释放连接及并发槽。流内错误本身不触发新增账号封禁或冷却策略。

#### Scenario: 错误后资源释放

- **WHEN** 流内错误结束请求
- **THEN** 请求监控结束、账号最近结果为失败，连接关闭且并发槽释放一次

#### Scenario: 错误终态输出时客户端取消

- **WHEN** 客户端在错误帧输出时取消
- **THEN** 已确认的上游失败不被改写成成功或客户端取消，资源仍释放

#### Scenario: 正常完成及取消兼容

- **WHEN** 上游正常完成，或客户端在完成前取消且尚无上游错误
- **THEN** 正常完成仍记成功；提前取消仍记 499；Responses 正常终态输出后的取消仍保留成功
