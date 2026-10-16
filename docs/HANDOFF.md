# zcode2api 交接文档

> 初始部署快照：2026-10-16 13:30 (CST)
> 本地核对：2026-10-16 14:21 (CST)；线上恢复核验更新至 17:02 (CST)。详见下方本轮记录。
> 用途：把当前状态、已完成、待办、已知缺陷完整交代给下一个接手者（人或 AI）

## 本次核对结论

- **本轮源码发布**：`9ad47f7b8331a647fdde385747452a04e958183a`，修正连接池 direct 模式并增加 3 项回归；71 项相关测试和 VPS Ruff 通过。其余 15 份本地文档核对改动未纳入此次发布，继续保留。
- **Git 边界**：已获用户明确授权，提交已推送 main，VPS 原目录已快进到指定提交；运行源码和测试文件 SHA-256 与本地逐一匹配。最终交接补记另随 Git 同步。
- **运行态边界**：线上为 plugin + direct，网关 PID 715879，官方插件宿主已就绪。Windows 公网普通请求和 SSE 均 200、文本 `OK`，SSE 正常 `[DONE]`，临时直连业务恢复已实际验收。HK / JP 节点恢复仍未完成，见[专项记录](egress_recovery_20261016.md)。
- **主要纠偏**：注册表没有 `health` 字段，原 P1 诊断撤销但线上恢复未完成；探针配对和部分按账号指纹取值路径已核实，不代表整项验收完成。13 个未归档目录包含已实施记录和 3 个归档重复副本。
- **验证及核销复核**：原选定的 205 项隔离测试通过；补充的“额度已生效但未结算”展示场景失败。此前新增 24 个任务勾选中，2 个因改写验收条件而撤回，保留 22 个实现/离线子项；完整指纹调查仍开放。本轮只确认临时直连恢复，不据此核销 HK / JP 或其它未完成任务，见第四节第 7 项。

## 本轮线上恢复核验（2026-10-16，更新至 17:02 CST）

**临时直连业务已恢复，现有 HK / JP 出口未恢复。**固定账号、官方插件和最终外发请求对照：直连与 TW 可返回 200；HK / JP 返回 405/3012 或连接失败。HTTP CONNECT / SOCKS5h 两种入口结果一致，成功连接的 TLS 验证通过；生产配置、provider、监听与运行中选组未发现身份不符。现有证据支持出口相关上游风控拒绝，不能只凭 3012 断言纯 IP 信誉原因。

| 当前验收对象 | 本轮结果 |
|---|---|
| HK / JP 普通请求与 SSE | 固定 `21100` / `21300` 均 405/3012；同区恢复未完成 |
| HK / JP 全量节点 | HK 5 个中 4 个被拒、1 个连接失败；JP 24 个中 22 个被拒、2 个连接失败；逐节点使用独立宿主避免连接复用 |
| 直连对照 | HK、JP 各一账号普通请求 200、文本 `OK`；3 个 active 账号 SSE 均 200、文本 `OK` 且正常结束；另一 HK 账号普通请求虽 200，但被 64 token 上限截断，未算非空验收通过 |
| TW 代理对照 | 同一账号、当前插件经 `21200` 普通请求与 SSE 均 200、文本 `OK`，SSE 有正常结束事件 |
| 实际外部客户端 | 发布修正后，Windows → 公网网关普通请求 200、文本 `OK`；SSE 200、文本 `OK`、`finish_reason=stop` 和 `[DONE]`、无流内错误。发布前和仅切配置后的真实 503 单独保留 |
| 现有来源 | 重新读取两份来源（67 / 76 个节点），无新连接定义；用户确认没有其它节点 |
| 当前处置 | 用户确认“先直连”及 Git 发布；源码 `9ad47f7` 已上线，plugin + direct 模式公网普通请求/SSE 验收通过；HK / JP 仍开放 |

三项目源码核对、完整对照及诊断中连接复用问题的纠正见[出口恢复核验记录](egress_recovery_20261016.md)。本轮未核销其它待办，任务计数仍为 **11 项**。

## 此前线上只读复核（2026-10-16 14:34–14:37 CST）

**当时首要故障是账号所在区没有准入出口。**撤销 `health` 漏写的判断只纠正了诊断，不能视为线上业务恢复；后续直连恢复结果见上一节。

| 核对项 | 当前证据 |
|---|---|
| 部署版本 | VPS `9a6d971902048fa8ac8428097a05b1ad97bef995`，工作树干净；本地为 `8ff1e61`，6 个相关业务源码文件 SHA-256 逐一相同 |
| 服务 | `zcode2api` / `mihomo` 均 active/running、`NRestarts=0`；`/meta` HTTP 200、版本 `2.6.7`，仅证明服务接口响应 |
| 运行模式 | 服务环境为 `ZCODE_REQUEST_CHANNEL=plugin`、`ZCODE_EGRESS_MODE=proxy` |
| 注册表 | 800 槽：active 7、draining 134、empty 659；准入与路由集合一致，准入节点与生命周期身份一致；没有任何槽包含 `health` 键 |
| 各区准入 | TW 3、US 2、OTHER 2；HK / JP / SG / KR / EU 均为 0 |
| 账号 | 共 16 个：active 3、exhausted 10、disabled 3；3 个 active 均启用，分别为 HK 2、JP 1，无显式独立代理 |
| 最近扫描 | updater 于 13:45:17–13:51:10 成功完成，143 节点：7 个 HTTP 200、124 个 405/3012、12 个 network_fail；并非任务未运行或失败退出 |
| 网关日志 | 持续出现“绑定大区 HK / JP 当前无可用健康出口”，最新核对到 14:37:30 |
| 磁盘 | Python `disk_usage` 口径使用率 87.4%、剩余约 2.67 GB；旧“93%”记录不再视为当前值 |

