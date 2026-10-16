# HK / JP 出口恢复核验（2026-10-16）

**21:11 CST 新结论**：HK / JP 对话返回 3012，但同区领取 POST 可返回 1003（已领取），不能把对话拒绝外推为全部 billing 接口拒绝。WARP 普通对话及 SSE 通过；按用途分流尚未实施，线上继续 plugin + direct。真实新活动发额度仍未验收，详见文末对照。

本轮由用户授权恢复线上出口并验证真实请求、SSE。节点对照于 15:59–16:16 CST 完成；用户随后批准临时直连及 Git 发布，约 17:00 CST 公网普通请求与 SSE 验收通过。**HK / JP 节点恢复仍未完成**，没有把直连成功或 TW 成功记作同区恢复。

18:14–18:32 CST 又从 VPS 完成全部配置的独立插件复测，详见[142 个业务节点及 1 条信息项的完整清单](egress_node_inventory_20261016.md)：正常 4 个（普通请求与 SSE 均通过）、405/3012 拒绝 125 个、连续两次请求失败 13 个。赔钱机场正常 1 个，kcyun 导入源正常 3 个；后者按文件名推定对应快冲云，正式来源链接未保存。旧 active 的台湾3、美国 Hacienda Heights S11、埃及 Alexandria A1 本轮未通过，不能沿用旧“7 个可服务”的判断。此次最小插件请求不附验证码头，未完成逐节点真实验证码验收；生产准入和配置未变，前后直连对照通过。

18:50–18:52 CST 补查“VPS 是否真能连接节点”：旧扫描中 129 个已有 ZCode HTTPS 回包证据；对剩余 13 个和 4 个对照，另测 Cloudflare/ipify HTTPS 及入口 TCP。TW `21201` / `21206` 公共 HTTPS 成功，10 个其它失败节点入口 TCP 可达但代理 HTTPS 未成功，HK `21103` 的入口域名随后经系统及两家公共 DNS 确认为无法解析（NXDOMAIN）。两轮累计 131 个有完整代理通路成功证据、10 个只有 TCP 入口可达、1 个入口 DNS 失败；不升级为 131 个业务可服务。HK `21100`、JP `21300` 的公共请求明确显示 HK / JP 出口，证明它们的 3012 属于 ZCode 请求拒绝，不能解释为 VPS 连不上机场。详见完整清单末尾的连接能力补测。

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

## 活动领取与对话分流对照（19:17 及 21:07–21:10 CST）

用户要求验证领取接口是否也返回 3012，并判断可否“节点只做领取，对话走直连或 WARP”。本轮在 VPS 隔离进程读取生产 SQLite（`mode=ro`），沿用已有账号指纹，显式指定代理、关闭环境代理继承；没有启动生产 store 生命周期、自动领取全池或通知，没有改变生产出口。

源码核对：`app/claim.py` 的 `preview_plans` 使用 `GET /billing/preview`，`_post_claim` 使用 `POST /billing/claim`（JSON `{plan_id}`），均位于 `https://zcode.z.ai/api/v1/zcode-plan`；对话则是 `/anthropic/v1/messages`。`_claim_headers` 复用 `quota._auth_headers`，保留该号设备/平台并附验证码。`_billing_request`、`claim`、`claim_with_captcha` 进入同一 `account_client_pool.operation`；`app/client_pool.py` 的 direct 优先于显式代理，`app/settings.py` 和 `.env.example` 目前没有独立的领取/对话出口开关。领取节流参数 `CLAIM_PROXY_INTERVAL` 只管频率，不选择出口。

19:17 使用同一 HK、JP 两账号，在直连 / HK `21100` / JP `21300` / TW `21200` / WARP 各查 preview、current，共 20 次 GET 全部 `200/code=0`；两账号均已持有 Start Plan、preview 为空。Cloudflare trace 分别确认出口：

| 路径 | 公共 HTTPS 出口 | trace |
|---|---|---|
| direct | `107.174.244.167`，US | `warp=off` |
| HK `21100` | `116.49.103.221`，HK | `warp=off` |
| JP `21300` | `39.110.230.58`，JP | `warp=off` |
| TW `21200` | `36.230.59.156`，TW | `warp=off` |
| WARP `40000` | `2a09:bac1:76a0:450::57b:3e`，US | `warp=on` |

这是 trace 请求的出口观测，不把它当作每个上游连接都固定使用同一地址的证明。21:07–21:10 对同一个已持有套餐 `zcode-v3-start-plan-0817`，提交领取 POST 前在该出口重新读取 current 确认归属，并通过该出口新解验证码：

