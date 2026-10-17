# 执行与验收记录

## 规范后续合并（2026-10-17）

`split-claim-and-chat-egress` 实施验收后已同步主规范。本 change 的验证码 requirement 也合并为相同完整块，保留 direct 满/空水位、无可选账号及 proxy 水位场景，并加入 claim 按需且不常驻预热；避免后续归档覆盖已上线用途边界。仅调整规范，历史修复和验收记录不变，两份 change 均未归档。相关主规范及两份 change strict 通过；全仓旧 gateway-unavailability 长文本警告见分流执行记录。

## 修复及隔离验证（2026-10-17 CST）

- 根因：补货计划按账号代理计算，direct producer 却强制使用 None；已满的直连水库与被检查的代理水库不是同一个。共享目标解析新增 direct 分支，返回单一 None 目标，已有水位和缓存逻辑因此一致。
- 只修改 `app/client_pool.py`、`app/captcha.py` 和 `tests/unit/test_refill_domain.py`；不新增配置、依赖，不改账号/节点绑定或 proxy 的区域门禁。
- Windows 基础 Python 预加载 SSL 并复用 venv 依赖执行：修复前 17 项中 **4 失败、13 通过**；修正后相关四份测试 **44/44 通过**。标准 venv 的 SSL 故障没有在本 change 中修复。
- VPS 从 Git 基线导出的隔离目录 `/root/direct-refill-verify-20261017-8sjiqr20/repo` 覆盖候选文件，用现有 venv 运行 11 份验证码/出口单测及网关验证码诊断集成，**104/104 通过**，Ruff 检查三个修改文件通过。该阶段测试使用独立数据目录与 Mock 上游，未读取生产账号库或修改生产源码；实际发布见下节。
- `openspec validate fix-direct-captcha-refill-targets --strict` 通过。上述测试有重叠，不将 44 与 104 相加。
- 日志/JUnit 位于本地忽略目录 `data/diagnostics/direct-refill-fix-20261017/`（red.xml / green.xml / verification.xml）。

## 上线与真实业务

- 修复提交 `d147f5f8b937ab7e8942bd4cf8504318bd318503` 已推送并在 `/root/code/zcode2api` 快进部署；2026-10-17 **01:44 CST** 仅重启 `zcode2api.service`，`/meta` 200。三个修改文件的 SHA-256 与候选一致，网关 PID `1132673`，01:50 再核 active/running、NRestarts=0。
- 回退材料 `/root/direct-refill-recovery-20261017-uyh4tk_q/` 保留旧源码、原 HEAD `736959617fc279d993be4b191d755271f9786f86` 和配置哈希。发布前后 `.env`、注册表、节点生命周期、Mihomo 配置哈希一致，Mihomo PID `2797546`、WARP PID `755387` 未变；保持 `plugin + direct`，未改账号/节点绑定。
- **真实公网验收**：01:45 Windows 显式不使用本机代理，访问公网 `/v1/chat/completions`，`GLM-5.3-Flash`。普通请求 200 / `OK` / stop，2.890 秒；SSE 200 / `OK` / stop，5.719 秒，收到 `[DONE]`，流内错误 0。临时认证文件在 VPS 下载后立即删除、本机验收结束后删除，报告不含凭证。
- **生产补货观察**：就绪后观察 140.5 秒，超过 token TTL 75 秒。与重启前同长度窗口比较，refill **22 → 6**，hit **7 → 6**；两个窗口均有 7 次成功业务请求，后窗口另有 2 次 foreground 求解。修复后补货发生于就绪后 4.6 / 10.2 / 24.4 / 55.8 / 68.1 / 94.6 秒，观察末尾 45.9 秒未再补货。结合满水位回归，确认目标错配引发的持续补货已修复；正常消费/过期补货保留，不把旧窗口全部补货判为浪费，也不据此宣称容量或长期命中率已验收。
- 原 15 份本地文档改动逐文件 SHA-256 与实施前一致，未纳入本修复提交；交接同步本子项完成及主线未完成边界。change 不自动归档，不修改旧 change 的核销状态。
- **验收后定时更新**：`mihomo-probe-updater.service` 于 01:45:22–01:51:51 自动运行、ExecMainStatus=0，01:51 更新 `.env`、注册表及生命周期，后续哈希因此不同于发布前保全值。01:55 实际网关仍为 `plugin + direct`、PID `1132673`，Mihomo 配置哈希未变；注册表 active 5（新增 TW `21201`）、draining 136、empty 659，HK / JP 准入仍为 0。发布阶段和 140.5 秒验收窗口配置一致的结论保留时间边界；没有回写文件抹掉此次独立更新，新增节点当前普通/SSE 未手动复验。

本地忽略目录 `data/diagnostics/direct-refill-fix-20261017/` 保存 `public-acceptance.json`、`manifest.json`、`observation.json`、`final-status.json`、`updater-status.json` 与隔离测试 JUnit。VPS 回退目录保存对应运行证据。

## 剩余限制

只验收本次 direct 补货目标修复。HK / JP 对话恢复、TW `21200` 验证码失败成因、冷启动与稳态容量、领取/对话分流、全量测试和 Windows 标准 venv 的 SSL 故障均未因本修复关闭；新活动真实发额度、WARP 公网网关链路及长期账号风控未覆盖。
