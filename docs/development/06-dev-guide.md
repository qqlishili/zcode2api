# 06 — 开发指南

## 1. 环境搭建

```bash
# 依赖：Python 3.11+、Node 20+（验证码求解器）
git clone <zcode-hub 仓库> && cd zcode-hub
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # fastapi uvicorn httpx cryptography pytest pytest-asyncio respx
cd captcha_node && npm ci && cd ..       # happy-dom（按锁文件安装）
cp .env.example .env                     # 按需修改；不要提交真实配置
```

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
| 验证码连续失败 | 确认 `captcha_node/node_modules` 已装；`ZCODE_CAPTCHA_TIMEOUT` 调大；阿里云指纹逻辑变更时需更新 solver.js 的浏览器 API 桩 |
| 额度一直是 0 / 401 | WAF 拦截：检查是否带全套身仿真头；错峰参数是否被调成 0 |
| 领取一直 ineligible | `identity.appVersion` 低于活动要求，升级配置值 |
