# 06 — 开发指南

## 1. 环境搭建

```bash
# 依赖：Python 3.11+、Node 20+（验证码求解器）、Bun（插件通道宿主）
# --recursive 必需：vendor/magpie-plugins 是子模块，不拉则插件通道缺插件目录
git clone --recursive <zcode-hub 仓库> && cd zcode-hub
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # fastapi uvicorn httpx cryptography pytest pytest-asyncio respx
cd captcha_node && npm ci && cd ..       # happy-dom（按锁文件安装）
cp .env.example .env                     # 按需修改；不要提交真实配置
```

已 clone 但漏了子模块时补拉：`git submodule update --init --recursive`。
Bun（插件通道）与 Node / mihomo 的安装位置要求见 §5「运行时依赖」。

本地跑起来：

```bash
python cli.py serve                     # 网关 + 后台，默认监听 0.0.0.0:3000
python cli.py login zai                 # OAuth 登录（浏览器授权，凭证入池）
python cli.py accounts                   # 巡检；另有 quota / status
```

## 2. 测试命令

在项目虚拟环境中运行。启动前，将 `ZCODE_DATA_DIR` 指向本次新建的临时目录，并设置 `PYTHON_DOTENV_DISABLED=1`；上游隔离见 §4，不在生产服务目录测试。

```bash
python -m pytest tests/unit -q
python -m pytest tests/integration -q
python -m pytest --cov=app --cov-report=term-missing  # 需安装 pytest-cov
```

Windows 可通过 `./.venv/Scripts/python.exe -m` 调用同样模块。`tests/interop/`、`tests/contract/` 与 Mock / E2E compose 尚未提供，对应测试规划见测试文档 `03`～`05`。

## 3. 编码规范

- **类型注解全覆盖**（`mypy --strict` 为目标，至少 `pyright basic` 过）；dataclass 优先。
- 模块依赖方向遵守 01 文档 §3 的规则；`pool.py` / `classify.py` / `translator/` 必须保持无 IO（时间用 `time.time` 注入参数）。
- 上游常量（URL、模型名、关键词表、头名单）一律收口到 `app/constants.py`，禁止散落字面量——它们是风控联动的敏感点。
- 错误分类、状态机转移**必须有对应测试 ID**（见测试文档 02），改行为先改用例。
- 日志：请求行沿用 `#id | fmt | model | status | ttfb | tokens` 表格风格；池换号必须打 `#id account failed (<reason>)` 便于 grep。
- 提交信息 `feat|fix|test|docs|refactor(scope): ...`；每个 Phase 对应里程碑分支 `phase/N`。

## 4. Mock 上游（隔离测试）

`tests/mock_upstream/server.py` 提供 FastAPI Mock，覆盖网关、额度、领取、客户端配置与 OAuth 的测试路由。既有 `gateway_client` 夹具启动本机 TCP Mock，并注入上游地址、额度查询和验证码桩；场景与控制头见测试文档 `04` §2。

仅设置 `ZAI_UPSTREAM_URL` / `ZAI_FALLBACK_URL` 不能隔离 billing、OAuth、事件上报等调用。项目没有统一重定向所有上游的环境变量；独立运行开发服务前须分别核对这些调用的注入，不能使用真实账号做隔离测试。

### 验证码诊断与预算

默认失败摘要不依赖 `CAPTCHA_DEBUG`：

```text
SOLVER_DIAG={"v":1,"code":"param_too_short","stage":"extract","exit_code":6,"param_chars":76,"elapsed_ms":1830}
```

该示例仅为合成夹具。Node 纯模块 `solver_diagnostics.js` 复用真实参数校验，摘要最多2KiB且仅含上述字段。Python 校验前缀、版本、类型、类别、长度与实际退出码；损坏/未知摘要保留安全未知类。退出6无摘要为 `param_invalid_unknown`，其它失败为 `solver_failed_unknown`，缺Node为 `solver_unavailable`，成功退出无参数为 `solver_no_output`。原始 stderr、验证参数、securityToken、certifyId、出口地址或 SDK 文本不进入新增诊断；无需全局开启 DEBUG。

每个共享 producer 使用现有 `ZCODE_CAPTCHA_TIMEOUT`（默认25秒）覆盖配置/排队/执行/退避；最多尝试次数仍由 `ZCODE_CAPTCHA_RETRIES` 控制。每次 Node 及回收占槽，退避和配置在槽外，删除10秒排队切片。双核最大一个活跃Node；补货只针对**可服务账号实际绑定的端口**且每端口水位 1；同端口连续 2 次求解失败即退避 45–90 秒（`CAPTCHA_PORT_FAIL_THRESHOLD` / `CAPTCHA_PORT_COOLDOWN`）。新出口和后续有限挑战各有独立预算，同请求同出口已终止失败可复用，有效缓存优先；不新增全局失败缓存或配置开关。