**代码与运行态对应**：`AccountClientPool.resolve_proxy` 对已绑定账号只在同区选择出口；`gateway._dispatch` 把健康大区集合传给 `store.select`，粘性选号同样检查地域健康。因此当前 3 个 active 账号都不满足地域准入条件；别区有 7 个准入槽不能直接使它们可服务。账号 active 与额度缓存也不证明当前真实额度有效。

**恢复任务**：先确认并恢复 HK / JP 同区能受理业务的出口，或另行评估明确的账号重绑定方案。124 个 3012 的成因仍需结合固定账号、请求形态和具体出口验证，不能仅凭业务码认定纯 IP 原因。恢复后必须通过实际客户端非空输出与正常 SSE 终态验收；本轮没有新增模型调用，不声称已恢复或已取得当前端到端失败复现。

**证据时间限制**：请求证据库最近 6 小时没有请求，最近一条 503 为 2026-10-16 02:27:13 CST；该历史 503 不冒充本次请求结果。当前故障依据是准入条件与持续运行日志。只读复核未重启、切换出口、修改账号或运行更新器。

---

## 一、本地状态与部署快照

### 代码流向

本地信息于本次核实；GitHub private 属性和 VPS 信息来自初始交接快照，未重新现场验证。

```
本机  D:\Temp\lizi\zcode2api
  │   HEAD: 8ff1e61  「docs: 增加交接文档」
  │   tracked: 412     核对前未提交: 0；本次文档更新未提交
  │   remote: git@github.com:qqlishili/zcode2api.git
  │
  ├─ push ──→ GitHub **private** qqlishili/zcode2api
  │                    │
  │                    └─ git clone --recursive ──→ VPS
  │
VPS  /root/code/zcode2api
      HEAD: 9a6d971（初始交接记录；本次未复验，不再声称与本机同一 commit）
      tracked: 411     未提交: 0
      子模块: 6a3fe9b49e7e638ed79f00d274ad6e1b93a384cd (heads/main)
```

### 服务（systemd，初始交接记录）

| 单元 | 状态 | 说明 |
|---|---|---|
| `zcode2api.service` | enabled + active | 网关，监听 `0.0.0.0:18088` |
| `mihomo.service` | enabled + active | 出口中继，800 槽位（21100–21899） |
| `mihomo-probe-updater.timer` | enabled + active | 每 6h 跑 `mihomo_auto_probe.py` |

单元文件位于 `/etc/systemd/system/`，路径已指向 `/root/code/zcode2api`
（`WorkingDirectory` / `EnvironmentFile` / `ExecStart` 共 5 处）。

### VPS 运行时数据（初始交接记录；不入库，迁移时单独保全）

```
/root/code/zcode2api/.env                         948 B   （含订阅 token）
/root/code/zcode2api/data/accounts.db            143 KB   （16 个账号）
/root/code/zcode2api/data/evidence.db            475 KB
/root/code/zcode2api/data/node_lifecycle.json    278 KB   （探活结果）
/root/code/zcode2api/data/egress_registry.json    99 KB   （800 槽位）
/root/code/zcode2api/data/plugin-auth.json         6 KB
/root/code/zcode2api/.venv                       153 MB   （shebang 已修）
/root/code/zcode2api/captcha_node/node_modules    25 MB
```

---

## 二、迁移及此前已完成的工作（历史记录）

本节的修复提交号保留作历史线索，不等同于当前重建仓库的提交历史；本次确认的是对应代码和文件形态，未重新执行部署。

### 1. 修复探活判据（commit `3382ca8`）

**问题**：`node_reachable()` 只拒 `effective >= 500`，而业务码 `3012` 被归一为
HTTP 405，落在兜底分支被判为"可达" → 一轮探活 143 个节点里 **124 个假 active**。

**修法**：改成三类判定，风控状态取自 `constants.RISK_CONTROL_HTTP_STATUSES`
（与网关路由、`egress_probe` 同源）：

- A 上游受理业务（200 / 402 / 403 / 429 / 404）→ 可达
- B 上游边缘拒绝（3012 / 归一后 405）→ 不可达
- C 上游或链路故障（5xx）→ 不可达

### 2. 修复探针账号选择（同一 commit）

**问题**：`probe_account_candidate()` 取"第一个 enabled 且有凭证"的账号，
而 `store.list_accounts` 把**已被风控**的账号排在首位 → 用它探任何出口都返回
3012（账号级拒绝，与出口无关）→ 整池误判。

**修法**：优先选 `status == 'active'`；无 active 时退回任意 enabled 且有凭证者。
`status` 用 `getattr` 读取，缺失按非 active 处理。

**效果**：探活从 `active=0` 恢复到 **`active=7`**（见下节实测）。

### 3. 消除硬编码机器路径（`2f3e2f8`）

- `app/settings.py` `BUN_PATH` 默认值 `/root/.bun/bin/bun` → `"bun"`（走 PATH）
- `scripts/plugin_preflight.py` 同样修改
- 8 个 `scripts/diagnostics/*.mjs` 的绝对路径 → 环境变量优先 + 位置反推
- `tests/unit/test_readloop_liveness.py` 删掉多余的 `sys.path.insert`

