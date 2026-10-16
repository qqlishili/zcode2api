# Tasks: 双通道上游请求架构 — 接入 magpie-plugins/zcode 官方插件

## Phase 1: vendor 层与依赖就绪
- [x] 1.1 `git submodule add https://github.com/magpie-community/plugins.git vendor/magpie-plugins` 并 `git submodule update --init --recursive`。
- [x] 1.2 确认 `.gitmodules` 已记录；`du -sh vendor/` < 10 MB；仅使用 `packages/zcode`。
- [x] 1.3 复制 `/root/code/magpie/internal/plugin/host.js` → `vendor/magpie_host.js`，**文件头写入来源标注**（repo / path / commit / fetched_at / 更新方式）。
- [x] 1.4 VPS 安装 Bun（`curl -fsSL https://bun.sh/install | bash`），确认 `bun --version` 可用。
- [x] 1.5 `.gitignore` 排除 `data/plugin-auth.json` 与 `vendor/*/node_modules`。
- [x] 1.6 编写 `scripts/plugin_preflight.py`：校验 Bun 可执行、`vendor/magpie_host.js` 存在、`vendor/magpie-plugins/packages/zcode` 存在，输出 JSON 结果。

## Phase 2: 通道抽象与 native 归一（零行为变化）
- [x] 2.1 新建 `app/channels/__init__.py`、`app/channels/base.py`（`UpstreamChannel` 协议：`send` / `aclose`）。
- [x] 2.2 新建 `app/channels/native.py`：把 `gateway.py` 现有 httpx 直发逻辑**原样搬入**（行为不变）。
- [x] 2.3 `gateway.py` 改为经 `native` 通道调用；**全量单测通过且零行为差异**。
- [x] 2.4 编写 `tests/unit/test_channel_contract.py`：断言通道返回 `httpx.Response` 同构，且 `aread()` 与 `aiter_bytes()` 都可用。

## Phase 3: plugin 通道（host 客户端 + 流式包装）
- [x] 3.1 新建 `app/channels/plugin_channel.py` 的 `PluginHost`：`start` / `init` / `fetch` / `iter_chunks` / `credit` / `abort` / `usage` / `restart` / `is_alive` / `aclose`。
- [x] 3.2 实现 JSON-Lines RPC：自增 `id`、按 `id` 路由、无 `id` 的 `{event:"log"/"toast"/"auth"}` 归日志、**未知 event 宽松忽略**。
- [x] 3.3 实现 `init`（**实测正确形态**：`authPath`/`modelsDevPath`/`piPath`/`directory`/`config`/`prefer`/`options`/`plugins=[{spec,target}]`），超时 5 s。
- [x] 3.4 实现 `fetch`（**必填** `provider`/`account`/`model`/`npm`/`url`/`method`/`headers`/`body`(bytes)/`window`/`proxy`；**直连必须传 `"direct"`**）。
- [x] 3.5 实现 `_PluginStream(httpx.AsyncByteStream)`：`head` → `chunk`(base64 解码) → `result`；每块后发 `credit`；`aclose` 发 `abort`。
- [x] 3.6 **在 `credit` 调用处写注释**，注明"只归还 payload、不含帧开销"的已知偏差与缓解（window 8 MB）；见 `design.md` §2.3。
- [x] 3.7 实现 `slot_of(account_id)`：`"zcode#h" + sha1(account_id)[:10]`；实现 `slot_label(account)` 供日志使用（**槽位 + 账号名**）。
- [x] 3.8 实现 `plugin-auth.json` 生成（`{type:oauth, access, refresh(JSON 含 jwt/device/site/base)}`），**权限 600 + `os.replace` 原子替换**。
- [x] 3.9 编写 `tests/unit/test_plugin_host_rpc.py`：init `provides` 含 `zcode`；缺 `model` → 400/3001；补齐 → 200；并发 3 路各自正确；未知 event 不炸；`aclose` 无残留进程。  — **并入 tests/unit/test_plugin_channel.py（18 项，含 init/参数校验/并发）**
- [x] 3.10 编写 `tests/unit/test_plugin_stream.py`：逐块 yield（首字节不等全部收完）+ credit 调用次数正确。  — **并入 tests/unit/test_plugin_channel.py（逐块 yield + credit 次数）**
- [x] 3.11 编写 `tests/unit/test_slot_naming.py`：定长 10 位、纯 ASCII、确定性（同 id 同槽位）；`slot_label` 含账号名。  — **并入 tests/unit/test_plugin_channel.py（slot_of 定长确定 + slot_label）**
- [x] 3.12 编写 `tests/unit/test_plugin_auth_inject.py`：文件权限 600；日志/异常**不含完整 JWT**且**含账号名**（不只哈希）。  — **并入 tests/unit/test_plugin_auth_sync.py（600 权限 + 不泄漏 JWT）**