25秒不限制模型连接、上游429等待、整个故障转移或 SSE。总耗时仍受有限账号/挑战/上游重试边界影响，应测量实际客户端等待能力。排队/预算终止和最后求解器类别分别查看；无实际网络证据不惩罚出口，取消仅释放自己的需求。清理时间单列，不声称预算截止瞬间已回收。

用监控 `req_id` 对照 `journalctl -u <SERVICE_NAME>` 中最终 `code/stop_reason`，再以 `solve_id` 对照安全 producer 摘要。页面保留最近500条内存记录且重启清零；跨重启的可用性判定改用落盘证据 `data/evidence.db`（`GET /admin/api/evidence/summary?hours=24` 给出请求成功率、`t_first` 分位、求解成功率、池命中率与按端口/原因的分布）。Node纯函数、实际manager Events、临时Node清理和本地TCP测试分别验证参数、调度、回收与三协议，不能用探活代替业务恢复。

## 5. 构建与部署

### Linux + systemd（源码部署）

现有 VPS 使用 Python 虚拟环境、Node 求解器和 systemd 服务。继续使用既有目录、服务和数据；公开文档只记录通用约定，真实地址、目录、端口、服务名与运维记录留在私有配置中。

首次部署按 §1 安装依赖，在 `.env` 中设置端口与密钥。服务须能找到 Node；如需绝对路径，使用 `ZCODE_NODE_PATH`。已有服务保留原配置，以下仅为新环境的单元示例（替换 `<PROJECT_DIR>`，按实际服务名保存）：

