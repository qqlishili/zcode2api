# Tasks

## 1. 方案审查

- [x] 1.1 使用 Antigravity CLI 只读审查 proposal/spec/design/tasks，修正阻断意见并通过 OpenSpec 严格校验。

## 2. 流内失败收口

- [x] 2.1 扩展现有 Mock 和监控集成测试，验证三协议首事件及部分输出后的错误在原代码上失败。
- [x] 2.2 在网关统一识别流内错误并延后流式账号结果，补齐跨块、取消和换凭据单测；验证错误终态、失败监控、单次上游调用及幂等资源关闭。
- [x] 2.3 在现有 API 文档补充三协议流内错误契约，核对差分仅含必要说明。

## 3. 回归与验收

- [x] 3.1 运行受影响转换器、监控、并发、生命周期和会话路由测试及变更文件 Ruff 检查，保留正常结束、非流式和取消行为。
- [x] 3.2 使用独立 Antigravity CLI 会话验收最终差分、规格与实际测试证据，修复本次引入问题后复验；OpenSpec 任务全部勾选且严格校验通过。

## 当前检查点

- 方案严格校验通过；基线 63 项通过；新增 12 个集成用例在原代码上误记成功，修复后监控与流生命周期 54 项通过。
- 回归 152 项通过，变更 Python 文件 Ruff 检查及 `git diff --check` 通过；输出保存在 `regression.log`。
- Antigravity CLI 方案审查与独立实现验收均 PASS，结果保存在 `plan-review.json` 和 `acceptance.txt`；6/6 任务完成。
- 后续 Git 发布已完成：本地、GitHub、VPS 均为 `018a48b`，工作区干净；原服务重启正常，本机页面和 VPS 免费 Flash 流式请求通过。Antigravity CLI 发布验收 PASS，结果见 `acceptance.txt`，发布证据见 `release-checks.json`。
