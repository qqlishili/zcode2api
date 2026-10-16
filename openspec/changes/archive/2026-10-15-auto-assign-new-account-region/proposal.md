# Proposal

## Why

2026-10-14 排查确认：账号大区（`assigned_region`）只由离线脚本 `scripts/seed_account_regions.py` 在上线前批量固化，运行期没有任何入口为新入池账号补发。VPS 实测 16 个账号中，10-09 之前入池的 13 个均有大区，10-12 之后入池的 3 个全部为空。

缺失大区的账号在 `AccountClientPool.resolve_proxy` 中无法走账号专属大区端口，只能落到全局回退分支。该分支要求注册表已加载且存在可用候选，实际可用性依赖探测状态，且在托管注册表形态下与发牌账号的出口选择语义不一致。同时新号在 `_schedule_auto_claim` 领取赠送额度时依赖同一条出口链，出口不稳定会直接放大「授权成功但领不到赠送额度」的表现。

## What Changes

- `Store.add_account` 为持有凭证的 zai 账号即时发牌：入池即分配稳定大区，覆盖后台粘贴、OAuth 授权回传、API Key 兑换、备份导入四类入口（四者底层共用该函数）。
- 新增 `_backfill_regions()` 启动补配，与既有 `_backfill_fingerprints` / `_backfill_install_ids` 并列，为存量空大区账号补发。
- 发牌算法改为「读当前出口注册表 + P0/P1 加权轮转」，只发当前存在活跃出口的大区，避免发到空区后 `resolve_proxy` 返回空导致该账号不可服务。
- 发牌范围限定 P0（HK/JP/TW）与 P1（SG/US），维持既有市场供给梯队；P2（KR/EU/OTHER）不纳入发牌。
- 幂等：已有大区的账号在入池与补配路径中一律保留，重复调用不改变既有绑定。

## Capabilities

### New Capabilities

- `account-region-assignment`：运行期账号大区即时发牌、存量补配与空区规避。

## Impact

- 实施影响 `app/store.py` 的账号创建路径、`app/main.py` 的生命周期补配入口，以及大区发牌逻辑的共享实现；不改动出口解析、注册表发布与探测链路。
- 复用既有 `scripts/seed_account_regions.py` 的梯队常量与权重定义、`AccountClientPool` 的注册表读取能力；不新增依赖、不新增常驻服务。
- 新增单元用例覆盖四类入口发牌、空区规避、幂等保留与存量补配；同步 `docs/development/` 相关小节并保持代码中文注释与提交风格一致。
