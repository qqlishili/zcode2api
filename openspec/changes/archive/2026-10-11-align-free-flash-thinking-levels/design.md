# Design

## Context

见 proposal.md。Chat 与 Responses 已共用 _apply_reasoning_params；网关固定免费模型后再运行多模型归一器，预算和非法档位在此被静默折叠。pi 1.0.4 已通过真实 CLI + localhost SSE 夹具验证模型级映射可用。

## Goals / Non-Goals

复用现有转换与参数错误出口。仅调整思考输入契约和 pi 目标模型项，不新增设置或依赖、不改流转换器，不修改 pi 源码；保留先前静默截断差分。

## Decisions

1. 将现有 _apply_reasoning_params 改为返回参数错误或成功：先检查原始请求所有已支持 effort 字段、thinking/enable_thinking，再生成 Anthropic 参数。Chat/Responses 传播其错误，Messages 在 _normalize_body 前复用。这样在字段被覆盖前发现非法或冲突输入，无需新增三份校验器或异常框架。
2. effort 仅接受精确 low/high/max；多个来源相同可复用，冲突拒绝。显式关闭无论优先级均拒绝；非法参数形状拒绝。enabled/adaptive 可在没有 effort 时沿用默认；有效 effort 配预算时移除预算，只有预算时拒绝。不再猜测最接近档位。
3. 删除 _normalize_thinking_for_model 与其多模型常量；免费模型固定逻辑和其他规范化保持。不新增多模型矩阵或配置中心。
4. pi 目标模型项配置 off/minimal/medium/xhigh=null，low/high/max 原值映射，compat.forceAdaptiveThinking=true。兼容字段合并保留，其他字段和 settings 逐项比对。使用模型项验证的隔离复制，不向真实服务发测试请求，不输出凭据。

## Risks / Trade-offs

- 老客户端继续只发预算会得到 400 → 先对齐 pi，再收紧网关；API 文档一段说明迁移。
- pi CLI 会把某些强制非法 flag 钳到合法值 → 明确这是客户端行为；网关只能拒绝实际收到的非法参数，UI 可用档位通过实际 pi 注册表核验。
- 同文件存在先前修复 → baseline.patch 保存本轮前差分，自审增量，不整文件回退。
- 私有配置在工作区之外 → 修改需工具权限允许；私有备份留在 pi 配置旁，不进入项目。写前复核文件未被并发修改，按现有 JSON 格式合并。

## Migration Plan

Antigravity CLI 只读审查方案后，先备份并对齐 pi 模型项，以 localhost 实际 CLI 验证；再用红绿反例落实网关契约。最小文档更新后运行受影响隔离回归与 Ruff，独立 Antigravity CLI 验收。通过后完成 OpenSpec 任务，不自动 Git 发布或重启 VPS。回滚仅恢复本轮目标字段与代码增量，保留其他用户改动。