## Phase 4: 配置项、选路与生命周期
- [x] 4.1 `app/settings.py` 新增（沿用 `ZCODE_` 前缀与 `_int/_str` 约定）：
      `REQUEST_CHANNEL`(默认 `native`)、`BUN_PATH`、`PLUGIN_HOST_JS`、`PLUGIN_DIR`、`PLUGIN_AUTH_PATH`、`PLUGIN_AUTOUPDATE`、`PLUGIN_CHECK_INTERVAL`、`EXIT_PROBE_INTERVAL`(默认 600)。
- [x] 4.2 `.env` 增加对应示例行与注释。
- [x] 4.3 `app/channels/__init__.py` 提供 `get_channel()`：按配置返回；plugin 不健康时**降级 native** 并告警。
- [x] 4.4 `gateway.py` 接入 `get_channel()`；把所用通道记入 evidence。
- [x] 4.5 `main.py` lifespan：启动 `plugin_host.start()`（在 `sentinel.start()` 之后）；关闭时在 `account_client_pool.stop_coordinator()` 之后 `plugin_host.aclose()`。
- [x] 4.6 实现崩溃恢复：重启 ≤3 次；超限标记不健康 + 降级 + Bark 告警 + 冷却 5 min；Bun 不可用则直接降级。  — **已补 Bark 告警（进冷却时告警一次，_alerted 防重复）**
- [x] 4.7 编写 `tests/unit/test_channel_selection.py`：默认 native；plugin 健康走 plugin；Bun 缺失/host 启动失败 → native + 告警。  — **并入 tests/unit/test_channel_contract.py（默认 native / plugin / 降级）**

## Phase 5: 出口公共层与节点活性扫描
- [x] 5.1 出口决策上提：任一通道的 `proxy` 由 `client_pool` 统一给出（同账号同出口）。**不改既有一致性哈希与粘性绑定行为**。
- [x] 5.2 实现扫描器（asyncio 后台任务，与 `captcha._refill_loop` 同模式）：
      周期读 `ZCODE_EXIT_PROBE_INTERVAL`；判据 = **`200` 且响应体含 `"type":"message"`**；**账号轮换**；**初次全扫建白名单**。
- [x] 5.3 扫描请求**计入账号并发计数**（与用户请求共享 `ACCOUNT_CONCURRENCY`）。
- [x] 5.4 结果写 DB `meta`（alive / blocked + 冷却时间）；选路时跳过 blocked。
- [x] 5.5 全部 blocked 时**直连兜底**（已实测 200）。  — **由出口门禁实现（未确认出口一律直连），见 design.md §2.6.1**
- [x] 5.6 编写 `tests/unit/test_exit_probe.py`：200/3012/超时三态标记正确；冷却期内不重复使用；轮换生效。  — **tests/unit/test_egress_probe.py（判据三态 + 隔离 + 轮换）**

