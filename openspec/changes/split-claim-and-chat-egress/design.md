# Design

## Context

动机见 [proposal.md](proposal.md)。本轮 2026-10-17 02:01 CST 通过 Netcatty 只读核对：VPS HEAD `4695bb2df8be1d4730e5333d209b65dcd04da19f`、工作树干净、网关 PID `1132673`，实际 `plugin + direct`；对话准入为 TW `21200/21201/21202`、US `21506`、OTHER `21833`，HK / JP 为 0。未发模型或领取请求，历史通路证据沿用 `docs/egress_recovery_20261016.md` 的日期和边界。

图谱项目 `D-Temp-lizi-zcode2api`，查询时 generation `2026-10-16T18:02:46Z`。证据文件覆盖返回 metadata_changed，已回读关键源码；vendor 被排除，已直接读宿主代理分支。不以图谱空调用列表作不存在判断。

| 已核对代码 | 对方案的约束 |
|---|---|
| `app/client_pool.py` 的 resolve_proxy / _get_client_locked / acquire_operation / operation | 全局 direct 强制覆盖传入代理；缓存主键没有用途，嵌套上下文按账号复用；不能只给 claim POST 传 proxy |
| `app/claim.py` 的 _billing_request / report_activation_events / claim / claim_with_captcha | 服务端领取已有操作引用、真实代理传递、ClaimPacingGate；浏览器 token 是另一条入口，无法证明在 VPS 同出口出生 |
| `app/captcha.py` 的 get_verify_param / _solve_singleflight / _refill_targets | producer 引用和槽位代次已存在；代理值 None 还可能回落账号解析；补货必须保留已上线的 direct 修复 |
| `app/channels/plugin_channel.py:590`，`vendor/magpie_host.js:414` | Python 层未确认/异常会改空代理；宿主已有显式 `direct` / URL 的逐请求透传能力，优先修 Python，不整文件替换 vendor |
| `app/quota.py` / `app/install.py` / `app/sentinel.py` | 额度刷新、安装及哨兵会独立发激活和 billing，必须明确归属，不能只改自动领取函数 |
| `app/client_pool.py:550`，`app/egress.py`，`scripts/mihomo_auto_probe.py:786` | 当前 admission 是对话准入；固定引用只统计 account.proxy；claim-only 节点需独立绑定并加入排空，不改注册表协议掩盖旧消费者 |
| `frontend/admin/accounts.html:1140` / `app/routes/admin_api.py:617` | 当前手动弹窗启动浏览器 SDK，并向 /claim/manual 传 token；分流时需切到已有服务端领取入口 |
| `frontend/admin/settings.html` / `frontend/js/header.js` / `app/routes/pages.py:52` | 已有设置页与公共导航，包含鉴权、刷新、并发、巡检和通知；直接扩展为配置管理，不新增第二套页面 |
| `app/routes/admin_api.py:677/716` / `app/store.py:172/192` | 管理 API 共用 verify_admin_key；现有 meta 持久化可复用，但 set_setting 先改内存再写库，新出口策略需整对象提交成功后更新已保存视图，不能逐字段半保存 |

本地标准 venv SSL 故障仍在；后续本机可用基础 Python 预加载 SSL 验证，Linux 引用/退役测试在 VPS 独立夹具运行，不能用生产 data 目录跑测试。

## Goals / Non-Goals

**Goals:** 提供后台出口配置、节点选择和准确的生效提示；用现有连接池和 EgressOperation 增加用途维度，在一次操作内固定实际出口和身份，保持可回退配置及可独立核验的外发证据。

**Non-Goals:** 不新建通用策略引擎、领取节点扫描器或代理系统；不调整账号地区分配、设备身份、活动资格、额度状态或现有求解预算。scope 之外的运行故障继续保留原任务。

## Decisions

### D1. 页面配置优先，环境变量作为启动默认

