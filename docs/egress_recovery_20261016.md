# HK / JP 出口恢复核验（2026-10-16）

本轮由用户授权恢复线上出口并验证真实请求、SSE。节点对照于 15:59–16:16 CST 完成；用户随后批准临时直连及 Git 发布，约 17:00 CST 公网普通请求与 SSE 验收通过。**HK / JP 节点恢复仍未完成**，没有把直连成功或 TW 成功记作同区恢复。

## 直连复验与代理故障追加定位（17:13–17:31 CST）

Windows 外部客户端再次直接请求公网网关 `http://107.174.244.167:18088/v1/chat/completions`，模型 `GLM-5.3-Flash`：非流式 HTTP 200、文本 `OK`、`finish_reason=stop`，耗时 11.0 秒；SSE HTTP 200、文本 `OK`、正常 stop 和 `[DONE]`、流内错误 0，耗时 5.266 秒。原验收报告保留，复验独立保存为 `external-gateway-direct-recheck-20261016T091304Z.json`。

新增对照仍在隔离进程中显式使用固定代理端口，没有修改生产 direct 模式或绕过生产准入去承接业务：

| 检查 | 实测结果与含义 |
|---|---|
| 相同最终请求，不同代理协议/出口 | TW `21200`（HY2）、TW `21202`（VLESS）、US `21506`（VLESS）均 200；HK `21100`、JP `21300`（VLESS Reality）、JP `21306`（VLESS WS）均 405/3012。不是“加上任意代理必然失败”，也未找到 VLESS 协议通用故障 |
| 固定上游 DNS 目标 | HK `21100`、JP `21306` 固定连接 VPS 当时解析的 `47.246.23.166`，保留 `zcode.z.ai` 的 Host/SNI，仍 405/3012；JP `21300` 的固定目标对照 TLS 连接失败，不冒充业务拒绝证据 |
| HTTP 版本 | curl 分别传 `--http1.1`、`--http2`，但实际均协商为 HTTP/1.1，不能据此声称已完成真正 HTTP/2 对照；当前失败已可在 HTTP/1.1 复现 |
| 官方插件最小输入 | 绕开 `build_request` 的本地身份头和正文转换，只给官方插件标准 Anthropic 请求；不附验证码、附旧 `fake_param` 两种情况，直连/TW 均 200、文本 `OK`，HK `21100`、JP `21300` 均 405/3012。重复包装不是这组失败的充分解释 |
| 同出口真实验证码 | HK `21100`、JP `21300` 经对应代理取得通过现有严格校验的 280 字符验证码参数，再以现有网关正文及插件最小正文分别发请求，均 405/3012。该结果排除了这两个端口仅因旧探测使用假验证码而误判的解释 |
| HK 扩展与直连真实验证码对照 | HK `21101`、`21102`、`21104` 分别以新解出的同出口验证码请求，现有正文/最小插件正文均 405/3012；加上 `21100`，4 个能连接的 HK 出口均复现。直连两种正文配各自新解验证码均 200、文本 `OK`、`end_turn`。HK `21103` 仅有此前连接失败证据 |
| JP 追加端口的限制 | JP `21306` 不附验证码或附旧假参数均 405/3012；两次真实求解均为 `param_too_short`，未发送真实验证码业务请求。不能将该端口写成真实验证码业务失败 |

此前全量 29 节点扫描使用 `fake_param`，其结果只证明该探测条件下没有成功节点，不能宣称已逐节点用真实验证码验收。真实验证码的补测范围以本节明确列出的端口为准。首次隔离补测的 direct 验证码行因加载了 `.env` 的全局代理池、被一致性断言中止，不能作直连失败证据；随后在诊断进程清空 `ZCODE_PROXIES`，并核对实际 operation.proxy 后重测。TW 的真实验证码求解两次耗尽尝试，报 `param_too_short`、76 字符；未修改校验规则，也未将其充作有效验证码。TW 最小业务请求成功另有独立证据。

新增证据：本地 `data/diagnostics/handoff-20261016/egress-network-matrix.json`、`egress-captcha-shape-initial.json`、`egress-captcha-shape-expanded.json`；VPS `/root/egress-network-20261016-gx39n07r/report.json`、`/root/egress-captcha-shape-20261016-wzrj7jqb/report.json`、`/root/egress-captcha-shape-20261016-5kehe1ik/report.json`。报告不含 JWT、验证码值或完整外发请求头；各诊断进程已退出，临时 auth 副本及公网验收密钥文件已清理，生产凭据未变。

