# 执行与验收记录

## 修复及隔离验证（2026-10-17 CST）

- 根因：补货计划按账号代理计算，direct producer 却强制使用 None；已满的直连水库与被检查的代理水库不是同一个。共享目标解析新增 direct 分支，返回单一 None 目标，已有水位和缓存逻辑因此一致。
- 只修改 `app/client_pool.py`、`app/captcha.py` 和 `tests/unit/test_refill_domain.py`；不新增配置、依赖，不改账号/节点绑定或 proxy 的区域门禁。
- Windows 基础 Python 预加载 SSL 并复用 venv 依赖执行：修复前 17 项中 **4 失败、13 通过**；修正后相关四份测试 **44/44 通过**。标准 venv 的 SSL 故障没有在本 change 中修复。
- VPS 从 Git 基线导出的隔离目录 `/root/direct-refill-verify-20261017-8sjiqr20/repo` 覆盖候选文件，用现有 venv 运行 11 份验证码/出口单测及网关验证码诊断集成，**104/104 通过**，Ruff 检查三个修改文件通过。测试使用独立数据目录与 Mock 上游，没有应用到生产源码。
- `openspec validate fix-direct-captcha-refill-targets --strict` 通过。上述测试有重叠，不将 44 与 104 相加。
- 日志/JUnit 位于本地忽略目录 `data/diagnostics/direct-refill-fix-20261017/`（red.xml / green.xml / verification.xml）。

## 上线与真实业务

待完成任务 2.1–2.3：保全回退、Git 快进、仅重启网关、真实普通请求与 SSE、至少一个 token TTL 的补货观察，以及交接更新。当前隔离测试通过不作为线上已修复。
