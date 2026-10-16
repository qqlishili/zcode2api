# Proposal

## Why

共享调度器把无账号、额度、并发及换号失败合并成同一个 503 文案，调用方和监控无法据此判断原因或重试意义。增加请求内结构化证据，避免从中文错误信息推测根因。

## What Changes

- 保持最终 HTTP 503 与 `error.type=no_available_account`，增加原因、重试三态和请求关联字段。
- 从现有筛选与换号分支采集证据，区分无候选、尝试上限、混合和未知；不改变选号、账号状态与资源生命周期。
- 复用 reqlog 保存同一结果及有界脱敏证据，保留旧文案和已有监控界面。
- 三协议共享出口使用同一契约；只补必要 API 文档，不扩展流内错误或客户端重试实现。

## Capabilities

### New Capabilities

- `gateway-unavailability`: 定义共享最终 503 的原因、覆盖、重试和监控一致性契约。

### Modified Capabilities

无。相邻流错误、静默截断和思考等级变更的契约保持不变。

## Impact

实施范围为 `app/routes/gateway.py`、`app/models.py`、`app/store.py`、`app/reqlog.py` 及对应隔离测试；必要说明补到 `docs/development/04-api-spec.md`。不新增依赖或数据库表，不修改前端、账号运营、生产部署或其它项目。

本轮仅生成规划产物；不修复或恢复生产额度，不实施、提交或发布。
