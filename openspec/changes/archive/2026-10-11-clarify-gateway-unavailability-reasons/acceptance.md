# 实施验收

任务：`HP_ZCODE_ERROR_APPLY_20261009_237d9380`。首轮验收时点：2026-10-09 05:16 UTC；主控发现修复后的当前版本结果见末节，旧437项结果仅为历史记录。仅本机实施与隔离验证，未部署、提交、推送或归档。

## 结果

- tasks：11/11，`apply-4.2.json` 实测 `all_done`。
- 三协议的共享最终503保留HTTP/type和旧展示文案，新增14类原子原因、来源、混合/未知/尝试上限、重试三态、可靠等待秒数及请求ID；公开结果与reqlog同源。
- 共用原健康/模型谓词；不改变选号偏好、游标、内部重试次数、状态转换或槽生命周期。仅终止时读取锁内快照。
- 新增证据匿名、白名单、有界32条，截断不影响原因聚合；缺失模型窗口不归零，未知429不按额度字样踢号。

## 红绿及原始输出

| 阶段 | 原始记录 | 退出码与结果 |
|------|----------|--------------|
| 原环境沙箱内启动 | 本轮终端原始输出 | pytest 4，导入 `_ssl` 失败，未进入用例；PATH/add_dll_directory 仍失败；不能据此认定虚拟环境损坏 |
| 第一段业务红例 | `red-1.log` | 1，24项真实入口均 `KeyError: code`；独立Python3.13.3复用项目site-packages |
| 筛选绿 | `green-selection.log` | 0，9项 |
| 第一段绿 | `green-1.log` | 0，42项（24入口+9筛选+9reqlog） |
| 第二段无效夹具尝试 | `red-2.log` | 1，13项setup AttributeError；不存在的Mock函数，**不是业务红例**，已改为临时真实TCP路由 |
| 第二/三段HEAD基线红 | `red-2-baseline.log`、`baseline-red.py` | 1，22项均在真实503后因缺少code失败；只在隔离进程加载HEAD网关，未回滚源码 |
| 原解释器入口绿 | `green-2-native.log` | 0，37项 |
| 扩展边界绿 | `stage23.log` / `stage23-final.log` | 0，70 / 77项 |
| 关联回归首轮 | `regression-41.log` | 1，174通过、1失败：旧监控文案兼容断言；已恢复原监控文案，没有删断言 |
| 生命周期/协议回归 | `regression-42.log` | 0，253项 |
| 合并验收 | `final-tests.log` | 0，437 passed in 73.40s |
| 差分自审后复验 | `final-reviewed-tests.log` | **0，437 passed in 75.39s** |
| 六组实际入口样本 | `response-samples.log` | 0，6 passed / 48 deselected；三协议×stream两种值，JSON503、ID和监控字段逐项一致 |
| strict | `strict.log` | **0，valid=true，issues=[]** |

原解释器实测见 `native-environment.log`：`.venv/Scripts/python.exe`，CPython3.13.13 / OpenSSL3.6.2，沙箱外只读执行退出0。独立Python的早期结果不冒作原解释器验收。

## 可复验命令

从项目根在PowerShell运行。每次使用新的可丢弃basetemp（pytest会清理已存在的同名目录）。测试前设置进程级数据目录，不更改持久环境：

```powershell
$env:ZCODE_DATA_DIR=(Join-Path $PWD 'openspec\changes\clarify-gateway-unavailability-reasons\test-data')
.\.venv\Scripts\python.exe -m pytest tests/integration/test_gateway_unavailability.py tests/integration/test_gateway_monitoring.py tests/integration/test_account_concurrency.py tests/integration/test_gateway_connect_fail.py tests/integration/test_gateway_risk_cooldown.py tests/unit/test_reqlog.py tests/unit/test_selection_evidence.py tests/unit/test_unavailability_result.py tests/integration/test_gateway_lifecycle.py tests/integration/test_gateway_openai.py tests/unit/test_regression_baseline.py tests/unit/test_openai_compat.py tests/unit/test_responses_compat.py -ra --basetemp=openspec/changes/clarify-gateway-unavailability-reasons/tmp-final-reviewed -p no:cacheprovider
openspec validate clarify-gateway-unavailability-reasons --strict --json
```

最终命令实际经 `require_escalated` 执行，只为解除沙箱DLL初始化限制。未安装、重建或改变全局PATH。原始测试stdout/stderr由 `Tee-Object` 保存，每次用 `$LASTEXITCODE` 保留pytest原退出码。

HEAD红例命令：同一进程级环境下 `.\.venv\Scripts\python.exe openspec/changes/clarify-gateway-unavailability-reasons/baseline-red.py`。该脚本只读 `git show HEAD:app/routes/gateway.py` 并在测试进程加载，不修改Git或当前源码。

早期独立解释器命令：设置 `$env:PYTHONPATH=(Join-Path $PWD '.venv\Lib\site-packages')`，使用现有 `$env:APPDATA\uv\python\cpython-3.13.3-windows-x86_64-none\python.exe -m pytest`；分别执行新增入口、筛选单测及reqlog单测，basetemp为本变更独立目录，禁用cacheprovider。

## 隔离与覆盖

`tests/conftest.py`：启动前 `ZCODE_DATA_DIR` 防止模块单例写开发数据；fresh_app每用例重绑独立Store/tmp_path；gateway_client把消息、billing、事件、OAuth及兑换目标指向127.0.0.1，验证码为桩；ASGITransport不运行生产lifespan。Mock uvicorn只监听127.0.0.1临时端口，关闭客户端并等待后台任务后结束线程。新增临时诊断路由随monkeypatch恢复。预读取失败使用内存MockTransport，其余换号/状态/三协议经实际入口及本地TCP验证。

