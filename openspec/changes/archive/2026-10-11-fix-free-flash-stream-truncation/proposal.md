# Proposal

## Why

隔离复现确认：上游 HTTP 200 SSE 在没有 `message_stop` 时结束，三协议仍将请求与账号结果记成功，Chat / Responses 还补发成功终态。上一轮只修复显式 `type: error`，静默 EOF 会继续掩盖不完整回答。

## What Changes

- SSE 成功必须确认有效 `message_stop` 终态；空流、部分文本 / 工具参数后 EOF、残缺终态均转失败。
- 复用既有流内错误收口：Messages 保留已透传内容并追加错误帧，Chat 返回错误帧，Responses 返回一次 `response.failed`；不补成功终态、不重发。
- 请求监控与账号最近结果单次记失败，HTTP 200 保持实际值，连接与并发槽照常释放。
- 正常结束（含 `max_tokens`）、非流式、显式错误与取消语义保持，API 文档仅补必要契约。

## Capabilities

### New Capabilities

- `gateway-stream-completion`: 判断三协议上游 SSE 是否正常结束，以及静默 EOF 的失败收口。

### Modified Capabilities

无。主规格目录尚无对应能力；上一轮流内错误变更保留为已完成记录，不重写。

## Impact

限定 `app/routes/gateway.py`、现有 TCP Mock、监控 / 流生命周期测试和 `docs/development/04-api-spec.md`。不增加依赖、配置、数据库迁移或持久化数据；不调整思考参数、选号、免费额度领取或发布方式。OpenSpec 保持本地忽略；本轮完成本地实施与 CLI 验收，提交和 VPS 发布沿用后续既有 Git 流程。