```ini
[Unit]
Description=ZCode Hub
After=network.target

[Service]
WorkingDirectory=<PROJECT_DIR>
ExecStart=<PROJECT_DIR>/.venv/bin/python <PROJECT_DIR>/cli.py serve
EnvironmentFile=<PROJECT_DIR>/.env
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

新单元安装后执行 `systemctl daemon-reload`，再按实际服务名启用。运行状态与日志分别用 `systemctl status <SERVICE_NAME>`、`journalctl -u <SERVICE_NAME>` 查看。

### 多出口代理与账号连接池隔离

为了避免多账号集中使用单一机房出口 IP 触发上游风控（HTTP 405 / 3012），系统支持通过 `ZCODE_PROXIES` 配置多出口中继代理池（逗号分隔）：

```env
# 本值由 scripts/mihomo_auto_probe.py 依订阅自动回写，通常无需手填。
# 端口按大区分段（见 app/constants.py 的 REGION_PORT_RANGES），每区一段：
#   HK 21100-21199  TW 21200-21299  JP 21300-21399  SG 21400-21499
#   US 21500-21599  KR 21600-21699  EU 21700-21799  OTHER 21800-21899
ZCODE_PROXIES=http://127.0.0.1:21100,http://127.0.0.1:21200,http://127.0.0.1:21300
CLIENT_IDLE_TIMEOUT=300
```

- **物理隔离**：每个账号独占专属独立的 `httpx.AsyncClient`，连接池完全切分，互不干扰；
- **哈希绑定**：按 `account_id` MD5 选择账号大区的已提交出口；有效缓存保持稳定，退役后新操作转同区替代；
- **直连边界**：没有托管出口配置的普通客户端可直连；已绑定大区或已迁移托管出口的客户端没有合格代理时报告离线；
- **资源回收**：单账号 Client 空闲超过 `CLIENT_IDLE_TIMEOUT`（默认 300 秒）且实际操作引用归零后才关闭。

### 反向代理与登录锁定

后台登录失败计数只使用 ASGI 的客户端地址（`request.client.host`），不直接读取 `CF-Connecting-IP` / `X-Real-IP`。直连部署沿用现有启动方式；反向代理的转发头由 Uvicorn 既有的可信来源机制处理，不在鉴权层重复解析。

存在反向代理时，由代理生成或规范追加 `X-Forwarded-For`，在私有服务环境中用 `FORWARDED_ALLOW_IPS` 指定实际连接网关的可信代理地址。不要设为 `*` 或包含任意客户端的公网网段；真实代理地址不写入公开示例。

代理来源未受信或未传有效转发头时，计数会按代理连接地址归组。发布前须核对代理与 Uvicorn 配置，并验证同一客户端换伪造头仍会锁定、不同客户端的失败计数相互隔离；仅探活成功不能证明该链路正确。

### Git 发布（原目录更新）

发布顺序：本地提交 → 推送原分支 → VPS 原目录快进更新 → 核对提交与运行文件。沿用已有服务、数据和配置，统一通过 Git 发布。

核对差分，只暂存本次确认的文件，提交信息沿用 §3。推送后记录完整 `<RELEASE_COMMIT>`，核对远程分支。

```bash
git status --short
git diff -- <FILE_1> <FILE_2>
git add -- <FILE_1> <FILE_2>
git commit -m '<TYPE>(<SCOPE>): <DESCRIPTION>'
git push origin <BRANCH>
git rev-parse HEAD
git ls-remote origin refs/heads/<BRANCH>
```

VPS 有未提交修改时，先逐文件核对并承接到本地发布提交；确认一致后再处理对应改动。不直接覆盖或用 `git reset --hard` / `git clean` 清场。

工作树干净、当前分支正确且指定提交已推送后，在原目录执行：

```bash
cd '<PROJECT_DIR>'
git fetch origin <BRANCH>
git merge --ff-only <RELEASE_COMMIT>
git rev-parse HEAD
git status --short
```

不能快进时先核对提交差异，不强推或改写历史。依赖有变时在维护窗口按原虚拟环境和 Node 锁文件安装；后端业务改动重启已有服务，文档或前端热读文件改动无需重启。

发布验收：三处提交号一致，运行文件无未提交差分，服务仍读取原目录；业务改动核对 `/meta`、前端版本及实际客户端请求。记录上一提交与依赖版本以便回滚，保留账号数据。

### 运行时依赖（Node / Bun / mihomo）

三者都不随仓库分发，需自行安装。**关键约束是 systemd 服务的 PATH 与登录 shell
不同** —— 实测服务进程拿到的是 systemd 默认值：

```
/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/snap/bin
```

它**不含** `~/.bun/bin`、`~/.nvm/.../bin`。因此：

- **Node**（验证码求解）：装在 PATH 覆盖得到的公共位置（如 `/usr/local/bin`，
  或用 nvm 后自行建符号链接），此时 `ZCODE_NODE_PATH` 无需设置；
  装在别处则必须用 `ZCODE_NODE_PATH` 指明绝对路径。
- **Bun**（插件通道宿主）：默认按 `bun` 走 PATH；若装在 `~/.bun/bin` 这类
  服务 PATH 覆盖不到的位置，**必须**用 `ZCODE_BUN_PATH` 指明绝对路径，
  否则插件通道启动时报 `Bun 不可执行`。可用 `systemctl show <服务名> -p Environment`
  与 `/proc/<MainPID>/environ` 核对服务实际拿到的 PATH。
- **mihomo**（出口中继）：见下节。

启动前可用 `scripts/plugin_preflight.py` 校验 bun / 宿主脚本 / 插件目录三项是否就绪。

### 周期 Mihomo 更新器

mihomo 二进制不随仓库分发：从 Mihomo Meta 官方 release 取与本机架构匹配的
`mihomo`，放到 PATH 下（或任意位置后用 `MIHOMO_BINARY` 指定）并赋予可执行位。
版本按实测可用者为准，升级前先在维护窗口单独验证监听与出站，再替换。

中继配置（listeners / proxies / providers）由更新器脚本按订阅生成并写入生产
配置路径，不由手工维护；因此**不要手工编辑生产配置**，改动会在下一轮被覆盖。
数据目录、配置路径与控制器地址沿用既有约定，需要偏离时用
`MIHOMO_CONFIG_PATH` / `MIHOMO_CONTROLLER_URL` 覆盖。

探测二进制取 `MIHOMO_BINARY` 或 PATH 中的 `mihomo`；生产配置/Controller 沿用 `MIHOMO_CONFIG_PATH` / `MIHOMO_CONTROLLER_URL`，鉴权从生产配置读取。`MIHOMO_PROBE_PORT` 为可选回环端口，未设置时动态分配；占用或无法确认子进程监听归属时失败，不借用生产端口。`MIHOMO_PROBE_TIMEOUT` 覆盖单节点业务请求超时（默认 10 秒）。无需新依赖或常驻第二服务。

探测身份只需凭证有效且启用：额度耗尽或风控禁用账号同样可用于验证出口可达性，`max_tokens=1` 的探测请求不消耗额度；池内无任何有效凭证时才整轮未提交。可达性判定复用网关业务错误归一化，业务响应（额度耗尽、限流、验证码挑战）计为可达，连接失败、超时与 5xx 上游故障计入节点失败；排障时按 `phase=node_result` 的 `reachable` 与 `outcome` 字段区分业务受理与网络失败，不以状态码 200 单点判定。

沿用现有 oneshot/timer，仅为更新器设置有限 `TimeoutStartSec`、`TimeoutStopSec=15s`、`KillMode=control-group`。支持候选数的上界按 `10 + 12 × N + 16 × 订阅数 + 120` 秒核算，超时须高于该预算；脚本在受 systemd 调度时读取当前单元的有效超时，不接受 infinity 或不足的期限。

发布前暂停现有 timer、确认 updater 未运行，核对真实服务/worker、后台和共享消费者，私有备份源码、固定发布集合、基础配置及单元；核验权限/缺失标志/哈希。通过实际 Antigravity CLI 源码验收后按 Git 推送及 VPS 原目录快进发布，核对运行文件哈希。首次允许一次维护暂停，先关闭新准入、停后台并排空旧操作/Node，再统一部署网关协议和固定槽/file 提供器结构；记录实际暂停区间，恢复服务后分别验证短请求与正常 SSE。

首次维护前核对 `net.ipv4.ip_local_port_range` 与全部实际业务槽是否重叠；重叠时将配置中的业务端口合并到既有 `net.ipv4.ip_local_reserved_ports`，保存原值及专用 sysctl 文件的权限/缺失状态以便回退，不覆盖其它服务的预留。核对新增槽未被其它监听或在途源端口占用，加载后按原 PID 验证全部监听；配置接口成功不代表每个 bind 成功。预留只防止后续自动源端口分配，不解除现有连接占用；冲突须在维护内查明并处理，禁止日常重载或重启。

生产基础配置及提供器路径来自已有配置；日常脚本只接受已迁移结构和消费者协议。每槽始终有唯一物理身份或 reject，正常发布/恢复只调用提供器刷新和槽身份核验，不使用 `/configs`、生产 restart 或关闭连接接口。基础配置哈希不符停止，不能覆盖未知配置。`ZCODE_DATA_DIR` 沿用现有私有数据目录，不在公共示例写真实地址、订阅或认证。

待提交记录位于 `.mihomo-publish`，目录 0700、副本 0600。下轮先恢复再外呼：注册表提交前恢复旧固定集合，提交后向前核验。退役清理使用私有 `egress_retirement.json`、`egress_consumers/` 和 `egress_reclaim.json`，确认绑定实际进程启动身份、当前版本和节点代次。确认丢失/过期、固定引用、未知消费者或连接未排空均留下等待原因；观察超时不回收槽。已观测未知进程的启动身份和槽保存在 `egress_unknown_consumers.json`，该进程退出前不能用瞬间零连接撤销阻塞。reject 后中断核对实际状态，未确认冻结槽，不能删除记录掩盖失败。

无前台请求时应用协调任务仍关闭旧准入和零引用缓存。static 全局列表不能重新启用退役托管槽；新增节点仅用同区安全空槽，仍在排空或未知状态的槽不覆盖。日常完整周期必须在实际客户端非流式/流式业务中验证，全部验收通过才恢复原调度。回退旧缺陷脚本时 timer 保持暂停；涉及基础结构的回退另用维护窗口，不能把重启写作日常恢复。

### 常驻中继单元

mihomo 自身作为一个常驻服务运行；其单元不含项目路径，可直接照抄
（`<CONFIG_DIR>` 替换为实际配置目录，默认 `/etc/mihomo`）：

```ini
[Unit]
Description=Mihomo Multi-Egress Relay Daemon
After=network.target