### 4. 文档补齐（`a666285` + `82489c6`）

- `.env.example` 含 47 个不同配置键（包括注释示例），并非完整环境变量清单。本次 AST 核对仍发现应用读取的部分键未列入，例如 `ZCODE_CLIENT_IDLE_TIMEOUT`、`ZCODE_EGRESS_PROBE_INTERVAL`、`ZCODE_PLUGIN_DRAIN_TIMEOUT`；实际默认值以源码为准。
- `docs/development/06-dev-guide.md` §1 补 `git clone --recursive`
- §5 新增「运行时依赖（Node / Bun / mihomo）」，讲清 systemd PATH 与 shell 不同的约束
- §5 补 `mihomo.service` 示例；修正过时端口示例（21081–21084 → 真实大区分段）

### 5. 清除真实账号标识（`b9fb6fa`）

扫描全仓发现 5 类真实标识共 12 处，替换为中性占位（保留形态以维持断言说明力）：

| 类别 | 替换为 | 处数 |
|---|---|---|
| 账号显示名（中文前缀 + 数字） | `账号A` / `账号B` | 6 |
| 账号显示名（含空格与大小写） | `账号C` | 4 |
| 账号邮箱 | `user@example.com` | 1 |
| 设备 UUID | `11111111-2222-3333-4444-555555555555` | 1 |
| 设备长标识 | `sample-long-id-88634403` | 1 |

**注意**：只做了工作树脱敏，**未重写历史**。旧提交仍可通过 SHA 检索到这些标识。

### 6. 重建仓库为「真 submodule」形态（`aa993eb`）

**背景**：仓库曾两次删库重建（原库为 public，含真实账号名）。最终 pin 到
GitHub private 仓库。

**关键修正**：`vendor/magpie-plugins` 此前被当**普通文件**提交
（mode `100644`，245 个文件），导致 `app/vendor_check.py` 两个方法都拿到父仓库身份：

- `check_plugin()` 的 `current` 取到父仓库 HEAD（`7134751`），与上游 plugins HEAD
  永远不等 → **每天假告警**
- `apply_plugin_update()` 在父仓库上下文执行 `git fetch` + `git reset --hard`
  → **会把整个项目重置到上游 plugins 的 HEAD**

**修法**：改为 `git submodule add`，`git ls-tree` 现为 `160000 commit 6a3fe9b4`。

**子模块 pin**：`6a3fe9b4`，已验证其 `packages/zcode/index.mjs` 的 blob sha
（`166e881c148950776b267dd05ad361758984e590`）与迁移前一致 → **零行为变化**。

**效果**：`check_plugin` 现返回 `current == remote`，`update_available: false` ✅

### 7. 纳入 openspec 与 CI（`9a6d971`）

- 从 `.gitignore` 移除 `openspec/`（该行由 `d2acbe1`（2026-10-07）引入，
  当时仓库为 public，故忽略内部变更记录；现为 private，顾虑不成立）
- 合并两个来源的 openspec 记录：archive **15 个** change（10-11 批次 8 个 +
  10-15 批次 7 个），未归档 **13 个**，共 228 文件
- 补回 `.github/workflows/ci.yml`（ruff + mypy strict scope + pytest
  `--cov-fail-under=50`）

---

## 三、历史实测状态（本次未复验）

以下数字和 HTTP 结果只说明各自记录时点，不能替代当前外部客户端请求、服务和出口池验收。

### 探活（2026-10-16 07:49 那轮）

```
节点状态:  stale 136 / active 7     （共 143）
槽位:      draining 134 / empty 659 / active 7   （共 800）
日志末尾:  phase=committed active=7
```

### vendor 检查（2026-10-16 05:22）

```
check_plugin: current = remote = 6a3fe9b4…   update_available: false  ✅
check_hostjs: local fb06d4ca… / remote 4bfe7881…  update_available: **true**
```

### 真实上游请求（迁移后验证）

```
账号 16 个，active 3
用 akbgpryk 测试：
  [直连]     HTTP 200
  [经 21200] HTTP 200
```

---

## 四、核对后的任务与剩余限制

### 1. 原 P1 撤销：`health` 不是当前注册表字段

本次核实 `scripts/mihomo_auto_probe.py::build_candidate`、
`app/egress.py::validate_registry` 和 `app/client_pool.py::proxy_eligible`：

- 节点健康事实保存在 `node_lifecycle.json` 的 `state`、`last_healthy` 等字段。
- 注册表顶层为 `regions`、`slots`、`generation`、`updated_at`；槽位记录为 `node_id`、`epoch`、`region`、`admission`。
- `build_candidate` 根据节点健康、来源引用和退役状态生成 `admission`；客户端只允许 `active` / `fallback` 的完整槽身份承接新操作。
- 当前契约没有要求 `slots[].health`。对缺失键调用 `.get("health")` 同样返回 `None`，所以 `{None: 800}` 不能证明有 800 个空健康字段或探活漏写。

**处置**：删除“补写 health”的开发待办。后续线上核查应比较 `state → admission → regions` 的对应关系及版本、槽身份，不能靠不存在的字段判断故障。相关判定、槽操作和门禁测试本次通过；后续线上只读复核也确认 800 槽均不含 `health`，准入与节点状态一致，但账号所在区没有准入出口，业务条件仍不满足。