当前判断仍是**与现有 HK / JP 出口相关的上游业务拒绝**；配置核对未发现节点身份或端口错配，但没有取得上游规则，不能升级为“永久封 IP”或“所有可能请求形态都不行”。后续恢复需以对应出口的真实非空响应及正常 SSE 为准；保持已验收的 plugin + direct 临时服务，HK / JP 恢复任务开放。

## 实测结论

当前故障集中于 HK / JP 出口相关的上游拒绝。官方插件、相同账号和模型请求经直连或 TW 可以成功，经现有 HK / JP 返回 `HTTP 405 / code 3012 / server ESA`；部分节点连接失败。证据支持出口相关风控拒绝，不能进一步断言上游规则只检查 IP 信誉。

| 对照 | 非流式真实响应 | SSE |
|---|---|---|
| active HK 账号 a1，VPS 直连 | 200，文本 `OK`，`end_turn` | 200，文本 `OK`，有 `message_start` / `message_stop` |
| active JP 账号 a2，VPS 直连 | 200，文本 `OK`，`end_turn` | 200，文本 `OK`，有 `message_start` / `message_stop` |
| active HK 账号 a3，VPS 直连 | 200，但 64 token 上限截断推理，文本为空；不算非空输出验收通过 | 200，文本 `OK`，有 `message_start` / `message_stop` |
| 同一 HK 账号，经 TW `21200` | 200，文本 `OK`，`end_turn` | 200，文本 `OK`，有 `message_start` / `message_stop` |
| 同一 HK 账号，经 HK `21100` | 405 / 3012 | 405 / 3012，未建立业务 SSE |
| 同一 JP 账号，经 JP `21300` | 405 / 3012 | 405 / 3012，未建立业务 SSE |
| Windows 外部客户端 → 公网网关，修正发布前 | 503，`unknown_unavailability` | 503，未建立 SSE |
| Windows 外部客户端 → 公网网关，`9ad47f7` 发布后 | 200，文本 `OK`，`finish_reason=stop` | 200，文本 `OK`，`finish_reason=stop`、`[DONE]`，流内错误 0 |

以上 a1–a3 仅为本报告别名，不能和其它诊断中的序号作账号关联。测试模型为 `GLM-5.3-Flash`；上游对照直接调用现有 `PluginHost.fetch` 并显式指定代理，不经过 `PluginChannel` 的未确认代理直连兜底。因此代理成功结果没有混入直连成功。公网请求另从 Windows 实际发送，未用 VPS 本机请求代替。

## 节点配置与来源核对

- 当前两份来源均重新读取成功，分别为 67 / 76 个节点，完整连接指纹没有新增；没有可补入的新 HK / JP 节点。
- 早期使用假验证码参数的全量扫描：5 个 HK、24 个 JP 节点用独立插件宿主连接逐个请求，HK 为 4 个 405/3012、1 个连接失败；JP 为 22 个 405/3012、2 个连接失败。29 个节点定义不是 29 个独立出口 IP；取得定位的结果存在出口复用。真实验证码补测见本文第一节。
- 对 HK / JP / TW 共 36 个节点，核对生命周期完整连接指纹、生产 provider 定义、mixed listener 的端口/绑定组，以及 Mihomo 控制器运行中组选择，未发现身份不符或串线。
- 独立 curl 对照使用相同账号、请求头和正文：直连返回 200；TW `21200` 的 HTTP CONNECT、SOCKS5h 均为 200；HK `21100`、JP `21300` 的两种入口均为 405/3012。全部 `curl_exit=0`、`ssl_verify_result=0`，不是本机代理认证失败或上游 TLS 证书校验失败。
- 捕获官方插件最终外发请求后，以 httpx、独立 Bun fetch 重放：TW 200，HK 405/3012。请求正文和插件传输的差异不足以解释这组失败；未发现本地节点定义或端口映射错误。

`3012` 不能单独证明“配置绝对正确”或“永久封禁”。上述配置、TLS、入口和出口对照共同支持当前判断；上游具体风控规则未取得。

