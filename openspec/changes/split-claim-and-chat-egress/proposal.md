# Proposal

## Why

VPS 直连对话已通过普通请求和 SSE 验收；HK / JP 对话收到 3012，但同出口领取 POST 可返回 1003。当前单一 `ZCODE_EGRESS_MODE` 无法表达“领取固定代理、对话直连或 WARP”，且插件还可能把已选代理改为直连，需要按用途明确实际出口及失败边界。

## What Changes

- 增加 `chat` / `claim` 两种账号请求用途：对话可显式直连或使用本地 HTTP 代理（包括现有 WARP `40000`）；领取及账号管理链路可使用同区固定 Mihomo 槽位。
- 保留旧 `ZCODE_EGRESS_MODE`；新增用途配置默认继承旧模式，当前生产 `plugin + direct` 不自动切流。显式用途配置优先于全局模式及旧账号代理。
- 领取绑定独立于对话准入，以 `(port, node_id, epoch)` 校验固定同区节点；对话 3012 不抹掉领取绑定，移除来源、退役及身份变化仍禁止新请求。
- 同一领取操作的激活、preview、验证码和 POST 继承同一出口；插件只消费已准入的对话出口。验证码水库继续按实际出口/代次隔离，对话预热保留，领取按需求解。
- **BREAKING（启用用途配置时）**：手动选择活动仍可领取，但改用服务器验证码；旧浏览器滑块 token 无法证明与 VPS 同出口，`/claim/manual` 在显式领取用途配置下应明确拒绝，页面转用现有服务端领取入口。继承旧模式时保留原浏览器路径，不宣称其亲和性已验证。
- **BREAKING（代理模式）**：移除插件因未确认或异常而静默直连的行为；代理不可服务时返回既有不可用结果，不能以改 IP 兜底。
- 补齐用途路由、并发隔离、插件真实传参、领取限流和节点排空验收；更新旧的“全生命周期单一物理出口、禁止任何直连”要求。

## Capabilities

### New Capabilities

- `purpose-egress-routing`：用途配置、领取固定绑定、链路归属、插件路由一致性、失败隔离和运维验证。

### Modified Capabilities

- `account-client-pool`：按账号及用途隔离客户端，按有效用途策略决定代理优先级与直连边界。
- `egress-region-pinning`：地区粘性适用于同区代理路由；对话调度只看对话策略，固定领取绑定及退役引用独立判断。
- `strict-egress-convergence`：一致性从整个账号生命周期改为单次用途操作，明确 direct / 固定代理及关联链路。
- `regional-captcha-waterfall`：producer 继承有效出口，保留 direct 水位修复，领取不扩张常驻预热域，断路器按用途区分上游拒绝。

## Impact

- `app/settings.py`、`app/client_pool.py`、`app/egress.py`、`app/captcha.py`；网关、claim、quota、install、sentinel、`egress_probe`、plugin channel；`frontend/admin/accounts.html` 的手动领取兼容处理。复用现有连接池、操作引用、求解器和 Mihomo，不新增代理系统或依赖。
- 仅在实施阶段增加配置说明、脱敏路由诊断和回归；本轮只生成方案，不修改业务源码、账号数据、生产配置或服务。
- 不恢复 HK / JP 对话、不保证新活动到账，不修 TW 验证码成因、额度页面或 Windows SSL。不重写现有探活器为领取扫描器，不升级 vendor，不更改节点监听/系统路由/WARP 模式。
- 与已完成但未归档的 `fix-direct-captcha-refill-targets` 有同名验证码 requirement 增量：实施必须保留其 direct 满水位回归，合并规范时不能覆盖已上线修复。
