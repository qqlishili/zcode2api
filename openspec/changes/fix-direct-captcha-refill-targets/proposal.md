# 修复直连验证码补货目标

## Why

显式 direct 模式已上线，但后台仍按账号代理端口判断水位，实际求解却进入直连缓存，造成直连缓存已满仍持续补货。VPS 隔离实例已复现，生产最近 30 分钟记录 269 次 refill、26 次 hit，需要先稳定当前直连链路。

## What Changes

- direct 下存在可选 Plan 账号时，仅返回一个无代理补货目标；没有合格账号时返回空目标。
- 补货目标、producer 实际出口和缓存水位统一使用无代理语义；proxy 下继续按账号稳定绑定端口补货。
- 增加直连满水位停止补货、空水位补货、无合格账号及代理隔离回归，部署后核验真实普通请求、SSE 和补货数量。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `regional-captcha-waterfall`：明确显式 direct 的单一无代理水库与按实际出口判断补货水位；保留 proxy 的物理出口亲和。

## Impact

涉及 `app/client_pool.py`、`app/captcha.py`、相关单测与交接文档。复用现有 None / `__default__` 语义，不新增环境变量、依赖或数据库结构。不实施领取/对话分流、不修改代理准入和账号绑定；旧 strict-egress-convergence 与已授权 direct 的历史规范差异继续单独跟踪。
