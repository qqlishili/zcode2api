# Proposal: 双通道上游请求架构 — 接入 magpie-plugins/zcode 官方插件

## Why

### 现象：手写请求被上游 ESA 全面拦截（HTTP 405 / code 3012）

2026-10-14 实测，zcode2api 所有账号的 `/messages` 请求被上游拦截：

```
HTTP 405  {"code":3012,"msg":"request has been blocked due to unusual activity."}
```

**排除实验（全部实测，无一例外）**：

| 假设 | 实验 | 结果 |
|---|---|---|
| 出口 IP 被拉黑 | 3 个未使用节点 + 直连 VPS | 全 3012 |
| 模型名问题 | 全部 `GLM-5.3-Flash` | 全 3012 |
| 频次问题 | 12~20s 间隔、单次 | 全 3012 |
| 账号被标记 | 全新账号 `use_count=0` | 3012 |
| 请求头缺失 | 完整复刻第三方实现的头 | 3012 |
| 请求体缺失 | 补 system prompt / metadata | 3012 |
| HTTP 客户端 | Python httpx / Node fetch 双测 | 全 3012 |
| 环境差异 | 未改动版实例（:18089）同样测试 | 3012 |

### 根因：上游只服务「形态与 ZCode 客户端一致」的请求

官方插件源码 `magpie-plugins/packages/zcode/index.mjs:1310-1313`：

> `zcode.z.ai` serves the Start Plan only to what looks like ZCode's own request,
> and turns others away with 405 "request has been blocked due to unusual activity" (code 3012).
> A Start Plan request goes as the plugin the plan serves sends it: its headers (`startHeaders`)
> and its body (`dress`).

**判据是整份请求的形态**，不是单一特征 —— 20 个特征头 + 3 段官方 system prompt（10526 字节）+
`<system-reminder>` 首轮 + `metadata.user_id`。这解释了为何逐项复刻全部失败。

### 解决路径（已实测打通）

| 组合 | 结果 |
|---|---|
| 直连 + 官方 `dress` | ✅ **HTTP 200** |
| 代理节点 + 官方 `dress` | ❌ 3012（**该节点已被 ESA 拉黑**） |
| 直连 + 无 dress | ❌ 3012 |

**结论**：**dress 是必需的**；代理节点是否可用是**独立的出口问题**。

### 关键洞察：官方插件宿主可独立运行（已实测）

`magpie/internal/plugin/host.js`（89 KB，Node/Bun 脚本）是**独立插件宿主**，走 stdin/stdout JSON-Lines 协议：

```
magpie → host:  {id, method, params}
host → magpie:  {id, result} | {id, error}
fetch 流式:     {event:"head"} → {event:"chunk"} → {result}
```

**实测结论**（`scripts/diagnostics/probe_host_rpc.mjs`）：
- `init` 返回 `{"plugins":[{"spec":"@magpie-community/opencode-zcode-auth","provides":["zcode"]}]}`
- `fetch` 直连 **HTTP 200**

**→ 不需要 magpie 的 Go 二进制**；只需 `host.js` 一个文件 + `magpie-plugins` 一个仓库。

### 为什么不自己继续维护 dress

`packages/zcode` 是社区持续演进的包。ESA 规则一变，官方跟着改；自维护等于把上游的脏活背在身上且永远滞后。
**接入即获得「跟随上游更新 + 零维护」的收益。**

## What Changes

### 1. 双通道（重构 `gateway.py` 的请求出口）

- **通道 A `native`**：现有 httpx 手写逻辑**保留**，作为可优化、可对照的通道。
- **通道 B `plugin`**（新增）：经 `host.js` + `magpie-plugins/packages/zcode` 发请求。
- **配置**：`ZCODE_REQUEST_CHANNEL`（`native` | `plugin`，默认 `native`）。
- **约束**：单请求只走一条通道（不并行发两条）；配置只取一个值（不做主备）。

### 2. vendor 层

- **`vendor/magpie-plugins/`**：git submodule 引入 `magpie-community/plugins`（3.2 MB），**只用 `packages/zcode`**。
- **`vendor/magpie_host.js`**：复制 magpie 仓库的 `internal/plugin/host.js`（89 KB），文件头注明来源 commit。
  - **理由**：magpie 全仓 257 MB（`site/` 60 MB、`.git` 148 MB），为一个文件引入整仓不成比例；且 host.js 属协议规格，**不可独立更新，需人工确认**。

### 3. 请求形态

- **model 名完整透传**（含 `-Trial` 后缀 → 上游按活动额度结算）。
- 失败统一包装为 `httpx.Response`（host 进程异常 → 502），**gateway 的错误分支零改动**。
- 流式用 **`httpx.AsyncByteStream`（httpx 官方 API）** + 官方 `credit` 回补
  （`credit` 只归还 payload、不含帧开销的已知偏差见 design.md §2.3）。

### 4. 账号与凭证

- 槽位名 = `zcode#h` + `sha1(account.id)[:10]`（**纯函数，不存表**）。
  槽位是哈希，**日志必须同时带账号名**（可读性补偿，见 design.md §2.5）。
- `data/plugin-auth.json` 是**从 `accounts.db` 派生的缓存**（非权威源），**全量重写 + 重启 host 进程**（70–84 ms）。
- `device` 用 `account.fingerprint.device_mid`，**两通道同设备**（避免"同账号多设备活跃"风控特征）。