| 环境默认（拟新增，当前尚无） | 取值及作用 |
|---|---|
| `ZCODE_CHAT_EGRESS_MODE` | `inherit`（默认）/ `direct` / `proxy` |
| `ZCODE_CHAT_PROXY_URL` | chat 显式 proxy 时必填，第一版仅支持本地 HTTP 代理，例如 `http://127.0.0.1:40000`；不自动启动 WARP，不声称支持未验收的 SOCKS/远程代理组合 |
| `ZCODE_CLAIM_EGRESS_MODE` | `inherit`（默认）/ `direct` / `proxy` |
| `ZCODE_CLAIM_EGRESS_BINDINGS` | JSON 对象，按大区配置单个 `{port, node_id, epoch}`，仅在 claim 显式 proxy 时使用；缺省 `{}` |

有效策略优先级为：**启动时加载的数据库出口策略完整对象 → 无数据库覆盖时使用上述环境默认 → 用途 inherit 时解析旧 ZCODE_EGRESS_MODE**。数据库保存完整对象而非逐字段混合环境值，避免页面显示 direct、实际被 `.env` proxy 覆盖；用途 inherit 的页面文案为“继承全局模式”，不等同于删除数据库覆盖。没有用途参数的匿名网络操作沿用旧全局模式。

显式 chat proxy 选择单个本地端点；若仍需现有按账号地区选择对话代理，使用继承的 proxy 模式。显式用途策略优先于旧 account.proxy，不给同一用途增加多级失败回退。既有鉴权/刷新/通知等设置继续使用原持久化和生效方式，不迁移或重复存储。

启动检查模式枚举、JSON 结构、地区/端口段、node_id 格式、epoch 正整数、URL；无效配置报错，不默认为 direct。合法空表或部分地区缺项不使整个网关启动失败，而是在该账号 claim 联网前返回“未配置该大区领取出口”。不能因为 HK 绑定缺失停掉已经可服务的直连对话。

环境启动默认示意（没有数据库覆盖时才使用；不直接用于生产，node_id 必须来自当时真实注册表）：

```dotenv
ZCODE_EGRESS_MODE=direct
ZCODE_CHAT_EGRESS_MODE=direct
ZCODE_CLAIM_EGRESS_MODE=proxy
ZCODE_CLAIM_EGRESS_BINDINGS='{"HK":{"port":21100,"node_id":"<实际节点指纹>","epoch":1},"JP":{"port":21300,"node_id":"<实际节点指纹>","epoch":1}}'
```

不用隐式环境代理或 Mihomo 按 URL 路径分流：领取/对话同域，HTTPS 下普通代理看不到业务路径；已有宿主明确支持 HTTP `proxy` 参数。两项覆盖配置比引入规则 DSL 更易检查。配置仅随网关重启生效，本 change 不实现运行中切流。

### D1a. 后台配置管理页与持久化接口

沿用 `/admin/settings` URL，把导航及标题调整为“配置管理”，保留既有卡片，新增独立“出口分流”区域与独立保存按钮。日常操作不需 SSH 编辑 JSON，也不要求填写 node_id / epoch；端口、目录和 Node/Bun 路径等部署参数继续留在 `.env`。

| 页面区域 | 编辑内容与展示 |
|---|---|
| 对话出口 | 继承全局 / 直连 / WARP / 指定本地 HTTP 代理；WARP 映射为 proxy + `http://127.0.0.1:40000`，不另加底层模式 |
| 领取出口 | 继承全局 / 直连 / 固定同区代理；代理模式按大区显示节点下拉框、绑定覆盖数量及缺失地区 |
| 节点选择 | 友好节点名、地区、本地端口及可选/不可选原因；领取不能因为 chat draining 而隐藏同区候选，但须排除失去来源、空槽、身份变化和退役节点 |
| 生效状态 | 当前进程加载的策略/来源、已保存的目标策略/来源，以及“待重启生效”；实际路由失败/未知单独显示，不把已选择节点当成已验证可用 |
| 保存与恢复 | “保存出口配置”和“恢复环境默认”；成功提示说明需重启，不提供自动重启或隐含上游探测 |

新增受现有 verify_admin_key 保护的 `GET/PUT /admin/api/settings/egress` 和 `GET /admin/api/settings/egress/nodes`，不复用可修改密码/通知密钥的 PUT /settings payload。GET 返回运行快照、保存目标、各自来源、pending_restart；nodes 只返回节点显示信息与不透明选择标识，不返回节点密码、订阅地址或真实账号标识。现有 /settings 和其它表单保存不得覆盖出口对象。