### 2. 当前首要故障：HK / JP 账号没有同区准入出口；风控归因待验证

14:34–14:37 的线上只读复核已确认：3 个 active 账号全在 HK / JP，这两区准入数为 0；其它区的 7 个准入出口不能满足它们的同区路由约束。当前配置及持续日志支持业务不可服务的判断。优先处理该故障，再验收实际请求；不要因原 `health` 判断撤销而从任务列表遗漏。

**历史现象**：2026-10-16 07:49 的 143 个节点中，136 个 `stale`、7 个 `active`；失败表现包含 `status=405 outcome=3012` 和 `network_fail`。

**历史线索**：`server: ESA` 与业务码 `3012` 支持“上游边缘风控拒绝”的判断，但不能单独证明所有失败都只由出口 IP 信誉导致。此前同样存在账号级风控；`network_fail` 也不能归为同一个根因。

**此前记录的对照实验（本次未重跑）**：

```
cloudflare .../trace  → cf-ray: ...-HKG / ...-TPE   （证明隧道通）
Google 204            → 20/20                        （证明节点本身健康）
zcode 请求             → 0/20                        （对 zcode 全被拒）
```

**后续动作**：固定凭证有效的账号，核对其实际出口，再比较账号与出口组合。通用网站可达不等于 zcode 能受理请求；换订阅前先区分账号、出口与链路因素。

**历史可用面**：`21200` 曾取得真实 HTTP 200；该轮扫描另记录了 7 个 active 槽位。两种观测不能改写成“当前唯二出口”，当前数量待现场核对。

### 3. 待人工评估：`host.js` 升级

**此前差异分析**（本地文件大小与来源标注已核实；远端内容未重新获取）：

```
本地: 91452 B（正文 90861 B，2241 行），来源 commit 2076943…，fetched_at 2026-10-15
上游: 98612 B，2425 行
6 个 hunk，新增 187 行，删除 3 行
```

**差异内容**：单一主题 —— 上游新增「工具（tools）」能力：

```
withProviders() 增加 tools 字段
新增整节「tools」：toolsOf(spec) / jsonSchema(zod v3/v4 → JSON Schema) / toolList
用途：把插件的 OpenCode tool hook 暴露给 agent（经 magpie 的 tools MCP server）
```

**此前影响评估（历史结论）**：

- **纯增量**，未改动现有 `init` / `fetch` / `usage` 协议
- 本项目用的 `packages/zcode` **不导出 tool hook**（只做鉴权 / 额度 / 请求改写）
  → **此更新对本项目无功能影响**

**处置建议**：按 spec `REQ-VENDOR-04`（host.js 是协议规格，**严禁自动替换**）
保持"待人工确认"状态。**如需升级**：整文件替换 + 更新来源标注的 commit
+ 跑 plugin 通道回归。

### 4. 待现场核对：磁盘与回退副本

以下为初始交接的磁盘快照，本次没有重新读取 VPS，不能作为立即删除依据。

```
/dev/vda2  34G  已用 30G  剩 2.6G

可回收：
  /root/code/dengyie-zcode2api     241M   旧目录（回滚备份，稳定后可删）
  /root/zcode2api-runtime-stage    219M   迁移时的运行时备份
  /tmp/dengyie-zcode2api-full.tar.gz  14M
  /tmp/ref-zcode.log               11.8M  18089 实例的遗留日志
  ─────────────────────────────────────
  合计约 485M
```

**注意**：先确认新部署稳定、旧目录没有唯一未集成改动且回退材料已保全；删除需另有明确授权。本次未清理 VPS 文件或目录。

#### 旧目录的历史脏状态

```
18 × D openspec/changes/{两个目录}/...   （归档后本地删除的残留）
 1 × m vendor/magpie-plugins            （submodule 状态差异）
```

原清单包含 18 条删除记录和 1 个子模块状态，不能写成“共 18 项”。当前脏状态和是否存在唯一改动均未现场复验；归档副本存在也不能单独证明整个目录无保留价值。

### 5. 保留的隐私与发布边界

```
初始交接的分类统计（总数口径未复核，不再沿用“共 45 处”）：
  VPS 公网 IP          15 处
  真实账号名（两种）    13 处
  真实邮箱             13 处
  /root/code/...       16 处（机器路径）
```

**此前决定**：按私有仓库保留原始记录可读性。本次没有重新验证 GitHub 可见性或扩大数据可见范围。

**若将来需要公开 / 加协作者 / 转 fork**：这些需先处理，且**部分（如
`clarify-gateway-unavailability-reasons/` 的部署日志）是首次提交就带入的，
届时需要评估历史中的暴露与相应处理**，不能只修改当前文件就视为消除。

### 6. 未归档 OpenSpec 状态核对

下表是当前任务入口。代码存在、离线测试通过、历史部署记录和当前线上验收分别判断；本次没有实施功能、重新部署或归档目录。

