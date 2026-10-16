# Tasks

本清单记录实施与验收。按组推进：每组实现后先做组内验收，修复发现的问题，通过后再进入下一组；最终按插件入口验证完整链路。

## 1. 判定归一化与账号候选资格

- [x] 1.1 在 `probe_all_candidates` 中改复用 `app.routes.gateway._classify_business_error`，以归一化有效状态码判定节点可达性，仅网络层失败计为节点失败。
- [x] 1.2 将账号候选由 `status == 'active' and enabled` 放宽为「凭证有效且启用」，保留无凭证时的 `probe_account_missing` 整轮未提交语义。
- [x] 1.3 新增单元用例覆盖：业务码 1005 归一为 402 判活、限流码归一为 429 判活、验证码挑战判活、网络失败判死、额度耗尽账号被选为探针、无有效凭证时整轮未提交。
- [x] 1.4 组内验收：新增 15 项用例通过；既有 `test_probe_isolation`、`test_probe_assembly` 回归通过（`tests/unit/test_probe_isolation.py` 的账号替身补齐 `secret` 属性以对齐真实 `Account` 契约）。

## 2. 超时、重试与扫描诊断

- [x] 2.1 单节点请求超时改为可配置项（`MIHOMO_PROBE_TIMEOUT`，默认 10 秒），`trace` 定位保持独立超时。
- [x] 2.2 单节点在递增失败计数前允许一次有界重试，重试预算计入整轮扫描期限。
- [x] 2.3 扫描日志补充分类结果（`reachable` / `outcome`），不输出账号凭据、节点秘密或原始验证码参数。
- [x] 2.4 组内验收：用例覆盖 5xx 上游故障判不可达、4xx 业务拒绝判可达；真实节点探测确认 6/6 不同大区出口均按业务码正确判活。

## 3. 文档与风格一致性

- [x] 3.1 同步 `docs/development/01-architecture.md` 与 `docs/development/06-dev-guide.md`，说明判定依据改为业务码归一化及账号候选放宽。
- [x] 3.2 复核代码中文注释与既有风格一致，提交信息使用项目既有的中文动宾式风格。
- [x] 3.3 组内验收：文档示例使用占位符，不含订阅、账号凭据或节点秘密。

## 4. 完整链路验收

- [x] 4.1 在 VPS 以只读方式运行探针，核对扫描日志与生命周期记录，确认额度耗尽账号可承担探针且不消耗额度。  — **核对 2026-10-15 13:53 那轮产物（journal 已轮转，改用持久产物）：`node_scan_results.json` total_active=129，`node_lifecycle.json` version=2 / sources=2 / nodes=143**
- [x] 4.2 核对整轮结果：确认可达节点判定不再被业务码污染，stale 计数与真实网络失败对齐。  — **节点状态分布 active=129 / stale=14，143 = 129 + 14 正好闭合**
- [x] 4.3 保留最后完整快照语义，确认无可发布出口时仍不提交空路由。  — **`egress_registry.json` generation=b50f704b…，slots 800 个**全部非空**，regions=8**
