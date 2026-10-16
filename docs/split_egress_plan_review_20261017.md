# 分流与后台配置管理方案：Antigravity CLI 审查记录

- 日期：2026-10-17 CST。
- 方案：`split-claim-and-chat-egress`；当前仍为 **0/23 待实施**。
- 工具：本机 `agy.exe`，模型 `gemini-3.8-flash-high`，`--mode plan --sandbox --output-format stream-json`。
- 会话：`74cc857a-3b95-4b8f-95d8-a3a573d7eade`；三次有效文本回合：接收前3份、补齐5份并首审、两份增量复审。
- 复审结论：**SHIP；P0=0、P1=0、P2=0**。这只表示方案文本通过审查，不是代码或生产验收通过。

## 范围与证据边界

输入严格为 proposal、design、tasks 和五份 capability 增量，共8份方案文本。自动审批拒绝了最初携带关联私有源码的外部审查调用，理由为源码外发范围未获明确授权；该调用没有执行。随后收窄为用户明确要求的方案文档并获准执行，没有向审查模型提供源码、HANDOFF、运行数据库或凭证。

早期目录读取受 CLI 无交互权限阻止，text stdin 又未被 CLI 转入模型，因此这些尝试没有有效审查结论。最终通过两段显式提示词在同一会话提供8份带行号文本：先收到 RECEIVED_PART_1 回执，再取得首审及复审真实 result。未使用跳过权限选项；模型回合未调用环境或源码工具。

审查结论不能替代源码符合性、API/浏览器交互、公网普通/SSE、实际出口、新活动 code 0 到账或长期账号风控验收。尤其 **1003 只证明进入已领取业务返回，实际出网身份仍需独立证据**；下方模型原文中的概括不扩大项目验收口径。

## 首轮意见及落实

首轮标题为 SHIP，同时列2项P1、2项P2，主控未将其直接视为无缺陷通过。核对后作最小文本明确化，不增加新机制：

| 项目 | 修订落点 |
|---|---|
| 旧设置端点不能覆写出口对象 | purpose-egress-routing 规范补显式过滤/忽略场景；Task 3.6 增回归断言 |
| 待重启状态由服务端判断 | 规范及 Task 1.3 比较规范化策略和来源，完全等价为 false；不新增哈希机制 |
| TW 暂缺节点须分区登记 | Task 4.3 允许已覆盖地区先验，但缺项时本任务整体仍未完成 |
| 半开取消后许可可重新取得 | Task 2.2 明确中性释放后下一请求能再获许可，保留原规范 |

其中已有部分设计/规范约束，复审修订把它们落成更明确的场景和验收断言，不据此宣称发现或修复了现网代码故障。

## 复审目标指纹

以下相对于该 change 目录，复审期间和取得报告后均核对一致。HANDOFF 与本报告不属于送审输入。

| 文件 | SHA-256 |
|---|---|
| `proposal.md` | `aa3e5a24a2ff9794ad81afd0551505fca67dd0faf11c232e3ddf345d0b01e0ad` |
| `design.md` | `405ae3d80d2e164dfc37cf552634fec8ea2e2b38fe51fe5c8860a649469d6d73` |
| `tasks.md` | `84a66e0d2ddef56e5d5d0720bca75b0cb684da4b1ef4853c0b93d42f9dd5b186` |
| `specs/account-client-pool/spec.md` | `b7ded439e6dda2a2d5521ad282c4fb534b4e302dbc6912f4e51d2a177ac975fe` |
| `specs/egress-region-pinning/spec.md` | `3867b1fb86aa5eed0f3874eb290cbd7ddcd48bcc4a9ec9e99cf41619152fe0d7` |
| `specs/purpose-egress-routing/spec.md` | `8c653fd2f1b7981ecc76a44c5e2b0f5cbd8f12a38340525c4dc8b2b71b4425b0` |
| `specs/regional-captcha-waterfall/spec.md` | `62c4213f716c53e1e33526da05397e9e48c5e608c4ffaf196f3ca3947cce673c` |
| `specs/strict-egress-convergence/spec.md` | `96874332c04bb14a319dd7cb77659d44a30b15572621054bfdcf0df78715dc0d` |

## Antigravity 复审原文

以下为 CLI 最终 result.response 原文，主控不修改其裁决及意见；运行验收口径以上方边界为准。

VERDICT: SHIP

当前缺陷计数：**P0: 0 | P1: 0 | P2: 0**

经过对两份更新文本（`specs/purpose-egress-routing/spec.md` 与 `tasks.md`）及前序 6 份基线文本的严格比对核验，首轮指出的 2 项 P1 与 2 项 P2 已全部闭环，且本次增量未引入新问题或设计冲突，方案完全具备进入实施的条件。

---

### 一、 首轮问题复验与关闭状态核销