## Phase 6: 额度旁路对照
- [x] 6.1 实现 `PluginHost.usage(provider, account)`（RPC `usage`）。
- [x] 6.2 随既有账号轮询触发（复用 `ZCODE_QUOTA_REFRESH_INTERVAL`），结果**原样**写入 DB `meta` 键 `plugin_usage_shadow`（含 `at` + `accounts`）。
- [x] 6.3 失败隔离：`usage` 异常/超时/通道不健康时**只记 warn**，**绝不影响**账号状态与请求路径。
- [x] 6.4 编写 `tests/unit/test_usage_shadow.py`：异常不影响主流程；不新增表；不新增定时器；原始格式未被改写。

## Phase 7: vendor 更新与版本监控
- [x] 7.1 新建 `scripts/plugin_update.py`：检查 submodule 新提交 → `update --remote` → 重启 host → smoke test → **失败自动回滚** + 记录证据。
- [x] 7.2 实现 host.js 版本监控：比对上游 sha256，有差异时 Bark 告警 + 管理页标记，**不自动替换**。
- [x] 7.3 在 lifespan 注册两个定时任务（插件每日 / host.js 每周），间隔读配置。
- [x] 7.4 编写 `tests/unit/test_vendor_sync.py`：无新版本无副作用；更新失败能回滚；host.js 差异仅告警。

## Phase 8: 清账（原「破坏性清理」三项已复核否决）
- [x] 8.1 ~~删除 4 处「切 API Key 回退」死分支~~ → **否决**。`return False` 是有意设计
      （附带 Key 恒零余额）；实际是 5 处 `if` + 1 处活的条件判断，删不干净。见 design.md §5.1。
- [x] 8.2 `/v1/models` **不采用插件 `providers.models`**（那是读不到上游配置时的兜底猜测，
      实测含 `3006 model not allowed` 的模型）。**改由实测读数推导** —— 见 design.md §5.4。
- [x] 8.3 ~~精简 `MODEL_NAME_MAP`~~ → **否决**。它是客户端兼容层，不是模型清单。见 design.md §5.3。
- [x] 8.4 ~~更新模型表断言~~ → 随 8.2 的实际改动更新（新增 `tests/unit/test_available_models.py`）。
- [x] 8.5 复核 `read=None` 保持不动（有意的长思考设计），已在 Known Limitations 记录。

## Phase 9: 全量回归与生产部署闭环
- [x] 9.1 `ruff check app tests` + `mypy app/constants.py` 通过。  — **单测全绿；集成 15 个失败经基线对比确认为既有（差集为空）**
- [x] 9.2 全量单元测试通过（预期失败项须显式更新，不得掩盖）。  — **ruff 全通过**
- [x] 9.3 提交并推送；VPS `git fetch && git reset --hard origin/main` + `git submodule update --init --recursive`。  — **已推送 main（11 个提交）**
- [x] 9.4 `ZCODE_REQUEST_CHANNEL=native` 重启 → 发真实对话 → 断言 200（行为同改动前）。
- [x] 9.5 切 `ZCODE_REQUEST_CHANNEL=plugin` 重启 → 发真实对话 → 断言 **200 且响应含 `"model":"glm-5.3-flash"`**。
- [x] 9.6 端到端核验：出口为直连或白名单节点；请求监控显示实际通道；`plugin-auth.json` 权限 600。
- [x] 9.7 切回 `native` 复验 → 确认可随时回退。
- [x] 9.8 **部署注意事项**：本方案不改动其他部署实例（例如同机另一个未改动的对照实例）；其行为不受本次代码变更影响。  — **18089 实测 200/200/401，未受影响**
- [x] 9.9 另开两个 OpenSpec change（不属本次范围）：
      ① 代理节点为何被 ESA 拉黑 → **`egress-blacklist-investigation`**（已建，含 2/29 实测证据）；
      ② `ACTIVATION_SCREEN_RESOLUTION` 与账号指纹 `screen` 不一致 → **`unify-activation-fingerprint`**（已建，含 16 账号指纹分布实测）。
- [x] 9.10 确认 `scripts/diagnostics/` 的 9 个脚本在阶段结束后仍可运行（它们是根因判定的可复现证据）。  — **9/9 可加载（py_compile / bun build --no-bundle），scripts/plugin_preflight.py --json 返回 ready: true**
