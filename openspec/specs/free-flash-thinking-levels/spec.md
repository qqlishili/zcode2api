# free-flash-thinking-levels Specification

## Purpose
约束免费 GLM-5.3-Flash 的三协议思考参数，使用户明确选择的 low/high/max 保持到上游，省略参数遵循上游默认，并通过 pi 的本地模型配置使可选档位与网关契约一致，避免关闭、折叠和预算猜测。

## Requirements

### Requirement: 三协议保持合法思考档位

网关 SHALL 将 Chat 的 reasoning_effort、Responses 的 reasoning.effort、Messages 的 output_config.effort 中明确给出的 low/high/max 原值传为上游 output_config.effort，且 thinking.type 为 enabled。既有扩展字段使用同一契约；同一请求的多个 effort 必须相同。未给出 effort 时 SHALL 不生成 effort；未给出任何思考参数时 SHALL 不生成 thinking。

#### Scenario: 明确合法档位
- **WHEN** 三个接口分别收到 low/high/max
- **THEN** 上游收到同一档位，max 不被降档

#### Scenario: 使用上游默认
- **WHEN** 请求没有思考参数，或仅明确启用 enabled/adaptive
- **THEN** 网关不生成 effort，未指定 thinking 时也不生成 thinking

#### Scenario: 同档扩展参数和预算
- **WHEN** 多处明确 effort 相同，且可同时携带 budget_tokens
- **THEN** 合法 effort 保留，budget_tokens 移除，不用于猜测档位

### Requirement: 不支持的思考请求在上游前拒绝

网关 SHALL 对显式关闭、非 low/high/max 的 effort（含空值或错误类型）、冲突 effort、非法思考参数形状和仅 budget_tokens 无明确 effort 返回 HTTP 400，沿用现有 invalid_request_error 格式，错误信息列出 low/high/max。拒绝发生在选号和上游调用前，不消耗账号或免费额度。

#### Scenario: 非法档位
- **WHEN** 任一接口收到 minimal/medium/xhigh/none/off/disabled、未知字符串或非字符串 effort
- **THEN** 返回 400 并列出合法档位，没有上游调用

#### Scenario: 关闭或仅预算
- **WHEN** 请求携带 enable_thinking=false、thinking.type=disabled，或没有 effort 的 budget_tokens
- **THEN** 返回 400，没有上游调用

#### Scenario: 相互冲突的参数
- **WHEN** 原始请求含不同 effort，或合法 effort 同时要求关闭
- **THEN** 返回 400，转换不得覆盖非法字段后放行

### Requirement: pi 模型配置对齐三档

pi 的 zcode/GLM-5.3-Flash 模型项 SHALL 配置 thinkingLevelMap 仅启用 low/high/max，以及 compat.forceAdaptiveThinking=true；保留其他 provider、模型字段与已保存 high 选择。实际 CLI 出站 SHALL 携带 adaptive thinking 与对应 effort；证明请求确实完成。配置与私密备份 SHALL 不进入公开仓库。此契约限制模型可用选项，不承诺修改 pi CLI 对强制非法 flag 的自身钳制行为。

#### Scenario: 三档真实 CLI 请求
- **WHEN** 隔离配置和修改后的真实模型项分别通过实际 pi CLI 请求 low/high/max
- **THEN** localhost 接收端收到对应 effort、没有 budget_tokens，CLI 收到完整成功内容

#### Scenario: 保护其他私有配置
- **WHEN** 合并模型配置
- **THEN** 其他模型、密钥、地址与 settings 内容不变，脱敏结构核对通过

### Requirement: 保留既有网关生命周期

思考参数修改 SHALL 保持正常输出、流内错误、静默 EOF、客户端取消、账号结果及资源释放行为，不将失败误报为成功。

#### Scenario: 关联回归
- **WHEN** 运行既有流错误、静默 EOF、取消和正常终态测试
- **THEN** 原有终态及资源释放断言保持通过
