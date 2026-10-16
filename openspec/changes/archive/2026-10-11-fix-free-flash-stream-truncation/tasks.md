# Tasks

## 1. 方案审查

- [x] 1.1 用 Antigravity CLI 只读审查 proposal/spec/design/tasks 与关联源码，修正阻断意见，方案 PASS 且 OpenSpec 严格校验通过。

## 2. 静默 EOF 收口

- [x] 2.1 复用 TCP Mock 增加三协议空流、部分文本 / 工具参数、残缺终态场景，在原代码复现误记成功，保留失败证据。
- [x] 2.2 在现有网关补终态判定与失败收口，补跨块、尾行、错误帧取消、正常输出上限与非 SSE 回归用例；验证单次失败记录、不重发和资源关闭，新用例全部通过。
- [x] 2.3 在现有 API 文档补一处静默 EOF 契约，核对描述与三协议实际测试一致且无无关扩写。

## 3. 回归与验收

- [x] 3.1 运行受影响转换器、请求监控、并发、生命周期及会话路由回归和变更文件 Ruff，确认正常完成、显式错误与取消无回退。
- [x] 3.2 用独立 Antigravity CLI 验收最终差分、规格与实际红绿 / 回归证据；修复本轮阻断问题后复验，任务全部完成且严格校验通过。

## 当前检查点

- 本轮仅处理免费 Flash 静默 EOF；不改思考参数、账号调度或生产环境，不提交或发布。
- 方案 CLI 审查 PASS；新增三协议 18 个截断反例在原代码全部因 `ok=true` 失败，证据见 `reproduction.log`。
- 修复后相关 115 项、最终受影响回归 206 项通过；变更 Python 文件 Ruff 与 `git diff --check` 通过，证据见 `targeted.log`、`regression.log`、`ruff.log`。
- 非 SSE 的 Chat / Responses 4 个新增反例先失败后修复，保留 `nonsse-red.log`；API 文档仅补一段。
- 独立 Antigravity CLI 最终验收 PASS，证据见 `acceptance.txt`；OpenSpec 严格校验通过。全部任务完成，未提交、推送或部署。