覆盖14码/origin、原HTTP200与有效402/429/500、普通与未知429、冷却+模型硬障碍、缺失窗口、上限未尝试候选、同码不同origin、未知/恢复旧状态、先失败后成功、取消/流关闭/park与reacquire、Retry-After 0/分数/非法/超内部cap/HTTP-date/已消耗等待/多候选max-min、嵌套快照复制、32条截断与业务码白名单。原内部sentinel回归断言改为载荷类型及具体原因断言，保留并发不sleep与账号状态断言。

实际Messages样本（`response-samples.log`）：HTTP503、code=pool_empty、causes=[pool_empty/local_scheduler]、retryable=false、retry_after=null；request_id=`e2763c12a351f2ff` 与X-Request-Id、reqlog.req_id一致。Chat和Responses的true/false样本同语义。管理GET同源验证在 `test_final_503_matches_admin_monitoring`，不是只检查内部函数。

## 差分与边界

源码仅 `app/models.py`、`app/store.py`、`app/routes/gateway.py`、`app/reqlog.py`；文档仅 `docs/development/04-api-spec.md`；测试新增不可用入口、筛选、聚合边界，关联修改monitoring/reqlog/regression_baseline。自审修复旧监控文案，并从收到响应头的时点计算绝对等待，保留前次等待要求；去掉无用参数。`git diff --check`退出0。

原六项未提交改动仍为2插入/255删除：pages.py与06-dev-guide.md各1/1，三个部署脚本分别删除35/32/17行，test_deploy_scripts.py删除169行；本轮未写这些文件。测试结束后短暂出现的两个根目录临时文件已由既有测试自行清理，不自行删他人文件。本轮可丢弃test-data/basetemp在最终测试退出后清理，保留日志、脚本和验收记录。

没有生产请求、服务修改、账号/额度操作或新增持久化诊断写入（仅reqlog内存）。未验证生产部署后的真实额度、09:35/10:08历史503现场原因或Pi/Codex消费新字段；不能把本机通过当作免费额度恢复。未执行外部独立审查；本轮要求的验收为以上本机行为证据。OpenSpec目录保持原有gitignore约定，不强制入库或归档。

## 主控硬障碍反例修复（当前版本）

完整读取主控 `zcode2api-retry-barrier-probe.py` 和 `zcode2api-retry-barrier-red.log`。按其同一时序添加本项目真实TCP回归：响应1302/429之前将隔离账号enabled=false。没有运行占用主控夹具目录的脚本，也未清理 `parent-acceptance-237d9380`。

缺口：临时上游失败已观测到account_disabled时，终止状态与failure.state_code相等，原条件却省略了硬障碍，错误返回retryable=true。最小修复只调整该条件：临时失败仍需加入当前硬状态；同一硬失败造成的对应状态不重复作为新原因。不改状态、调度、槽生命周期或等待期限策略。

- 先恢复3.1/4.1/4.2未勾选，apply实测8/11；红例 `barrier-red.log`：**退出1，9 failed / 79 deselected**，6个三协议stream组合及3个禁用/失效/额度硬状态均断言True is False。
- 修复后 `barrier-green.log`：**退出0，88 passed in 13.19s**。实际三协议终态为mixed_unavailability，causes为account_disabled/account_state与upstream_rate_limited/upstream，retryable=false，retry_after=null且无Retry-After头，监控/ID同源。
- 最终当前版本 `barrier-final-tests.log`：**退出0，446 passed in 77.12s**。不是用旧437项结果验新代码。
- strict `barrier-strict.log`：退出0，valid=true、issues=[]。3.1及4.1/4.2复验后重新勾选，`barrier-apply-4.2.json`实测11/11、all_done。
- gateway.py SHA256：`ef257114d3b7dea79d9eda55c63f49075abaea0713bcd1c6ea8353630d7fa3e4`。

红例原始命令：设置本轮独立 `ZCODE_DATA_DIR` 后，`.\.venv\Scripts\python.exe -m pytest tests/integration/test_gateway_unavailability.py tests/unit/test_unavailability_result.py -k 'disabled_during or same_observed' -ra --basetemp=openspec/changes/clarify-gateway-unavailability-reasons/tmp-barrier-red -p no:cacheprovider`。绿例去掉-k，使用tmp-barrier-green。

最终原始命令：

```powershell
$env:ZCODE_DATA_DIR=(Join-Path $PWD 'openspec\changes\clarify-gateway-unavailability-reasons\barrier-test-data')
.\.venv\Scripts\python.exe -m pytest tests/integration/test_gateway_unavailability.py tests/integration/test_gateway_monitoring.py tests/integration/test_account_concurrency.py tests/integration/test_gateway_connect_fail.py tests/integration/test_gateway_risk_cooldown.py tests/unit/test_reqlog.py tests/unit/test_selection_evidence.py tests/unit/test_unavailability_result.py tests/integration/test_gateway_lifecycle.py tests/integration/test_gateway_openai.py tests/unit/test_regression_baseline.py tests/unit/test_openai_compat.py tests/unit/test_responses_compat.py -ra --basetemp=openspec/changes/clarify-gateway-unavailability-reasons/tmp-barrier-final -p no:cacheprovider
openspec validate clarify-gateway-unavailability-reasons --strict --json
```

仍用原.venv与require_escalated隔离测试；Mock仅127.0.0.1。本轮清理仅barrier-test-data和tmp-barrier-red/green/final，原六项改动仍2插入/255删除，git diff --check退出0。未写其它项目、主控目录、生产配置或全局环境；本实施验收时点未提交发布。

后续用户明确授权的Git发布已闭合：见 [release-verification.md](release-verification.md) 与对应JSON。本机、origin/main、VPS提交及11文件指纹一致，原六项修改保留。
