# ADR 0001: 以官方插件作为上游请求形态的唯一来源

- **状态**：已接受
- **日期**：2026-10-15
- **相关**：`openspec/changes/dual-channel-plugin-upstream/`

---

## 背景

`zcode2api` 作为多账号 ZCode 中转网关，向 `zcode.z.ai` 的 Plan 接口发起对话请求。
2026-10-14 起，所有账号的 `/api/v1/zcode-plan/anthropic/v1/messages` 请求被上游返回：

```
HTTP 405  {"code":3012,"msg":"request has been blocked due to unusual activity."}
```

排除实验覆盖：出口 IP（3 个未使用节点 + 直连）、模型名、请求频次、账号新旧、
请求头（完整复刻第三方实现）、请求体（补 system prompt / metadata）、
HTTP 客户端（Python httpx / Node fetch）、环境差异（未改动版实例）。

**全部 3012。**

官方插件源码注释给出了判据（`magpie-plugins/packages/zcode/index.mjs:1310`）：

> `zcode.z.ai` serves the Start Plan only to what looks like ZCode's own request,
> and turns others away with 405 "request has been blocked due to unusual activity" (code 3012).
> A Start Plan request goes as the plugin the plan serves sends it: its headers (`startHeaders`)
> and its body (`dress`).

**判据是整份请求的形态** —— 20 个特征头 + 3 段官方 system prompt（10526 字节）+
`<system-reminder>` 首轮 + `metadata.user_id`。

实测确认：**直连 + 官方 `dress` → HTTP 200**；同一账号**直连 + 无 dress → 3012**。
（复现：`scripts/diagnostics/probe_dress_effect.mjs`）

---

## 决策

**引入 `magpie-community/plugins` 的 `packages/zcode` 作为上游请求形态的唯一来源**，
通过其官方插件宿主 `magpie/internal/plugin/host.js`（复制单文件）以 JSON-Lines RPC 调用。

具体形态：
- `vendor/magpie-plugins/` —— **git submodule**（3.2 MB），**只用 `packages/zcode`**
- `vendor/magpie_host.js` —— **复制单文件**（89 KB）+ 来源 commit 标注
- `app/channels/plugin_channel.py` —— Python 侧 host 客户端（常驻单进程 + 槽位 `zcode#h<sha1[:10]>`）
- 保留 `native` 通道（现有 httpx 手写逻辑）作为可对照、可回退的第二通道

---

## 为什么不自己维护 `dress`

**这是本决策的核心。**

| 方案 | 可控性 | 维护成本 | 滞后风险 |
|---|---|---|---|
| **自维护 dress** | 高 | **每次上游变更都要跟** | **必然滞后**（上游先变，我们后知） |
| **调官方插件** | 中（受插件 API 约束） | **接近零**（`git pull` 即得） | **低**（社区持续跟进） |

`packages/zcode` 是 magpie 社区持续演进的包（`prompt.mjs` 内含官方 prompt 全文，`dress`/`startHeaders`
对应上游真实行为）。**自维护等于把上游的脏活背在自己身上且永远滞后** —— 这正是要避免的。

---

## 为什么不引入 magpie（Go 二进制）

`magpie` 全仓 **257 MB**（`site/` 60 MB 站点资源、`.git` 148 MB、`internal/` 46 MB），
而本方案**只需要其中一个文件** `internal/plugin/host.js`（89 KB）。

实测证明（`scripts/diagnostics/probe_host_rpc.mjs`）：**`host.js` 是独立的插件宿主**，
直接以 `bun host.js` 启动即可通过 stdin/stdout JSON-Lines 完成 `init` + `fetch` + `usage`。
**不需要 magpie 的 Go 程序。**

---

## 为什么不直接调 npm 包的 `dress` 函数

`magpie-plugins/packages/zcode/index.mjs` 的 `dress` 虽然在 `_internal` 中导出，但：

1. **`_internal` 是"给测试用的"** —— 官方可随时改动
2. **真正发请求的是宿主**（`host.js` 的 `fetch` 方法），插件只**声明**如何发（`auth.loader.fetch`）
3. 只调 `dress` 而不走宿主，等于**只抄了一半** —— `startHeaders`、流式、`credit` 流控都要自己实现

**走宿主是"用官方完整链路"，而不是"抄官方的一个函数"。**

---

## 被否决的替代方案

| 方案 | 否决理由 |
|---|---|
| **继续自维护请求形态** | 已实测失败（全 3012），且要永久跟上游 |
| **引入 magpie Go 二进制** | 为 1 个文件引入 257 MB；且它自带网关/账号逻辑与本项目重复 |
| **移植 `dress` 到 Python** | 复制官方 prompt 与逻辑 → **失去"跟随更新"这个唯一收益**，退化为自维护 |
| **让 magpie 做完整网关**（zcode2api 转发给它） | magpie 的账号轮转能力弱于 zcode2api（本项目有 Start Plan + 活动额度的精细选号） |

---

## 后果

### 正面
- 上游请求形态**跟随官方**，上游变更时 `git submodule update` 即得
- zcode2api 保留全部核心能力（账号池 / 出口池 / captcha / 额度调度）
- 两条通道并存，可对照、可回退

### 负面 / 代价
- **新增 Bun 运行时依赖**（VPS 已装 1.4.3）
- **新增跨语言边界**（Python ↔ Bun，JSON-Lines RPC）
- **受插件 API 约束** —— 缓解：只依赖 `init` / `fetch` / `usage` 三个公开 RPC 方法
- **vendor 层需版本监控** —— 缓解：插件自动更新 + host.js 人工确认

### 已实测的架构参数（各有可复现脚本）

| 指标 | 值 | 脚本 |
|---|---|---|
| 16 槽位 `init` | 70 ms | `probe_init_cost.mjs` |
| host 进程 RSS | 29–54 MB | `probe_init_cost.mjs` |
| 上游单账号并发上限 | **3**（超出 429 `code:3008`） | `probe_concurrency_limit.mjs` |
| 并发 32 打 host | 全并行（RPC 非瓶颈） | `probe_concurrency_limit.mjs` |
| 改 auth.json 不重启 | **不生效** | `probe_auth_reload.mjs` |
| 槽位名可用形态 | 中文 / UUID / 长英文 / 纯数字 | `probe_slot_naming.mjs` |

---

## 已知技术债（随决策一并记录）

1. **`credit` 只归还 payload、不含帧开销** —— 官方窗口计费含每帧固定开销。
   本实现按 `len(chunk)` 回补，长响应可能因窗口渐耗而停滞。
   缓解：`window` 设 8 MB + 代码注释标注；实测停滞则改为按帧精确归还。
2. **槽位是哈希** —— 运维无法从槽位名定位账号。缓解：**日志必须同时带账号名**。

---

## 保密与安全

- `data/plugin-auth.json` 含全部账号的 JWT 明文 → **权限 600**，定位为从 `accounts.db` **派生的缓存**
- 日志中**禁止**出现完整 JWT（仅允许前 8 字符）