## 与 VPS 上三个项目的核对

| 目录 | 检出版本与本轮检查 |
|---|---|
| `/root/code/ZCode` | `aac4755666d09fdcd70272fcf063c077a639015f`；读取 Host 的 `nodeApiNetwork.ts` 及 CLI 的 `proxy-fetch.ts`，核对按请求注入代理 transport 的实现 |
| `/root/code/magpie` | `22d7ef08fedbb9bb61876dec0aa396af8d8fd0ed`；`internal/plugin/host.js` 的代理上下文和 `fetchAs` 段与当前 vendor 副本一致 |
| `/root/code/magpie-plugins` | `f2f4ff4014d3abcb9851ff87af75d214786f5949`；`packages/zcode/index.mjs` 相对当前引入的 `6a3fe9b4…` 没有差异 |

真实调用使用项目当前引入的 `vendor/magpie_host.js` 和 `vendor/magpie-plugins/packages/zcode`。没有仅凭主仓库或插件版本号猜测故障，也没有为本次故障升级 vendor。未启动完整 ZCode 桌面客户端做 E2E。

## 证据边界与诊断纠正

最初隔离扫描在同一代理地址上切换 selector，并复用插件宿主，Bun 的已有连接仍使用切换前的出口，曾产生 TW 插件失败及 HK 插件成功的误导结果。这些跨节点宿主复用结果不作为对应节点的验收证据。最终全量插件扫描每个节点重建独立宿主；固定生产端口的非流式/SSE 对照、独立 curl/Bun/httpx 重放另作交叉验证。

本地原始结果位于忽略目录 `data/diagnostics/handoff-20261016/`：

- `egress-plugin-fresh-connections.json`：最终逐节点插件扫描。
- `egress-upstream-response-and-sse.json`：实际响应与 SSE 起止事件、文本检查。
- `egress-transport-exact-request.json`：最终外发请求的独立传输重放；其中复用宿主后的 HK 行不采信，独立 httpx / Bun 行有效。
- `egress-http-socks-comparison.json`：HTTP CONNECT / SOCKS5h 与 TLS 验证结果。
- `external-gateway-before.json`：Windows 外部客户端实际 503。
- `external-gateway-direct-after.json`：修正发布后，Windows 公网非空响应及正常 SSE 终态断言均通过；非流式 12.5 秒、SSE 6.5 秒，含客户端到网关及验证码耗时。

对应 VPS 原始报告保留在 `/root/egress-restore-20261016-akuu7qju/report.json`、`/root/egress-sse-20261016-ifsm79lp/report.json`、`/root/egress-wire-20261016-cfcwqk3_/report.json`、`/root/egress-tunnel-20261016-6y9pxird/report.json`。目录权限 0700，报告权限 0600。临时凭据和请求头文件在核验结束后清理，不进入 Git。

## 临时恢复结果与 HK / JP 剩余事项

用户确认没有其它节点。现有 HK / JP 没有找到可服务出口，不能通过放宽准入、改地区标签或把 3012 标健康来完成恢复。

用户随后明确同意“先直连”。已于约 16:22 CST 保全 `.env` 和 `app/client_pool.py`，将 `ZCODE_EGRESS_MODE=proxy` 改为 `direct` 并重启 `zcode2api`，`/meta` 200、新 PID 687842。但从 Windows 在服务就绪后复测，非流式和 SSE 仍为 503。配置位置正确，单改配置不足以修复当前版本。

原因已由源码、实际 503 和新增失败测试共同复现：`PluginChannel` 识别 direct，但 `AccountClientPool.get_healthy_regions`、`is_region_healthy` 仍以代理大区过滤账号，连接池也禁止已绑定账号直连。已完成最小修正，使显式 direct 配置贯穿调度、解析和客户端获取；proxy 模式的同区隔离及禁止静默降级规则保留。

修正涉及 `app/client_pool.py` 和 `tests/unit/test_egress_operations.py`。新增 3 项回归先失败；修正后相关 9 份测试共 71 项通过，最后一次修改后受影响操作测试 12 项复验通过。Windows Ruff 原生进程因 DLL 初始化失败退出（3221225794），改用 VPS 已有 Ruff 对候选源码和测试运行，检查通过。用户已明确授权 Git 提交、推送和部署，发布提交为 `9ad47f7b8331a647fdde385747452a04e958183a`；VPS 原目录已快进，源码/测试 SHA-256 与本地相符。

