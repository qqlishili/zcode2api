# Design

## Context

见 proposal.md。`serviceable_ports` 是 `_refill_targets` 的唯一生产来源；现有 producer 和缓存已经支持 None / `__default__`，后台逐目标循环也已按该键判断水位。

## Goals / Non-Goals

统一已授权 direct 下的计划目标、实际出口和水位；不新增配置、不修改 proxy 选路、不实施领取/对话分流。

## Decisions

- 在共享目标解析处处理 direct：有可选 Plan 账号返回 `[None]`，否则 `[]`；返回类型明确为 `list[str | None]`。proxy 分支保留现有稳定绑定和可选账号过滤。相比在补货循环里重写代理，这能让两个补货调用者共享同一语义。
- `_refill_targets` 同步类型和说明，继续复用已有 None 缓存、水位与 producer，不增加另一套直连缓存或补货任务。
- 回归通过真实连接池目标解析及真实补货循环判断水位，只替换求解/调度边界以避免访问上游；先取得失败，再修代码。

## Risks / Trade-offs

- `[None]` 与 `[]` 含义不同 → 覆盖空账号、耗尽/禁用/冷却/Key 账号及 direct 满/空水位；确保 proxy 不因直连缓存已有 token 而停补。
- 生产有真实用户、token 会过期 → 同时记录请求/命中与 refill，观察至少一个 token TTL，不能把所有正常补货要求为零。

## Migration Plan

保全当前源码和受保护配置哈希，Git 快进部署后仅重启网关，保持 Mihomo/WARP 与 direct 配置；验证真实普通请求和 SSE，再核补货计数。必要时恢复保全源码并重启网关，不改节点、账号或准入。
