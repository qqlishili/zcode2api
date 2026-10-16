# Antigravity CLI 审查记录

## 范围与状态

用户明确授权 Antigravity CLI 完整审查本变更，并允许必要的破坏性更新。下文方案审查是2026-10-11的历史记录，基线为 `dad9c584d9b621753904315b33230a99185f730a`，其旧任务状态和运行快照不代表当前。当前实现已部署并完成VPS实际恢复验收，OpenSpec任务24/24；最终完整CLI验收和事实核正均返回SHIP，提交内容已按本文件记录核销。实现、版本、证据和限制见 [execution.md](./execution.md)。

首审：Herdr 当前 zcode2api 工作区，`wB:pJ` / `zcode-spec-review`，真实 `agy` CLI 1.3.3，模型 `gemini-3.8-flash-high`，独立会话 `4015d584-9021-42f2-a31f-ea895fb63d43`。六份原始规划及带行号源码通过本地 bundle 完整审阅。实际裁决 **FIX-FIRST**。原文保存于 `data/diagnostics/20261011-503/antigravity-review-initial.md`，输入哈希见 `antigravity-reviewed-input-manifest.json`。

修订版复审：全新独立会话 `2d113cb9-9428-4ceb-b498-57e41d03adbc`，`wB:pK` / `zcode-spec-final`，同 CLI/模型。实际返回 **SHIP（可以实施）**，完整正文保存于 `data/diagnostics/20261011-503/antigravity-review-final.md`。输入六份文件哈希见 `antigravity-revised-input-manifest.json`，取回裁决时逐一核对，全部匹配。此裁决来自完整复审，不沿用首审结论。

## 方案首审发现与主代理核验（历史）

| 首审意见 | 核验与处理 |
| --- | --- |
| 10秒排队切片截断仍可完成的单槽请求 | 采纳删除。单次持槽11秒、其它前台仍有预算时不能因旧切片失败；是超时冲突，不是已证明的死锁。 |
| 跨账号共享40秒会剥夺健康出口机会 | 采纳删除。预算只归一个producer，其它出口和有限挑战重解有独立机会。 |
| 本地失败污染出口断路器 | 采纳故障栅栏。当前参数/Node重试最终None会走 `app/captcha.py:322` 的代理失败计数；当前占槽超时抛异常，不能声称它已经走同一计数路径。新增中性失败处理须闭合HALF-OPEN资格释放。 |
| 同出口换号重复求解 | 采纳请求内失败证据复用；有效缓存优先，字典随请求结束丢弃。拒绝无依据增加全局3~5秒负缓存或配置。181只是历史关键词日志行，不证明181次独立失败或唯一根因。 |
| Semaphore无法使用公共API做空闲准入 | 不采纳。本机项目Python 3.12.10标准库确认 `locked()` 检查可立即准入与等待者，`acquire()` 在notlocked分支首次await前减计数并返回；同事件循环连续检查与直接await可以复用单一容量。设计明确禁止中间await和 `wait_for(acquire,0)`，用并发行为测试核验。 |
| 固定P95<9秒、git revert30秒回退 | 不作为已证实门槛。单探针5.91秒不能估计稳定P95，Git操作也不能代替授权。改为逐出口真实输出及10请求并发2全部完成；旧代码/启动参数完整副本可恢复、实测耗时、不回滚额度。 |

首审的冷却300秒与部分报告行号不准确。`app/settings.py:70` 的既有默认为600秒，历史实际配置另需核对；原报告约100行，不能引用不存在的601行。原审查P0/P1定级保留在原文，主代理不把未经核验的定级或陈述当作当前运行事实。

复审后事实校订：原设计及审查提示误写本机 Python 3.11，实际 `.venv/Scripts/python.exe` 为 3.12.10，已重新读取其 Semaphore 源码，准入结论不变。设计纠正版本并明确部署运行时另核对，任务开头同步已返回的 SHIP 状态，24个任务仍全部未勾选。原始审查正文与送审哈希保留，不把旧哈希伪作新版本。

## 最终规划版本确认与验证（历史）

2026-10-11 23:46:54 CST 保存同一独立复审会话的收尾确认：**VERDICT: SHIP**。CLI 实际读取最终 `design.md`、`tasks.md` 与 `data/diagnostics/20261011-503/antigravity-final-input-manifest.json`，确认两处校订不影响实施结论，其余四份规划哈希与已完整复审版一致。完整复审覆盖六份规划及相关源码；收尾确认只核对上述校订，不将其另称一次六文件源码全量审查。

- 完整复审正文：`data/diagnostics/20261011-503/antigravity-review-final.md`，SHA256 `C1A8279C5004CB0347E344E1D0349DA3C0246EA6CAC353350A0E47CB38850F1C`。
- 最终校订确认：`data/diagnostics/20261011-503/antigravity-review-final-confirmation.md`，SHA256 `96298A700C44912BC0FEE1F75A6B6ED8CD5D5DE6D28DDB6F3086BE5D24842765`。
- 最终六份文件逐一核对 `antigravity-final-input-manifest.json`，全部匹配；`openspec validate fix-captcha-failure-diagnostics-and-scheduling --strict` 通过。
- 业务源码、前端、测试和运行文档的 Git 差分为空，只有原先的未跟踪 `CONTEXT.md`；本次没有实施、部署、重启或发起新的验证码/模型请求。

Herdr 的有界 prompt 等待曾返回 timeout；随后读取实际 `done` 状态及完整 SHIP 正文，才记录裁决。等待超时不等于 CLI 审查失败，也不单独证明完成。

`openspec/` 和 `data/` 当前被 Git 忽略，本次文档保存在本机。后续传输/发布须显式包含规划及所需脱敏审查记录，不能假设普通 Git 发布会携带这些文件；本次未执行 commit/push。