| 目录 | 本次核对结果 | 剩余动作 |
|---|---|---|
| `captcha-pool-capacity` | 调查提案；已有冷启动、稳态及高频请求的历史测量，未形成实施任务 | 固定负载口径核实补给上限和冷启动，再决定最小改动 |
| `egress-blacklist-investigation` | 线上只读复核确认 3 个 active 账号均在零准入的 HK / JP；最近扫描 7/143 准入，账号与出口两类影响仍需区分 | 首先恢复满足账号地域约束的实际出口，再补业务验收；不直接把 3012 等同于纯 IP 原因 |
| `entitlements-fallback-before-balances` | **部分实现，未验收**：辅助函数已有实现；design 要求“等待结算”，当前代码与测试却使用“等待生效”，相应 2 个勾选已撤回；proposal/design 状态表述也不一致 | 对齐原需求及状态语义，保留实现偏差；补真实结算后自动恢复验收 |
| `fix-captcha-failure-diagnostics-and-scheduling` | 原任务 24/24 勾选，另有同名 2026-10-15 归档副本 | 以归档及其执行证据为历史入口，未归档副本不代表要重做 |
| `fix-global-build-entitlement-display` | **部分实现，未验收**：授予量和本号 preview 代码存在；已复现 `effective_at` 过去仍显示“未生效” | 修正待生效 / 待结算区分，再完成页面真实验收；不能只按代码存在核销 |
| `isolate-mihomo-probe-from-production` | 原任务 30/30 勾选，另有同名 2026-10-15 归档副本 | 以归档及其执行证据为历史入口；当前 VPS 连续性仍需现场证据 |
| `normalize-plan-time-and-status-display` | **部分实现，未验收**：时间渲染存在；`exhausted` 固定显示“用完”，已生效的 pending 额度仍显示“未生效” | 对齐状态及生效/结算语义，补页面验收 |
| `pin-captcha-egress-and-refill-domain` | 未归档副本仍有 8.1 未勾，归档副本追加了稳态通过与高频未达标的不同记录 | 不把两个副本的勾选当成统一验收；80% 命中率是否在同一稳态批次取得，现有记录不足以核实 |
| `plan-slot-start-date-display` | `starts_at`、未开始后置排序和单测已存在 | 页面实际排序、日期及待生效展示验收 |
| `plugin-hot-reload` | **部分实现**：R15 探针已使用 `resolve_proxy(account)`，对应测试通过 | 插件状态/同步/重载 UI、代码重载 API、客户端中断统计分离仍待实施 |
| `remove-pool-claimable-sharing` | 池内共享符号和前端推断已移除；现实现只保留本号活动 | 历史数据清理与部署仅有执行记录；页面及当前数据待复验，不重复清库 |
| `report-app-login-success-event` | 事件参数化、登录事件及 `installed_at` 守卫已实现，相关单测通过 | 新账号 OAuth 到奖励任务完成的真实闭环、bigmodel 实测仍待验收 |
| `unify-activation-fingerprint` | **调查未完成**：原引用属于匿名首启，不能证明账号路径故障；按账号遥测同源只验证了部分路径，不能推出全部身份一致 | 继续逐字段比较登录事件、native 和 plugin 的实际外发身份，补运行与上游影响证据，不关闭调查 |

### 7. 任务核销复核：已有实现不等于整项完成

用户质疑关闭依据后，重新检查了本次 diff 新增的 **24 个勾选子项**及上表中撤销/降低优先级的判断。本次核销对象和证据边界如下；不把“错误诊断已撤销”“实现子项已核实”“整项业务验收已完成”合成一个完成状态。

| 项目 | 现有证据 | 本次可核销范围与保留项 |
|---|---|---|
| `health` 漏写判断 | 当前生成/校验/消费代码，以及 VPS 800 槽无此字段、准入关系一致 | 仅撤销这条诊断；线上 HK / JP 零准入的业务故障继续开放 |
| 探针对齐生产选路 R15 | `EgressProbe.run_once` 调用 `resolve_proxy(account)`；已有绑定配对单测实际通过 | 仅确认实现与离线配对测试，当前真实上游组合验收仍开放 |
| 指纹一致性调查 | 安装、领取遥测和 native 身份头取值已读；插件派生凭证取账号 device_mid | 原引用错误不等于不存在其它差异；完整跨通道逐字段及运行调查未完成，撤回“仅有新证据才继续”的表述 |
| 额度待结算 | helper、当前分支及 7 项相关单测已有证据，但测试和代码同为“等待生效” | 保留 helper、无授予额度分支及已有余额回归等 3 个子项；恢复 design 的“等待结算”验收要求并撤回 2 个勾选，不按当前实现修改要求 |
| 池内共享删除 | 共享函数/调用与前端符号已做当前源码检查；12 项哨兵测试通过 | 保留 12 个实现/静态检查子项；Ruff、preview 空结果的专项行为、当前存量数据和页面验收仍开放 |
| 登录成功事件 | 当前常量、事件参数、Authorization 调用与 installed_at 守卫已读；7 项遥测测试和安装回归通过 | 保留 7 个实现/离线/记录子项；新账号 OAuth 自动闭环、bigmodel 实测及当前线上验收仍开放 |
| 套餐生效与结算展示 | 后端槽位测试通过；新增隔离调用当前 `planGroupHtml` 的过去/未来两个时间场景 | 未来场景标签符合预期；过去场景仍标“未生效”，断言失败。展示任务明确未完成，原 205 项测试不覆盖此遗漏 |
| 历史归档与验证码容量 | 两份任务记录与历史批次指标 | 只确认重复和证据冲突；稳态同批次命中率及当前恢复不能因旧勾选而关闭 |