[Service]
Type=simple
User=root
ExecStart=/usr/local/bin/mihomo -d <CONFIG_DIR>
Restart=always
RestartSec=3
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
```

更新器单元是 oneshot + timer，`WorkingDirectory` 与 `ExecStart` 同样以
`<PROJECT_DIR>` 占位，按前述替换后再启用。

### 公开仓库边界

- `.env`、`.env.deploy.local`、`data/`、私钥与账号导出文件不入库；提交前检查所选文件与差分。
- 公共示例只用占位符；真实域名、IP、SSH 别名、个人电脑路径和生产日志留在私有运维记录中。
- 当前文件脱敏不会擦除旧提交中的信息；历史清理另行评估，保留上游署名与许可证义务。

## 6. 目录与命名

- 模块名单数（`store.py`/`pool.py`）；测试文件与被测模块同名 `test_<module>.py`。
- 上游模型名常量保留官方大小写（`GLM-5.2`），映射表 key 用小写。
- 时间字段统一 unix 秒（float），UI 层负责本地化。

## 7. 常见排障

| 症状 | 排查 |
|------|------|
| 全部请求 503 no_available_account | `GET /admin/api/status` 看状态分布；`billing` 端点 401 多为 JWT 过期（需重登）而非无额度 |
| 验证码连续失败 | 确认 `captcha_node/node_modules` 已装；`ZCODE_CAPTCHA_TIMEOUT` 调大；阿里云指纹逻辑变更时需更新 solver.js 的浏览器 API 桩。若失败集中在单一出口，查 `/admin/api/evidence/summary` 的 `by_port` / `by_reason`：单端口产率过高会触发端口退避（症状是 `solver_port_backoff`），此时应把账号分散到同区端口或降低该端口水位 |
| 额度一直是 0 / 401 | WAF 拦截：检查是否带全套身仿真头；错峰参数是否被调成 0 |
| 领取一直 ineligible | `identity.appVersion` 低于活动要求，升级配置值 |