PUT 接受完整的规范化策略及从 nodes 返回的选择标识；服务端重新读取槽位/来源验证并填入真实 `(port,node_id,epoch)`，禁止按裸端口在保存时自动接受已换代节点。表单停留期间旧选择失效返回 409，保留原配置并要求刷新；继承模式之外不接受跨区领取绑定。JSON 类型、枚举、URL和节点校验全部通过后，以一个 meta 键 `egress_policy` 原子提交整个策略。只在数据库提交成功后更新已保存视图；校验错误 400、数据库写失败 500 均不改变任何有效或已保存配置。

恢复环境默认仅删除该出口对象覆盖，不改其它 meta、`.env` 或账号；当前运行快照保持到重启。API 不接受任意配置键，不回显/修改密码、验证码、订阅或部署变量；保存本身不访问上游、不发活动领取、也不自动切换 WARP。

### D1b. 已保存与生效快照分开

进程启动时在 store.load 完成后、账号网络链路/补货/探针启动前，从 SQLite 读取一次目标策略，或在没有覆盖时使用本次启动加载的环境默认，验证后冻结为 active_policy / active_source；网关与 CLI 入口遵循同一初始化顺序。后续页面保存仅改变 saved_policy / saved_source，路由、手动验证码兼容分支、补货、断路器和退役协调始终读 active 快照，不能某些模块立即读 store、另一些仍读启动变量。pending_restart 依据规范化目标策略及来源是否不同判断，刷新页面或重启后可重建，不用临时浏览器状态冒充持久化。

没有数据库覆盖时，GET 展示本进程启动时已加载的环境默认，不为预览读取或写回 `.env`；外部改 `.env` 仍须重启才能读入。启动时遇到结构损坏的已保存对象必须明确报错，不悄悄回退环境；合法但缺地区/身份已过期的 claim 仍只影响对应领取，不停掉可服务对话。

保存移除绑定而未重启时，旧活动绑定仍计入 fixed_ports 并阻止回收；尚未生效的新绑定不参与活动准入。重启加载时重新验证候选身份，若在待重启期间退役/换代则拒绝该 claim，不自动改绑。首版保留原重启生效约束，避免新增热切流和多进程广播协议；页面必须明确提示出口配置和既有即时生效字段的区别。

### D2. 固定领取绑定独立于对话准入，复用已有槽位身份

claim 显式 proxy 从配置取账号 assigned_region 对应的唯一 `(port, node_id, epoch)`。运行时核对注册表槽位及生命周期：同地区、同代次、有来源、非 reclaimed/retiring、无有效退役关闭记录、claim 断路器允许。聊天状态 draining 本身不否定领取；空槽、失去来源、真实身份变化必须拒绝。绑定表不写回 `regions` 或对话 admission，不改节点监听，不把 1003 转成“领取已完全验收”。

02:17 只读补核：HK `21100` / JP `21300` 均 epoch=1、来源存在、未 retiring/reclaimed、身份匹配，但对话 admission 为 draining；这只证明当前固定绑定前提，不是本轮真实领取验收。14 个 enabled JWT 账号分布 HK 6 / JP 5 / TW 3，实施必须单独验证并覆盖 TW；只有 HK/JP 的示例不能当全账号分流完成。

读取身份和取得客户端/操作引用在既有池锁内完成，不能把不同版本槽位拼成一次准入；无法确认文件一致性时 fail closed。选择固定一条，失败原地报告，不加跨区或同区自动换节点，避免未确认的账号换 IP。

当前生效的固定领取端口加入消费者 `fixed_ports` 和 ack 的 fixed 计数，不能使用刚保存但尚未生效的对象替换；操作、producer、客户端和 token 继续按物理 slot identity 计数。收到退役请求必须关闭所有用途的新准入；仍在生效的固定绑定会阻止回收，须先移除绑定/重启并排空后才能释放。旧配置遇到槽位换代只能失败，不能自动接受同端口的新节点。保持现有 JSON 注册表协议，避免旧更新器/其它进程读不懂新 admission。