#### 1. [P1-1] 旧通用设置端点 `PUT /settings` 防冲刷白名单
- **状态**：**已关闭 (CLOSED)**
- **文本证据**：
  - `specs/purpose-egress-routing/spec.md:42-43`：明确写入 *“旧通用设置端点 MUST NOT 接受或覆盖出口策略键；出口保存不得覆盖其它设置或写 .env”*。
  - `specs/purpose-egress-routing/spec.md:56-59`（新增场景 `Legacy settings cannot modify egress policy`）：定义 WHEN 提交旧表单或携带出口键，THEN *“该键被过滤忽略且不能修改或清空出口对象，原有鉴权、刷新及通知字段保持原契约”*。
  - `tasks.md:24 (Task 3.6)`：更新验收条件，包含 *“确认旧 PUT /settings 过滤忽略出口键、新接口不覆盖原设置，补接口契约”*。
- **核验结论**：在规范要求、测试场景和任务执行验收三处均建立了刚性约束，杜绝了通用设置表单静默重置出口策略的风险。

#### 2. [P1-2] `pending_restart` 规范化比对与来源等价判定
- **状态**：**已关闭 (CLOSED)**
- **文本证据**：
  - `specs/purpose-egress-routing/spec.md:61-62`：明确要求 *“待重启状态 MUST 由服务端比较规范化目标策略及来源与生效快照计算，两者完全等价时为 false，不依赖浏览器脏状态”*。
  - `specs/purpose-egress-routing/spec.md:75-78`（新增场景 `Equivalent target is not pending`）：明确 WHEN 规范化策略与来源与生效快照完全相同，THEN *“返回 pending_restart=false，页面重载不产生虚假的待重启提示；仅策略相同但来源不同仍需重启加载该来源”*。
  - `tasks.md:9 (Task 1.3)`：任务落实为 *“服务端比较规范化策略及来源计算 pending_restart，等价为 false”*。
- **核验结论**：摒弃了新增非必要哈希机制的过度设计，采用服务端规范化对象与来源比对，不仅覆盖了“保存回原状无待重启”的体验，还正确锁定了“内容相同但来源（如 env 变 DB）变更必须重启生效”的控制语义。

#### 3. [P2-1] TW 缺候选槽位登记离线与 Task 4.3 整体未完成约束
- **状态**：**已关闭 (CLOSED)**
- **文本证据**：
  - `tasks.md:32-34 (Task 4.3)`：更新为 *“TW 等地区无候选时登记该区领取离线，已覆盖地区可先验，缺项仍使本项整体保持未完成，不跨区补齐或写入对话活跃列表”*。
- **核验结论**：严格遵循局部失败隔离与不隐式补齐原则；允许可用大区先行验证，同时锁死该任务在未全量覆盖前不得虚报完成。

#### 4. [P2-2] 断路器半开（Half-Open）探测取消时的中性释放与许可重获
- **状态**：**已关闭 (CLOSED)**
- **文本证据**：
  - `tasks.md:14-15 (Task 2.2)`：回归测试范围明确增加 *“半开取消中性释放且下一请求可重新获得许可的回归”*。
  - 结合基线 `specs/regional-captcha-waterfall/spec.md:55`（*“cancellation releases the permit neutrally”*），执行与规范口径一致。
- **核验结论**：消除了半开探测遭遇异步取消或中断时导致 breaker 状态悬挂的隐患。

---

### 二、 增量修改扫描与新问题审查

本次对 `specs/purpose-egress-routing/spec.md`（修改 3 处）与 `tasks.md`（修改 4 处）的审查确认：
1. **边界契约一致**：增量改动严格限制在明确既有规范边界，未增加新的配置项、依赖或架构分层。
2. **状态逻辑完备**：“策略内容相同但来源不同仍需重启”的逻辑严密，符合“启动时加载一次生效快照”与“数据库优先覆盖环境默认”的基础架构假定。
3. **新问题扫描结果**：**无新增 P0、P1 或 P2 缺陷**。

---

### 三、 未验证边界声明

依据方案设计与输入约束，以下三项边界必须由实施阶段在真实环境逐项核销，不在文本审查中假定通过：
1. **公网与代理端口物理连通**：WARP `40000` 与 VPS 直连公网普通/SSE 对话质量留待部署后验证。
2. **TW 槽位可用性**：注册表是否存在有效且同代的 TW 候选槽位留待 Task 4.3 现场检索确认。
3. **活动实际到账**：code 1003 仅核销领取链路与出网身份，真实活动资格与 code 0 到账保持独立待办。

---

> **审查范围声明**：本次复审严格限定于前后两条提示词中提供的方案与规范文本，全程无源码读取、无环境工具调用、无外部检索与运行验收，结论仅对方案文本的完备性与契约一致性负责。方案已闭环，可以进入实施阶段。


## 本地原始证据

忽略目录 `data/diagnostics/split-egress-admin-review-20261017/` 保留首审/复审 stream-json、result JSON、输入提示词、8份文件哈希和运行日志；不入 Git。原有15份文档改动哈希保持不变。本轮没有实现页面、API或分流，也没有触发真实领取或重启服务。