**尚存的规范冲突**：额度变更 proposal 曾要求不设 EXHAUSTED，design/tasks 则要求保持 EXHAUSTED 且不可选；当前文案又与 design 不同。本次仅记录冲突和撤回无充分依据的核销，没有改业务代码或替用户决定状态语义。

**新增失败证据**：以 Node VM 执行当前 `planGroupHtml`，格式化辅助函数用固定替身、时钟固定；`effective_at` 为过去和未来时均渲染“未生效”，过去场景不满足原提案的 `effective_at > now` 判断。结果保存在本地忽略目录 `data/diagnostics/handoff-20261016/pending-label-review.json`，这是函数级复现，不是浏览器或线上验收。该失败与原 205 项通过结果并列保留。

**建议推进顺序**：先恢复账号所在 HK / JP 的业务出口并实际验收，再处理状态文案、插件重载/UI及其它验收。验证码容量按实际负载另议，`host.js` 升级和磁盘清理各自保留人工判断。原 `health` 误判撤销不代表线上恢复。

### 8. 用户确认的待办总表与本轮执行范围（2026-10-16）

计数口径：13 个未归档目录先排除 3 个归档重复副本，余下 10 个变更尚未完成或验收；本地测试环境另计 1 项，共 **11 项**。三份已有任务清单合计 **17 个未勾选子项**，其它事项未完整拆解，所以 17 不是全项目任务总数。状态保持“未完成”，仅按实际验收结果调整。

| # | 任务 | 仍需完成 | 本轮状态 |
|---|---|---|---|
| 1 | 恢复线上出口 | 恢复 HK / JP 可服务出口，通过实际客户端非流式请求和 SSE | **HK / JP 未完成**：29 个节点不可服务；用户批准的临时直连已上线，公网普通请求与 SSE 实测通过 |
| 2 | 额度待结算语义 | 对齐需求、文案与测试，验证实际到账后自动恢复 | 待完成 |
| 3 | 授予额度展示 | 修复已生效仍显示“未生效” | 待完成 |
| 4 | 套餐时间与状态 | 统一用完 / 待生效 / 待结算及时间展示 | 待完成 |
| 5 | 套餐开始日期与排序 | 实际页面验证日期与未开始后置排序 | 待验收 |
| 6 | 插件热重载与管理面板 | 完成代码重载、操作入口和客户端断开统计区分 | 待完成 |
| 7 | 移除池内领取共享 | 专项验证、当前存量数据与页面验收 | 待验收 |
| 8 | 登录成功事件 | 新账号 OAuth 自动完整链路和 bigmodel 实测 | 待验收 |
| 9 | 指纹一致性调查 | 跨通道逐字段比较与实际外发核对 | 调查未完成 |
| 10 | 验证码容量 | 冷启动、稳态容量与同批次命中率证据 | 待完成 |
| 11 | 本地测试环境 | 修复 `.venv` 的 SSL DLL 故障，恢复标准测试命令 | 待完成 |

用户已明确授权本轮执行第 1 项及必要的线上恢复操作、真实模型请求和 SSE 验证。目标是恢复现有 HK / JP 业务路径并以真实输出验收；保留其它文档改动，不把恢复误写成其它 10 项完成。`host.js` 升级、磁盘旧备份清理和重复目录处置仍为独立运维事项，本轮不据此删除或升级。生产变更先保全回退状态，并记录实际影响；跨区或直连不冒充 HK / JP 恢复。

用户随后明确同意“先直连”，并授权本次 Git 提交、推送和快进部署。仅切配置后实际仍 503；补充调度/连接池修正并完成本地验证后，源码已上线，Windows 公网普通请求及 SSE 均通过实际验收。

**直连的账号影响已核对**：VPS WARP 是本地 `127.0.0.1:40000` 代理模式，项目未使用它；HTTPS trace 与独立 IP 查询均确认原生出口 `107.174.244.167`、`warp=off`，zcode 路由走 `eth0`。多个账号直连会共享此 IP，上游可据此关联；本次请求成功不保证长期无风控。保留账号各自凭证、设备及大区字段，HK / JP 实际出口隔离暂不生效。详细证据见[专项记录](egress_recovery_20261016.md)。

---

## 五、关键约束与踩过的坑

### 1. systemd 的 PATH ≠ 登录 shell 的 PATH

以下为迁移时记录的运行环境；路径是否仍可执行需在 VPS 复验。

```
服务进程实际 PATH = /usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/snap/bin

  node  → /usr/local/bin/node    ✅（且是指向 nvm 版本的**符号链接**，同一文件）
  bun   → **找不到**             ❌

→ 结论：ZCODE_BUN_PATH 必须设绝对路径（删了插件通道会崩）
        ZCODE_NODE_PATH 可省（默认 "node" 能解析到同一文件）
```

核对方法：`systemctl show <服务名> -p Environment` +
`tr '\0' '\n' < /proc/<MainPID>/environ`。

### 2. `.venv` 跨路径迁移要修 shebang

`cp -a` 复制 `.venv` 后，`bin/` 下 24 个脚本（pytest / uvicorn / fastapi /
httpx / coverage…）的 shebang 仍指向旧路径。必须批量替换：

```bash
grep -rl "<旧路径>" .venv/bin/ | while read -r f; do
  sed -i "s|<旧路径>|<新路径>|g" "$f"
done
```

`python -m venv --upgrade` **修不了这个**（实测残留 24 个）。

### 3. PowerShell `Copy-Item -Recurse -Force` 是**合并**不是覆盖