### 5. 出口（通道无关的公共层）

- **出口门禁**：候选由 `client_pool` 给出（账号绑定，隔离时同区环形避让），
  **是否采用由门禁决定** —— `EGRESS_MODE=proxy` 时**只走探针已确认可服务的端口**，
  未确认则**直连兜底**；`direct` 时强制直连（诊断用）。**不更换代理池**。
- **出口探针**：asyncio 后台任务（与 `captcha` 同模式），默认 **600 s**（可配），
  **只探账号实际使用的大区**（实测 32 个端口，全局池 127 个）；**账号轮换**；
  结果写回 `client_pool` 的既有三态断路器 + DB meta。
- **判据**：`200` / `402` / `403` 判可达（前者受理、后两者障碍在账号侧）；
  **`405` 或业务码 `3012` 判不可达**（该出口已被上游拉黑）。
- 探测请求**占用账号并发计数**（与用户请求同一上限）。

### 6. 额度（旁路对照，零影响）

- 周期调用插件的 `usage` 方法，结果落 **DB `meta` 表**。
- **不参与任何决策**：现有 `quota.py` 判定逻辑完全不变。

### 7. 运维

- **插件**：每日自动检查 + `git submodule update --remote` + 重启 host。
- **host.js**：每周检查上游 sha256，**有差异仅告警（Bark），不自动替换**。
- **Bun 崩溃**：重启 ≤3 次；超限降级 `native` + 告警，冷却 5 分钟。
- **管理界面**：设置页显示当前通道配置值；请求监控页显示每次请求实际走的通道。

## Capabilities

### New Capabilities
- `dual-channel-upstream`：两条并列的上游请求通道，可配置切换、可回退，共用账号池与出口层。
- `plugin-channel-runtime`：host.js 常驻宿主的生命周期管理（init / fetch / stream / restart / 就绪探测）。
- `vendor-sync`：vendor 层版本监控与更新（插件自动、host.js 人工确认）。
- `plugin-usage-shadow`：插件额度读数的旁路对照（零决策影响）。

### Modified Capabilities
- `gateway`：发上游请求前按配置选路（`get_channel()`）；`/v1/models` 改为由实测读数推导。
  ~~清理 4 处不可达的「API Key 回退」死分支~~ → **已复核否决**（`return False` 是有意设计，见 design.md §5.1）。
- `models-endpoint`：`/v1/models` 改为由**实测读数推导**（所有启用账号的 `usage` 模型并集，剥离 `-Trial`，归一到官方写法；无读数时回退硬编码）。**不采用插件 `providers.models`** —— 那是读不到上游配置时的兜底猜测，实测含 `3006 model not allowed` 的模型。
- `egress`：出口选择上提为公共层；新增节点活性扫描与白名单。

## Impact

**新增**：`app/channels/`（`base.py` / `native.py` / `plugin_channel.py` / `plugin_auth.py`）、`app/egress_probe.py`（出口探针）、`app/usage_shadow.py`（额度旁路对照）、`vendor/`、`data/plugin-auth.json`、`scripts/diagnostics/`（诊断工具）

**修改**：`app/settings.py`（新配置项）、`app/routes/gateway.py`（选路 + 死分支清理）、`app/main.py`（lifespan 挂载）、`app/client_pool.py`（出口公共化 + 扫描）

**依赖**：新增 **Bun 运行时**（VPS 已装 1.4.3）；`magpie-plugins` 作为 submodule（3.2 MB）

**数据**：无 Schema 变更（`usage` 对照写 `meta` 表）

**回退**：`ZCODE_REQUEST_CHANNEL=native` + 重启即完全回到现状

**不涉及**：不引入 magpie Go 二进制；不使用 `magpie-plugins` 中除 `packages/zcode` 外的其他包；不改动其他仓库的部署实例

## Known Limitations（记录，不修）

1. **`read=None`**（`gateway.py:48` / `client_pool.py:425`）：有意为长思考流式设计，代价是"上游建连后不吐数据"时永不超时。属既有设计，本次不动。
2. **`credit` 少归还帧开销**：本实现只按 payload 字节回补，长响应可能因窗口渐耗而停滞。缓解见 design.md §2.3。
3. **`ACTIVATION_SCREEN_RESOLUTION = "2560x1440"`** 与账号指纹 `screen: "1366x768"` 不一致：需独立验证是否有意，另开 change。
4. **代理节点为何被 ESA 拉黑**：另开 change 调查（与本方案正交）。

## 实测数据（架构参数）

| 指标 | 实测值 |
|---|---|
| 单槽位 `init` | 84 ms |
| **16 槽位 `init`** | **70 ms** |
| host 进程 RSS | 29–54 MB |
| **上游单账号并发上限** | **3**（并发 4 起出现 429） |
| 429 业务码 | `3008 user concurrency limit exceeded`（无 `Retry-After`） |
| 槽位名合法形态 | 中文 / UUID / 长英文 / 纯数字 **全部可用** |
| 改 `plugin-auth.json` 不重启 | **不生效**（必须重启进程） |
| 并发 32 打 host | 全并行（墙钟 2101 ms ≈ 单请求延迟，RPC 非瓶颈） |
| `-Trial` 透传 | ✅ 可走活动额度，响应 `model` 字段被上游归一 |

> 上表每一项都可由 `scripts/diagnostics/` 下的对应脚本复现（见该目录 README 的结论索引）。
