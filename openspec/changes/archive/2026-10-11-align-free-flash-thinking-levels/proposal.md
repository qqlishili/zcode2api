# Proposal

## Why

免费 GLM-5.3-Flash 仅提供 low/high/max；当前网关折叠其他档位、按预算推算并允许关闭，pi 默认发送预算，导致用户选择与上游请求不一致。

## What Changes

- **BREAKING**：三个接口仅接受明确的 low/high/max，非法档位、关闭请求、预算独立请求在选号和上游调用前返回 400。
- 保留合法档位原值；省略参数沿用上游默认；删除预算推算和不可达的其他模型思考分支。
- 仅为 pi 的 zcode/GLM-5.3-Flash 配置三档映射及 adaptive thinking，不改 pi 源码或其他模型。
- 复用现有转换、错误出口和测试夹具，仅补必要 API 契约。

## Capabilities

### New Capabilities

- `free-flash-thinking-levels`：三协议与 pi 的免费 Flash 思考等级契约。

### Modified Capabilities

无。当前主规范目录未建立相关能力。

## Impact

涉及 app/openai_compat.py、app/routes/gateway.py、关联单元/集成测试、docs/development/04-api-spec.md 与私有 pi 模型配置。保留开始前五个未提交文件中的静默截断修复；不新增依赖，不提交、推送或部署。本地 OpenSpec 保持忽略，审查材料不含真实凭据或服务地址。
