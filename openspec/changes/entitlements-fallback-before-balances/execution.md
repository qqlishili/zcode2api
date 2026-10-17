# 执行记录

## 2026-10-17 修复上线（8/9，真实结算恢复仍开放）

- 用户明确“按账号下所有余额”，列举两个 Global Build 未生效但仍有其他额度的账号；据此统一 proposal/design/tasks：任一真实模型余额可用则账号 ACTIVE，余额全空且有授予权益才区分等待生效/等待结算。保留原 EXHAUSTED 不可选及后台刷新约束，不将授予量加为余额。
- 页面红灯为 4 失败/2 通过，后端新增回归红灯为 4 失败/9 通过（其中两个为继承重复，已去重）；修复后专用 12 项通过。第一组额度/槽位/模型/冷却 81 项通过；页面 13 项 DOM 通过，包含其他活动同模型余额和已有余额到账的展示。
- 初轮完整 CI：Ruff、mypy 通过；pytest 1258 通过/4 失败，覆盖率 82.10%。未改业务基线中旧模型断言和地区清池夹具仍失败，另外两个单跑通过；全量警告证明补货测试替换全局 asyncio.sleep 终止了 session Mock 线程。仅修旧断言/URL 夹具及模块局部 sleep 替换，组合 219 项通过。
- 第二轮 1261 通过/1 失败：隔离 Mihomo 的 interrupted_before 本地连接超时，单独三轮为 1 失败/2 通过。定向时序记录显示连续 production_pid/check_base 扫描让同一事件循环停顿 4.07 秒，磁盘写入合计不足 0.1 秒；更新器与客户端在生产中独立运行。测试 helper 将更新操作放到独立事件循环，保留 3 秒连接超时和全部断连/归属/终态断言，不改 Mihomo 生产逻辑。原始失败与诊断保留在 VPS 的 full-final.xml、fault-check-0.xml、timing-diagnostic.log；额外延迟实验未复现，不作为修复证据。
- 修复测试夹具后，9 项真实 Mihomo 故障场景通过；最终全量 1262 项通过、覆盖率 82.10%，Ruff app/tests、mypy app/constants.py 通过。已核对本机与 VPS 隔离候选的 8 份业务/测试文件 LF 哈希一致；证据 full-green.xml/log、checks-green.json、tested-code-hashes.json。
- 业务提交 `d9379fe32ef0234254ee2c7f637b1bdcaef2cb7b`；默认分支推送曾被自动审批拒绝，用户随后明确“允许”，正常审批通过。连同记录提交 `a77584b` 已推送并 VPS 原目录快进上线；在途为 0 后仅重启网关，耗时 0.82 秒，PID 1679556、active/running、NRestarts=0。回退保全于 `/root/entitlements-recovery-20261017-eskas8ag`，未覆盖账号库。
- 上线后真实管理 API 只刷新两个指定账号，2 成功/0 失败、不领取活动；均 enabled/active，Global Build 1 亿未生效、23:00 生效，其他活动仍有真实余额。一个有 Weekend Flash 约 1.305 亿余额，另一个 GLM-5.3 2,986,112、Flash 0；授予量未计入 remaining。上线页面原始 JS + 真实 API 数据的 DOM 验收通过。
- Windows → 公网 → VPS plugin 的 Flash/GLM-5.3 各普通/SSE共 4 项均 HTTP 200、模型精确一致、end_turn、错误 0、逐字 OK。`.env`、注册表、生命周期、Mihomo 配置哈希与 Mihomo/WARP PID 保持，chat direct / claim proxy、active=saved、pending=false；plugin host_alive=true/stale=false。报告 after.json、after-dom.json、public-after.json、deploy.json。
- 真实同一权益到账后的自动恢复仍无当前样本，两个指定账号现有其他余额、并非 balances 空样本；不以监控方法的夹具恢复核销该项。当前 8/9，不自动归档；视觉/真实浏览器兼容性及长期运行继续保留未验。

> 2026-10-16 核对及复核：下文保留历史实施与部署记录；当前源码和测试实际使用“额度已授予，等待生效”，与 design 的“等待结算”不一致。测试通过只证明当前行为，不能核销原要求，相关 tasks 已撤回勾选。proposal 与 design 的状态要求也尚未对齐；本次未修业务代码、复验线上或发布，整项保持未完成。见 [项目交接](../../../docs/HANDOFF.md)。

## 真实验证（先于落地，铁证根因）

- 账号 `国际-ok li` 实测：`billing/balance` 返回 `plans[0].plan_id=zcode-v3-start-plan-referral`（夜间蹬蹬卡）、`status=active`、`entitlements[0].grant_units=100000000`（1 亿 GLM-5.3-Flash，period=daily_night），但 `balances=[]`（空）。
- 该账号发对话返回 `1005 exceed quota limit`——证明 `entitlements` 只是「已授予资格」，`balances` 未结算时**实际不可消费**。
- 旧逻辑 `fetch_quota` 在 `balances` 空时无差别判 `EXHAUSTED` + "额度已用完"，误导用户禁用并陷入死循环。

## 分组实施与验收

- 新增 `_has_granted_entitlements` 纯函数 + `fetch_quota` 的 `balances` 空分支区分「待结算」vs「真用完」。
- 单测：5 项纯函数边界 + 2 项 fetch_quota 集成（balances 空 + 有/无 entitlements）。
- 全量单元回归通过（exit 0）。

## CI 与部署

- `ruff check app tests`、`mypy app/constants.py`、`pytest` 全量通过。
- 提交 `45d00dd`，push 到 origin；VPS `reset --hard` + `systemctl restart zcode2api`，服务 active。

## 真实验证（部署后）

- 刷新 `国际-ok li`：`last_error` 从「额度已用完」变为「额度已授予，等待结算」，`status=EXHAUSTED`（不可选），`quota={}`（不虚构剩余额度）。

## 剩余限制

- 未验证 `balances` 结算后「待结算 → ACTIVE」的自动恢复闭环（需等该账号 balances 实际生成，属上游时序，非代码可加速）。
- `billing/balance` 带 `?app_version=`（P1）未做，留待后续确认是否有差异后再决定。