对话断路器与 claim 断路器以 `(purpose, proxy, identity)` 区分；物理退役对二者都生效。claim 的有限半开恢复可由 activation/preview 或正常领取提供实际结果，不新增定时奖励 POST。Mihomo 自动更新器继续只产出对话准入及节点身份；不会覆盖新配置变量，不新增全池领取测试。

### D3. 显式用途穿过连接池和嵌套操作

扩展 EgressOperation 携带 purpose，客户端缓存至少区分账号、用途、有效出口和代次；各账号的前台连接仍独立。父操作只能由同账号同用途的嵌套调用复用，producer 只能派生同一实际出口/身份。即使外层 ContextVar 被后台任务继承，也不能让 claim 额度刷新复用 chat 客户端；released 父引用仍不能复活。

`resolve_proxy`、地区查询、客户端获取、准入/结果、缓存清理均接受/使用有效用途。`None` 在已准入 direct 操作中是明确的无代理，不再代表“尚未解析”；不增设另一套连接池单例。`close_account` 必须关闭该账号所有用途缓存，引用归零规则、取消中性结算和重复释放安全保留。

### D4. 每条链路明确入口

| 链路 | 用途 |
|---|---|
| 网关普通/SSE、三协议适配后的上游对话、Plan/API Key 回退、挑战重解 | chat |
| EgressProbe 的账号对话确认 | chat；direct 无需托管节点确认，显式本地代理可尝试；结果不修改 claim breaker |
| 领取 preview/POST、claim/auto_claim_all_plans 的激活、服务端验证码 | claim；一次自动领取尝试从 preview 到提交持有同一操作 |
| JWT billing/current、balance、usage、领取后刷新及 include_claimable | claim；后台入口显式声明，不继承 chat |
| 账号安装 configs、激活/登录事件，哨兵活动预览/补领，管理页刷新前日活 | claim；保留 installed_at 和活动资格判断 |
| 无账号全局启动安装/configs、OAuth 登录建号之前、非 ZCode 的 API Key 额度查询 | 旧全局策略；本 change 不改变该协议 |

原 `auto_claim_all_plans` 在 claim 外执行的日活/preview 应包进同一 claim 操作，嵌套 claim() 复用它；独立 preview/激活每次按 claim 策略准入。失败/取消均释放引用，不将持有同一固定出口等同于独占该出口。

### D5. 插件不再二次选路，验证码保持实际出口亲和

PluginChannel 消费已准入客户端的真实 proxy。托管继承代理若需要既有账号/端口确认，未确认则返回出口不可用；显式本地 chat proxy 直接尝试；任何错误都不改为 `proxy=None`。PluginHost.fetch 已把 None 序列化为明确的 `direct`，URL 原样传给宿主。验证逐请求 JSON-Lines 参数与实际 TCP/出口，避免仅看 Python 客户端属性；保持 vendor 不变，只有真实验证发现缺口才作有界修复。

captcha config 真正联网时透传操作；已有非 token 公共配置缓存仍可复用，缓存命中不算新出口网络验证。token/singleflight 按实际 proxy 与 slot identity 隔离，不必仅为用途不同而复制同出口 token 池。producer 持有父路由引用；global direct 不得清掉 claim 显式代理。

周期目标只取有效 chat 策略下 selectable Plan 账号的去重出口，保留 direct 的唯一 None 目标、实际水位和停止补货回归。claim 只按需求解，不触发周期或消费回补；即使共享池命中，claim 调用也不安排 claim-only 后台补货。3007 只清失败的实际出口/身份，direct 不能触发全池清空。保留 Singleflight、CPU 槽、前台优先、绝对预算及求解退避，不调大并发来掩盖分流问题。

### D6. 人工选择套餐保留，外部浏览器 token 明确迁移

