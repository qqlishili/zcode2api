# Proposal

## Why

上游 HTTP 200 的 Anthropic SSE 流仍可能包含 `type: error`。当前兼容转换忽略该事件，随后输出正常终态，请求监控和账号最近结果因此误记成功。

## What Changes

- Messages 保留上游错误帧；Chat 输出错误帧；Responses 复用 `response.failed`。
- 流内错误记为失败，不补发正常终态，不在输出后重试或换号。
- 流式账号最近结果延后到消费结束记录；连接与并发槽继续复用现有幂等关闭。
- 复用本地 Mock 上游，覆盖首个事件报错、部分输出后报错、正常结束及取消。

## Capabilities

### New Capabilities

- `gateway-stream-errors`: 三种网关流式接口的错误终态与监控收口。

### Modified Capabilities

无；项目尚无对应主规格。

## Impact

- 主要修改 `app/routes/gateway.py`，复用 `reqlog`、转换器和 `_Upstream.close`。
- 测试复用 `tests/mock_upstream/server.py`、网关监控集成测试及流生命周期单测。
- API 文档仅补充流内错误契约；不新增依赖、设置、订阅能力或自动重试。
- 只使用免费赠送的 `GLM-5.3-Flash`；测试使用合成凭据和本地上游，不消费真实额度。
- OpenSpec 保持忽略；本次不提交、不推送、不部署。