源目录被整个塞进目标下（产生 `openspec/openspec/`），删嵌套层时可能连带删掉
本该保留的内容。**跨目录同步建议用 `git` 或明确的文件级复制**。

### 4. 提交前必须做的检查

```bash
git status --short                    # 确认无被忽略项混入
git ls-files -s vendor/magpie-plugins # 应为 160000（submodule）
git submodule status                  # 子模块是否检出
```

### 5. 本机 `data/accounts.db` 是空的

本次以 SQLite 只读连接核实：本地 `accounts` 表为 **0 行**。历史记录中的 16 个真实账号在 VPS，本次未重新读取。隐私比对需另行取得获准的真实源；不能把本地空库当成线上账号状态。

---

## 六、常用命令

### 本机 → 推送

```powershell
cd D:\Temp\lizi\zcode2api
git status --short
git diff -- <本次文件>
git add -- <本次文件>
git commit -m "<type>(<scope>): <描述>"
git push origin main
```

### VPS → 同步 + 重启

```bash
cd /root/code/zcode2api
git pull --ff-only origin main
git submodule update --init --recursive    # 若子模块有变
systemctl restart zcode2api                 # 业务改动才需重启
```

### 验证

```bash
systemctl is-active zcode2api mihomo mihomo-probe-updater.timer
curl -s http://127.0.0.1:18088/meta
journalctl -u zcode2api -n 30 --no-pager
journalctl -u mihomo-probe-updater -n 30 --no-pager   # 探活日志
```

### 手动跑一轮探活

这是有副作用的运维操作：会请求订阅及真实上游，成功时发布提供器并更新生命周期、扫描、环境代理项和注册表，不是只读检查。执行前确认对应授权、当前单元及运行前提。

```bash
cd /root/code/zcode2api
.venv/bin/python scripts/mihomo_auto_probe.py
```

### 插件通道自检

```bash
.venv/bin/python scripts/plugin_preflight.py
```

---

## 七、仓库结构速查

```
zcode2api/
├── app/
│   ├── channels/          plugin 通道（host.js RPC + 凭证派生）
│   ├── routes/gateway.py  上游请求 / 账号切换 / 错误归一化（_classify_business_error）
│   ├── client_pool.py     账号→出口绑定（一致性哈希）
│   ├── egress_probe.py    出口活性探针 + 断路器
│   ├── vendor_check.py    vendor 层版本监控（只告警，不自动应用）
│   └── settings.py        主要应用配置与默认值（identity / scripts 另有环境变量读取）
├── scripts/
│   ├── mihomo_auto_probe.py   订阅拉取 → 隔离探活 → 发布固定槽提供器与注册表
│   ├── plugin_preflight.py    bun / host.js / 插件目录自检
│   └── diagnostics/*.mjs      8 个历史诊断脚本（2026-10-14 根因判定证据链）
├── vendor/
│   ├── magpie-plugins/        **真 submodule** → pin 6a3fe9b4
│   └── magpie_host.js         单文件复制 + 来源标注（协议规格，人工升级）
├── openspec/
│   ├── changes/archive/       15 个已归档 change
│   ├── changes/               13 个未归档目录（含 3 个归档重复副本，见第四节）
│   └── specs/                 从 archive 提升的 spec
├── docs/
│   ├── development/           架构 / 移植 / 数据格式 / API / 上游协议 / 开发指南 / 路线图
│   ├── adr/0001-vendor-official-zcode-plugin.md
│   └── gateway_retry_controlflow.md   gateway 重试链路只读梳理（848 行）
└── tests/                     单元 + 集成（含 mock 上游）
```

---

## 八、外部依赖清单

VPS 路径和运行时版本为初始交接快照，本次未连接核实；本地已核实的 vendor pin 和文件来源不代表上游最新版本。

| 依赖 | 位置 | 版本 | 能否内联 |
|---|---|---|---|
| **mihomo** | `/usr/local/bin/mihomo` | v1.19.1 | ❌ 30 MB Go 二进制，需外部装 |
| **bun** | `/root/.bun/bin/bun` | 1.4.3 | ❌ 运行时，需外部装 |
| **node** | `/root/.nvm/versions/node/v24.19.0/bin/node` | v24.19.0 | ❌ 运行时（`/usr/local/bin/node` 是它的符号链接） |
| **Python** | `/usr/bin/python3.12` | 3.12.3 | ✅ venv 在项目内 |
| **magpie-plugins** | `vendor/magpie-plugins` | pin 6a3fe9b4 | ✅ submodule |
| **magpie_host.js** | `vendor/magpie_host.js` | commit 2076943… | ✅ 单文件 vendored |

---

## 九、换机器部署清单

```bash
# 1. 依赖
#    Python >=3.11（CI 使用 3.12）/ Node 20+（验证码求解）/ Bun（插件通道）/ mihomo（出口中继）

# 2. 拉代码（--recursive 必需）
git clone --recursive <repo> && cd zcode2api
#    漏了子模块时：git submodule update --init --recursive

# 3. 虚拟环境
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cd captcha_node && npm ci && cd ..

# 4. 配置
cp .env.example .env
#    管理密钥：ZCODE_ADMIN_KEY；网关鉴权另设 ZCODE_GATEWAY_KEY
#    出口订阅：AIRPORT_SUB_URLS；bun 不在服务 PATH 时设置 ZCODE_BUN_PATH
#    数据：从旧机器拷 data/（accounts.db / evidence.db / node_lifecycle.json /
#          egress_registry.json / plugin-auth.json）

# 5. 自检
.venv/bin/python scripts/plugin_preflight.py

# 6. systemd
#    按 docs/development/06-dev-guide.md §5 的示例，替换 <PROJECT_DIR>
#    zcode2api.service / mihomo.service / mihomo-probe-updater.{service,timer}
systemctl daemon-reload && systemctl enable --now zcode2api mihomo mihomo-probe-updater.timer

# 7. 验证
curl -s http://127.0.0.1:18088/meta
#    此接口只核对服务信息；业务验收另需实际客户端非空输出与正常流终态
```