claim override 非 inherit 时，`/claim/captcha-config` 返回可识别的服务端验证方式，页面不初始化浏览器 SDK；人工选择账号/plan_id 后调用现有 POST `/claim` 由服务器求解。旧 `/claim/manual` 返回 HTTP 409 与稳定的 `claim_verification_route_mismatch` 标识，说明改用服务端领取，且在发上游前短路。保持账户校验、已领取拦截、原 outcomes 和通知结构。

claim 为 inherit 保持原浏览器入口，作为旧兼容路径，不承诺同 IP；不新建浏览器代理、验证码中继或远程桌面。若服务器求解失败，明确显示失败，不偷偷用外部 token 绕过校验。这是有意兼容变化，须在部署说明、API 和页面验收中检查。

## Risks / Trade-offs

- 固定 claim 节点可能只能进入 1003 去重返回，或未来被拒 → 配置前验证 configs/真实求解/preview/POST，逐地区小范围上线；没有新活动就保留发额度任务，不能补造 code 0。
- 按用途会让同账号出现多个公网 IP，固定领取节点、direct 和共享 WARP 均可能被多个账号共用 → 记录这是运营选择，不宣称消除风控或每账号独立 IP；设备身份不变。
- WARP 公网 IP 可变化，URL 不提供 Mihomo 代次语义 → 单独验实际出口与网关普通/SSE，不据此保证 IP 固定或长期安全；本次首次部署建议继续 direct。
- 固定绑定阻止更新器回收端口 → 使用原固定引用/排空协议，绑定移除后才能回收；不得绕开引用计数强删监听。
- claim-only 依赖生命周期文件 → 结构/来源无法验证时仅该 claim 失败，正常 direct chat 保持可用；不以 legacy 对话 admission 代替用途判定。
- 代理未确认时插件不再裸连、显式 claim 不再接收浏览器 token → 两项变更已在 proposal 标 BREAKING，需以对应负向用例及页面确认，不能隐藏在异常兜底中。
- 页面保存成功但未重启，或数据库覆盖使 `.env` 修改看似无效 → 始终展示 active/saved 及来源、待重启提示，所有路由模块只读生效快照；不把持久化成功标为出口验证成功。

## Migration Plan

1. 完成隔离回归、配置 API/页面及 Linux 多进程排空；保全现有代码、出口配置 meta 对象（若有）、`.env`、注册表/生命周期哈希、服务 PID；不将 production data 作为 pytest 夹具。
2. Git 快进部署代码，仅重启网关；先保持用途 inherit，证明当前 plugin + direct 普通/SSE及超过 75 秒的预热行为无回归。定时更新器可能独立更新注册表，须记录时间和来源。
3. 从配置管理页选择真实、仍有来源的同区节点并保存领取绑定，核对 active/saved 和来源；启用 chat direct、claim proxy 前，列清所有允许 billing 账号的大区覆盖，再重启网关加载并核对快照；未配地区明确不可领取，不能把该阶段标为全账号分流完成。
4. 逐地区验证实际路由、激活/preview、服务端验证码及领取 POST；手动选择页面、后台自动入口、代理失败的聊天隔离一并验收。无新活动时只核销通路/路由验收，保留 code 0 到账任务。
5. WARP 是可选生产模式，不作为首次切流依赖；若启用，需从外部客户端补网关普通/SSE及实际出口验收，不以旧 VPS 内对照替代。
6. 回退时仅恢复本次保全的出口 meta 覆盖/环境默认及代码，恢复当前 direct 基线并仅重启网关；不能只改 `.env` 而忽略数据库优先级，也不能回滚整个 accounts.db 丢失后来账号/额度变化。保留运行数据和排空引用，不回滚更新器维护的注册表或批量改账号，回退后再验公网普通/SSE。
7. 规范同步时合并本 change 与已上线 direct 补货增量，更新旧 Purpose/架构及 CONTEXT 中“3012 永久封禁”等过强表述；主规范在本规划阶段暂不改变，不把 planning complete 当实现完成，不自动归档。
8. 2026-10-17 用户确认采用无浏览器验收：以真实管理 API、执行真实页面 JS 的 DOM 行为及 VPS 插件完整链路覆盖配置交互和业务行为；明确保留视觉布局、真实浏览器兼容性未验证，不将 DOM 执行称为真实浏览器通过。
