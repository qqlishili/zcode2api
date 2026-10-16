# Design

## Context

动机见 `proposal.md`。Chat 在 EOF 后调用 `done()`，Responses 即使未完成也调用 `done()`，Messages 在 EOF 后直接记成功。三者已有 `_UpstreamStreamError`、一次性账号回调和幂等关闭；Mock 已有 `sse_truncate`。

## Goals / Non-Goals

**Goals:** 在现有网关消费循环确认正常终态，复用已有失败收口。

**Non-Goals:** 不改纯转换器接口、消息顺序校验、超时策略或重试策略；不扩展为通用 SSE 解析框架，不在本轮处理思考参数与磁盘问题。

## Decisions

1. 完成信号仅为已解析的 `type: message_stop`。Chat / Messages 在现有循环旁记录完成状态，Responses 复用收到该事件后生成的 `is_finished`；在 EOF 及成功收口前检查。相比把 EOF 当成功，不完整内容可被识别；相比完整帧 / 事件序列校验，保持现有单行解析和无换行完整尾行兼容。
2. 复用 `_UpstreamStreamError` 的错误路径；静默 EOF 使用可区分的小型异常和固定文案。Messages 仅对此异常追加标准错误帧，必要时先加帧分隔，显式上游错误仍原样透传。Chat / Responses 沿用错误 JSON / `fail()`。
3. 错误在终态 yield 前记录；账号回调保持一次性与凭据栅栏，资源共用 `finally: close()`。不增加选号、冷却或重发逻辑，避免同一输入重复消耗免费额度。
4. 完整 JSON 非 SSE 分支不做终态检查；成功、输出上限、提前取消和 Responses 完成后取消沿用原逻辑。截断测试使用现有 TCP Mock，补合成 bytes / lines 的分块和取消用例。

## Risks / Trade-offs

- 未发送 `message_stop` 的上游会从原先误记成功转失败 → 这是本次契约变化；正常终态、输出上限与尾行兼容做回归。
- Messages 已透传的残缺 JSON 无法撤回 → 保留原字节，追加错误前提供独立帧分隔，不把残缺片段拼进错误帧。
- 旧用量测试把没有终态的流当正常透传 → 正常样例补有效终态并保留缓存 / 无换行尾行断言，原截断行为用独立反例验证失败，不能仅修改断言使测试变绿。

## Migration Plan

无数据和配置迁移。完成隔离复现、实施回归与独立 Antigravity CLI 验收后交付差分；本轮不部署或改写 Git。回退只撤销本轮网关、测试和 API 契约补充，保留上一轮显式流内错误修复及用户既有改动。