---

## 十、接手者第一件该看的事

1. 先读本页第四节的核对表，再读所选任务的 proposal / tasks / execution；不要把历史未勾选项或重复目录直接当新开发任务。
2. 部署与测试约定以 [开发指南](development/06-dev-guide.md) 为准；`tasks.md` 中旧 `reset --hard` 发布写法不应覆盖现行 Git 快进流程。
3. 要核对线上，先进行获准的只读版本、服务、磁盘及已有探活记录检查；真实探活、模型请求和重启是独立操作，不在“加载项目”时自动执行。
4. 按任务补当前验收证据；原 `health=None` 的 P1 判断已经撤销。

---

## 十一、本次验证与未覆盖范围

### 本地证据

- Git：`git status --short --branch`、`git rev-parse HEAD refs/remotes/origin/main`、`git ls-files -s vendor/magpie-plugins`、`git submodule status`。本地子模块 pin 为 `6a3fe9b49e7e638ed79f00d274ad6e1b93a384cd`，`packages/zcode/index.mjs` 的 Git blob 为 `166e881c148950776b267dd05ad361758984e590`。
- OpenSpec：实际目录计数为未归档 13、归档 15、跟踪文件 228；`CONTEXT.md` 已跟踪，旧执行记录中的“未跟踪”仅是历史状态。
- 图谱：发现时的索引代次为 `2026-10-15T18:27:39Z`；相关路径存在元数据变化和未跟踪提示。图谱仅用于定位，本页实质结论均补读当前源码，没有依赖旧行号或空查询证明代码不存在。
- 源码：核对注册表生成/校验/准入、两种探针、额度槽位与前端、领取共享移除、登录事件、按账号安装与身份头。未改业务源码。

### 隔离测试

| 范围 | 结果 |
|---|---|
| `test_probe_node_health`、`test_probe_assembly`、`test_egress_probe`、`test_egress_gate`、`test_egress_operations`、`test_entitlements_fallback`、`test_plan_slots`、`test_login_success_telemetry`、`test_sentinel`、`test_quota_bonus` | 136 通过，0 失败/错误/跳过，69.694 秒 |
| `test_install`、`test_install_identity`、`test_identity`、`test_fingerprint` | 69 通过，0 失败/错误/跳过，1.912 秒 |

用例均位于 `tests/unit/`。启动前设置 `PYTHON_DOTENV_DISABLED=1`、唯一临时 `ZCODE_DATA_DIR` / pytest basetemp、`ZCODE_EVIDENCE_ENABLED=0`，清空测试进程的代理变量并设置 `NO_PROXY=*`；测试中的上游使用既有替身或 MockTransport，未运行应用 lifespan、真实模型请求或探活脚本主入口。

**解释器限制**：首选 `.venv/Scripts/python.exe` 在收集前报 `_ssl` DLL 初始化失败，直接独立导入也复现；同一基础安装的 Python 3.12.10 能正常加载 OpenSSL 3.0.16。上述通过结果使用基础解释器预先加载 SSL，再从本项目 `.venv/Lib/site-packages` 加载现有依赖执行 pytest，未安装依赖或修复全局环境。因此不能写成“标准 venv 命令已通过”。

脱敏日志与 JUnit 保存在本地忽略目录 `data/diagnostics/handoff-20261016/`：`pytest.log`、`verification.xml`、`identity-pytest.log`、`identity-verification.xml`。临时测试数据与诊断交付物分开，日志不入 Git。

核销复核后的 17 个 Markdown 文件已通过 36 处相对链接/锚点、代码围栏及 `git diff --check`；同时核对任务勾选为 3 / 12 / 7，共 22 个实现或离线子项，撤回 2 个不充分勾选。3 个本次新建的临时测试目录已清理，保留上述日志、JUnit 和新增失败证据；未删除历史项目、变更目录或回退副本。

### 尚未验证

- GitHub 当前 HEAD、私有属性、CI 运行结果；VPS 版本、服务、账号状态、磁盘与准入出口已在后续只读复核中检查，但真实额度、实际请求与全部部署配置尚未验收。
- 新账号 OAuth 自动登录事件闭环、真实额度结算后恢复、bigmodel 的真实事件投递。
- 当前管理页的视觉、交互与真实数据展示；本次前端结论仅来自源码。
- 整库全量 pytest / Ruff / mypy、实际客户端三协议与 SSE；历史测试和部署记录保留原有覆盖限制。

本次终态：本地已核实的实现与选定测试有据可查，核销复核撤回 2 个不充分勾选、保持完整指纹调查开放，并新增 1 个展示失败场景；线上已确认账号地域无准入出口，生产恢复和整项业务验收均未完成。文档已同步，未修改业务源码、提交、推送、部署、归档或清理旧项目副本。
