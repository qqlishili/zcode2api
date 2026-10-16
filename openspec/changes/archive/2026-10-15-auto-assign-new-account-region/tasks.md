# Tasks

本清单记录实施与验收。按组推进：每组实现后先做组内验收，修复发现的问题，通过后再进入下一组；最终按插件入口验证完整链路。

## 1. 发牌算法与常量收口

- [x] 1.1 将大区梯队与权重从 `scripts/seed_account_regions.py` 上移至 `app/constants.py`（`REGION_ASSIGN_P0/P1/P2`、`REGION_ASSIGN_P0_WEIGHTS`、`REGION_ASSIGN_P1_RATIO`），建立单一事实源。
- [x] 1.2 新增 `app/region_assign.py`：`assignable_regions()` 剔除当前无活跃出口的区，`pick_region()` 以「实际占比相对目标占比欠额最大」做稳定增量发牌。
- [x] 1.3 组内验收：连续 20 次发牌落在 P0/P1 且 P0 内部符合 HK:JP:TW = 5:4:2；仅 P2 有出口、注册表全空、注册表不可读三种情形均返回 None（不发空牌）。

## 2. 运行期接入与存量补配

- [x] 2.1 `Store.add_account` 入池即发牌，覆盖后台粘贴、OAuth 授权回传、OAuth API Key 兑换、备份导入四类入口。
- [x] 2.2 `app/main.py` 新增 `_backfill_regions()`，与既有指纹、安装身份补配并列接入 lifespan，只补缺项。
- [x] 2.3 `Store.export()` 导出 `assigned_region`；`import_accounts()` 以备份为权威来源覆盖入池发牌结果。
- [x] 2.4 组内验收：覆盖四类入口发牌、删了重加、换指纹保留、备份往返、启动补配只补缺口、无凭证账号不臆造、仅 P2 时留空——21 项用例通过。
- [x] 2.5 修复组内发现缺陷：导入时 `add_account` 已先发牌导致「尊重备份大区」条件恒不成立，改为备份值直接覆盖。

## 3. 文档与风格一致性

- [x] 3.1 同步 `docs/development/` 相关小节，说明运行期发牌、存量补配与备份保留语义。
- [x] 3.2 复核代码中文注释与既有风格一致，提交信息使用项目既有的中文动宾式风格。
- [x] 3.3 组内验收：文档示例使用占位符，不含订阅、账号凭据或节点秘密。

## 4. 真实环境验收

- [x] 4.1 VPS 只读演练确认 3 个缺大区账号的拟定发牌结果与配额缺额一致（HK / JP / TW）。
- [x] 4.2 执行真实补配：16 个账号全部具备大区，空缺由 3 降至 0；既有 13 个账号绑定未被改动。
- [x] 4.3 全量单元回归通过（既有 `test_client_pool.py::test_consistent_hash_proxy_distribution` 为改动前既有失败，与本变更无关）。