## 规划阶段关联范围与运行快照（历史）

图谱项目 `D-Temp-lizi-zcode2api`，代次 `2026-10-11T13:19:02Z`；相关路径为metadata_changed，已用当前源码补证。`get_client` 的所有五个生产调用点为gateway、claim两个入口、quota、install。半开准入若只改gateway，后台调用仍会漏资格；修订方案明确同步这些调用点的网络生命周期，保留原业务语义。

22:49:17 CST通过Netcatty只读刷新：107 VPS服务仍active/running，PID2079197、NRestarts=0、版本2.6.6、工作树干净。21点后退出码6/CPU配额关键词仍出现；这不证明新的独立503请求数量。本次未发模型或验证码请求，未部署/重启。

## 各阶段完成门槛

方案门槛：六份文档连贯、OpenSpec strict validate通过、最终修订版获得实际独立裁决并绑定文件哈希。

实现门槛：任务中的红绿/集成/UI/资源回收证据。

恢复门槛：当前每个独立出口与真实客户端正常完成模型输出；后台补货启用时10请求并发2全成功、无503/429/验证码排队或预算失败，并具备可执行回退。方案审查通过不等于已达到后两项。

## 实现审查与最终验收

实现阶段唯一材料包审查返回 `VERDICT [SHIP]`，材料随机标识 `3ad0fb4531cee8b79008b231` 与主代理核对一致，SHA-256为 `2eedcca0f19d83ac5d359dec06b9b06538734e72b796283a0ae99ee9e7251d72`；正文为 `data/diagnostics/openspec-apply/agy-code-review.json`。该轮早于窄屏CSS和Node管道finally修复，不能作为最终版本完整验收。

最终版全部11运行文件与发布清单一致；4出口、10请求并发2、Messages以及恢复标准启动后的两条请求均实际成功，监控与journal关联完成。页面通过隔离Edge真实DOM、键盘及窄屏检查；Antigravity自身浏览器桥的NOT-VERIFIED保留为工具限制，不作为页面通过依据。

- 最终完整CLI审查实际返回 `VERDICT [SHIP]`，会话 `8439a730-7aff-4454-9d25-788fdbc8d2ba`，随机标识 `c411cd4e02cefa2c357b4f88`；唯一包共14343行，SHA-256为 `5d06d5bc17b9f060efd059313297e777732a7ee233bc415fc3ff01fa6626e8db`。CLI报告完整分页和两张截图读取；主代理核对随机标识、实际源码与证据内容，不以CLI的SUCCESS代替裁决。
- 完整结果 `data/diagnostics/openspec-apply/agy-final-review-c411cd4e02cefa2c357b4f88.json`，SHA-256为 `a3c8536f85901496f94de5f6e1ea5a7a578564bbaeae87549b8a0686332064a7`。送审清单绑定全部运行文件、测试、规划和脱敏证据，包外私有配置未提供。
- 主代理发现该报告任务编号、源码行号和部分事实表述不准确，发送唯一核正包。实际返回新会话 `345d21dd-4c1f-4faa-be0a-ab323e77af24`，随机标识 `6daac456d91eb6432727d8a8` 与输入一致，核正裁决仍为 `VERDICT [SHIP]`；未把新会话冒称原会话。
- 核正包SHA-256为 `152465e4d7cde16948b58f2a6c5f2c2768752ecd4be6c848c8608f7cfe494f77`；正文为 `data/diagnostics/openspec-apply/agy-final-confirmation-6daac456d91eb6432727d8a8.json`，SHA-256为 `4daaa526d6c2bfb5209f553fdab12ffbc8c7316e86079613d56f59726cfef7be`。确认真实24任务分组、监控仅内存500条/重启清零、双核单Node、补货增长采到出口1/2/4、191回归与最后35/15复验时点、历史未知和实际函数行号；逐项列出12要求与47场景，主代理程序对比实际名称，无遗漏或虚构名称。
- 首次暂存暴露6份规划文末多余空行，逐字断言仅删除末尾换行；运行代码与测试输入未变化。CLI核正确认该格式变化不影响完整审查SHIP。完整包仍保持原哈希，不把格式后的文件声称为送审前原字节。
- 核正报告仍误引 `app/templates/monitoring.html` 与 `test_monitoring.py`，实际路径为 `frontend/admin/monitoring.html`、`tests/integration/test_gateway_monitoring.py`；诊断安全TCP用例为 `tests/integration/test_gateway_captcha_diagnostics.py`。其VPS失败memo/本地拒绝叙述没有生产故障注入依据，仅按本地真实manager/TCP用例与源码成立。首批连接读取错误具体环节未知，不采纳“网络毛刺”归因；未将报告这些文字当作运行事实。

## OpenSpec最终验证

| 维度 | 核验结果与证据 |
| --- | --- |
| 完整性 | 24/24任务；12条ADDED/MODIFIED要求、47场景逐项对应当前源码及行为证据。无REMOVED/RENAMED要求。 |
| 正确性 | Node纯校验与Python信任边界、真实manager事件/Node清理、半开准入及五个调用点、三协议×流式真假TCP、管理快照/证据上限、真实页面及VPS客户端验收均有证据；最终局部修复后35/15通过。 |
| 一致性 | 复用现有信号量、客户端池、错误协议与页面组件，未新增依赖、数据库迁移或配置旋钮；中文文档/注释及既有提交格式保持。strict validate、Ruff、实际暂存差分格式和敏感字面量扫描通过。 |

主代理未发现当前契约的阻断项；最终业务门槛已通过。历史退出6细分类、首批一次连接读取错误和统计意义P95仍未知；实际发布成功，生产故障回切场景未触发，隔离旧版启动和完整副本已验证。上述限制按原方案披露，不伪作已验证或新增完成门槛。