发布后公网实际请求均通过：非流式文本 `OK`；SSE 有文本 `OK`、正常 stop 和 `[DONE]`、流内错误 0。生产证据库对应成功记录为 `b79c49266cd9fb08`（非流式）和 `effbe7a28843afb6`（SSE），均 `status=200 / ok=1`。此结果只验收 `GLM-5.3-Flash` 的 `/v1/chat/completions`，未扩大为全部模型或全部 API 验收。

最终网关 PID 715879、Bun 子进程 715881，当前 PID 的日志确认官方插件宿主就绪、13 个凭证槽；看到的“读取循环结束/降级 native”警告属于重启前 PID 687842。Mihomo PID 2797546、WARP PID 2440689 均保持原值；三服务 active、NRestarts=0。16 个账号仍为 active 3、exhausted 10、disabled 3，active 账号 HK 2 / JP 1、risk_strikes 均为 0；这不是长期风控安全保证。

回退材料为 VPS `/root/egress-direct-recovery-20261016-0gck0fjw/`，包含权限 0600 的 `env.before`、`client_pool.py.before` 和哈希清单。`external-gateway-direct-config-only.json` 保留仅切配置后的真实 503，`direct-mode-red.xml`、`direct-mode-green.xml`、`direct-mode-final.xml` 保留失败与回归结果。

16:16 前的核对中，生产模式为 proxy，`zcode2api` / `mihomo` PID 为 186203 / 2797546、`NRestarts=0`；注册表 generation 为 `3e45cc67563daf227871bcbe05d7e556531590e341f02bb80b5c789129f97a29`，基础配置哈希与生命周期基线匹配。本轮仅切换出口模式并重启网关，未改变地区绑定、生命周期、准入或 Mihomo 基础配置。直连恢复即使成功，也使用 VPS 出口，**HK / JP 节点恢复仍保持未完成**。

## VPS 的 CF / WARP 出口与账号影响（16:55 CST 复核）

- `warp-svc` 运行、`warp-cli status` 为 Connected；实际配置是 `WarpProxy on port 40000`、MASQUE，仅监听 `127.0.0.1:40000`，不是系统全局隧道。
- `zcode2api` 进程没有 HTTP_PROXY / HTTPS_PROXY / ALL_PROXY 环境变量。Cloudflare HTTPS trace 实测 `ip=107.174.244.167`、`loc=US`、`warp=off`、`gateway=off`，ipify 独立确认同一个 IP。
- 用网关实际进程的环境运行相同 Bun 运行时，再次取得 `ip=107.174.244.167 / loc=US / warp=off`，没有用登录 shell 的代理设置替代应用环境。
- 当时 `zcode.z.ai` 解析为 `47.246.23.189`，实际路由经 `eth0`、源地址 `107.174.244.167`；没有 `/etc/hosts` 覆盖。已检查 nft/iptables 的地址转换规则，只有既有 Docker 入站 DNAT，未发现把本项目出站转入 WARP 的规则。
- 因此本次 direct 使用 VPS 原生公网出口，不自动经过现有 WARP 代理，也不因“为 CF 优化”而获得独立账号出口。未改 WARP、系统路由、DNS 或防火墙。

17:28 CST 再核对：WARP 为 PID 722852，启动时间 17:06:44，仍是 `WarpProxy on port 40000`、MASQUE、Connected/healthy；此前 PID 2440689 是旧快照，本轮诊断没有操作 WARP 服务。网关 PID 715879、Mihomo PID 2797546 仍 active、NRestarts=0；上游目标路由仍经 eth0、源地址 `107.174.244.167`。

影响：全局 direct 会让业务账号及使用该连接池的后台账号操作共享 VPS 公网 IP；原账号 assigned_region 和各自凭证/设备数据保留，但实际外呼不再按 HK / JP 分流。上游可以观察到相同来源 IP，结合令牌和行为关联账号；本次短时成功不能证明长期多账号同 IP 没有风控风险，也不能预测必然封号。此模式作为已获准的临时业务恢复，HK / JP 独立出口恢复继续开放。