| 路径 | 真实验证码 / 领取 POST | 同轮官方插件对话 |
|---|---|---|
| direct | 280 字符；HTTP 200、code 1003、`target already exists` | 普通 2.15 秒、SSE 2.46 秒，均 200 / `OK` / `end_turn` |
| HK `21100` | 280 字符；HTTP 200、code 1003 | 普通 HTTP 405、code 3012；未继续 SSE |
| JP `21300` | 首轮 HK 账号验证码 25 秒预算耗尽、未提交；补测 JP 账号同区新解 280 字符，HTTP 200、code 1003 | 固定 HK 账号普通 HTTP 405、code 3012；未继续 SSE |
| TW `21200` | 4 次均 `param_too_short`（76 字符），严格校验未通过，未提交 POST | 普通 3.22 秒、SSE 1.61 秒，均 200 / `OK` / `end_turn` |
| WARP `40000` | 280 字符；HTTP 200、code 1003 | 普通 2.53 秒、SSE 1.58 秒，均 200 / `OK` / `end_turn` |

三路成功 SSE 均有 `message_start`、`message_stop`、文本 `OK`、`end_turn`、流内错误 0；这是 VPS → 官方插件 → 上游对照，不冒充 WARP 下的公网网关完整验收。每个出口新建插件宿主，避免此前 selector 切换后的连接复用问题。这轮对话不附验证码头，领取各使用新解真实验证码，二者不是完全相同的请求形态；此前 HK / JP 对话附真实验证码仍 3012 的独立结果继续保留。

21:10 将 direct preview 扩展到 13 个允许 billing 的账号（active 3、exhausted 10），逐个均 `200/code=0`、plans 为空；其余 3 个 disabled 没有发送请求。未上报新的激活事件来制造活动资格，也未借用其它账号候选套餐。**1003 是“已领取”业务返回，并非新增额度成功；不能据此确认所有未领活动可领、所有 HK / JP 节点可领，或上游在去重返回前确实验证了验证码。**没有当前可领活动，真实新活动 `code=0` 及额度落账验收保持未完成。

分流方案具有接口通路证据：保留 HK / JP 节点给领取链路，对话可使用已验证的 direct 或 WARP。但当前全局出口策略没有用途维度，尚不能通过一项现成配置把领取与对话分开；本轮没有新增生产配置或源码改动。后续最小实现应让手动/自动领取、preview、领取内激活、验证码 producer 继承同一固定代理和操作身份，对话单独选择 direct / WARP；保留领取的同区隔离、20 秒默认物理出口节流、退役引用和代理代次校验。不能把已对话失败的节点直接重新标成“对话健康”，也不能仅修改 claim POST 而让验证码仍走另一出口。

对话使用 WARP 并不会自动为每个账号分配独立出口，也没有消除同 IP 关联风险的证据。当前生产保留此前授权的 plugin + direct；WARP PID 755387，仍 `WarpProxy on port 40000`、Connected；网关/Mihomo PID 715879/2797546，均 active、NRestarts=0。16 个账号仍 active 3 / exhausted 10 / disabled 3，active 的 risk_strikes 均 0；不作长期账号安全保证。五份诊断的 `.env`、生命周期、注册表和 Mihomo 配置哈希断言均未变。

原始报告（目录 0700、报告 0600，临时 auth 副本已清理）：

- VPS `/root/egress-claim-20261016-suqeuvby/report.json`；本地忽略目录 `data/diagnostics/handoff-20261016/egress-claim-query-20261016.json`：20 项 GET 和真实出口。
- VPS `/root/egress-claim-20261016-kcxxgde3/report.json`；本地 `egress-claim-post-20261016.json`：直连/HK/WARP 的真实验证码与 POST，以及 JP/TW 首轮求解失败。
- VPS `/root/egress-claim-20261016-c78xlnyr/report.json`；本地 `egress-claim-chat-20261016.json`：五出口普通请求、三路成功 SSE。
- VPS `/root/egress-claim-20261016-aa8pqjkv/report.json`；本地 `egress-claim-eligibility-20261016.json`：13 个允许 billing 的账号无当前可领活动。
- VPS `/root/egress-claim-20261016-a67pyubv/report.json`；本地 `egress-claim-jp-post-20261016.json`：JP 同区账号补测 POST 返回 1003。

本轮仅完成接口差异及分流可行性核验，未实施分流、未核销 HK / JP 对话恢复或原 11 项待办。
