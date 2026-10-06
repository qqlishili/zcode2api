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

```bash
pytest tests/unit -q                     # 单元（无网络）
pytest tests/interop -q                  # enc:v1 / .zsb 对拍向量
pytest tests/contract -q                 # 上游响应结构契约
docker compose -f tests/mock_upstream/compose.yaml up -d   # Mock 上游 :9901
ZCODE_MOCK_UPSTREAM=http://127.0.0.1:9901 pytest tests/integration -q
docker compose -f tests/e2e/compose.yaml up --build --abort-on-container-exit  # 全栈 E2E
pytest --cov=app --cov-report=term-missing  # 覆盖率（门禁见测试文档 01）
```

## 3. 编码规范

- **类型注解全覆盖**（`mypy --strict` 为目标，至少 `pyright basic` 过）；dataclass 优先。
- 模块依赖方向遵守 01 文档 §3 的规则；`pool.py` / `classify.py` / `translator/` 必须保持无 IO（时间用 `time.time` 注入参数）。
- 上游常量（URL、模型名、关键词表、头名单）一律收口到 `app/constants.py`，禁止散落字面量——它们是风控联动的敏感点。
- 错误分类、状态机转移**必须有对应测试 ID**（见测试文档 02），改行为先改用例。
- 日志：请求行沿用 `#id | fmt | model | status | ttfb | tokens` 表格风格；池换号必须打 `#id account failed (<reason>)` 便于 grep。
- 提交信息 `feat|fix|test|docs|refactor(scope): ...`；每个 Phase 对应里程碑分支 `phase/N`。

## 4. Mock 上游（开发期默认挂接）

`tests/mock_upstream/` 是 FastAPI 应用，模拟 zcode.z.ai / api.z.ai / open.bigmodel.cn 全部端点（见测试文档 04 的注入矩阵）。开发时通过环境变量把上游指过去：

```bash
ZAI_UPSTREAM_URL=http://127.0.0.1:9901/api/v1/zcode-plan/anthropic/v1/messages \
ZAI_FALLBACK_URL=http://127.0.0.1:9901/api/anthropic/v1/messages \
ZCODE_MOCK_UPSTREAM=http://127.0.0.1:9901 python cli.py serve
```

Mock 的故障注入用请求头控制（`x-mock-scenario: quota_exhausted | rate_limited | auth_invalid | captcha_challenge | captcha_3007 | sse_ok | sse_truncate | slow_first_byte`）。

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

### 分开发版脚本

操作者需要 Bash、Git、rsync、SSH，远端需有 rsync、systemctl、curl 与既有运行环境。两份脚本共用 `scripts/deploy-common.sh`，不会自动读取应用 `.env`。

将下面的占位值替换后保存在本机 `.env.deploy.local`（已被 `.env.*` 忽略），只写部署参数，不写账号凭据：

```bash
export DEPLOY_HOST='<SSH_ALIAS>'
export DEPLOY_DIR='<PROJECT_DIR>'
export DEPLOY_SERVICE='<SERVICE_NAME>.service'
export DEPLOY_PORT='<PORT>'
# 前端另有目录时，填写后端 ZCODE_FRONTEND_DIR 实际指向的位置
# export DEPLOY_FRONTEND_DIR='<FRONTEND_DIR>'
```

目录须为非根绝对路径，不含空格、shell 特殊字符或 `.` / `..` 路径段；端口须与现有服务一致。使用已配置的 SSH 身份，不把私钥、密码或真实主机信息写入公开脚本。

```bash
source .env.deploy.local
bash scripts/deploy-backend.sh --dry-run
bash scripts/deploy-frontend.sh --dry-run
# 核对私有配置中的目标与同步清单，确认已有备份和上一版本记录后发布
bash scripts/deploy-backend.sh
bash scripts/deploy-frontend.sh
```

`--dry-run` 仍会通过 SSH 读取远端目录，但不写入或重启。脚本只同步 Git 已跟踪的发布文件：后端为 `app/`（不含旧 `statics/`）、`cli.py`、`requirements.txt` 与 Node 求解器源码、清单、锁文件；前端为 `frontend/`。账号数据、应用配置、虚拟环境、日志、Node 已安装依赖和本机未跟踪文件均不在同步范围内。

依赖未变时后端脚本同步后重启已有 systemd 服务，并检查状态及 `/meta`；前端从磁盘热读，无需重启。依赖有变时，在维护窗口停服，先执行 `bash scripts/deploy-backend.sh --sync-only`，在远端原虚拟环境中安装 `requirements.txt`，按 Node 锁文件执行 `npm ci`，再执行正常后端发版。脚本不会自动升级依赖。

脚本不删除远端文件；涉及源码删除或改名时，按差分单独处理对应代码文件，避免对项目根目录执行 `--delete`、`git clean` 或 `git reset --hard`。前端目标必须与服务实际读取目录一致。

rsync 更新的是远端工作树，不会更新远端 Git HEAD。发布记录须关联本地代码版本与实际同步清单；不能只看远端提交号判断运行版本。后续使用 `git pull` 前，先核对远端工作树差分。

发布验收：`/meta` 的版本、前端页面与 `frontend/version` 一致，再用实际客户端完成一次请求；仅探活成功不代表功能验收通过。回滚用发布前记录的代码版本与依赖恢复服务，保留账号数据；详细备份位置和真实操作记录不进公开仓库。

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
| 全部请求 503 no_available_account | `GET /admin/api/pool` 看状态分布；`billing` 端点 401 多为 JWT 过期（需重登）而非无额度 |
| 验证码连续失败 | 确认 `captcha_node/node_modules` 已装；`ZCODE_CAPTCHA_TIMEOUT` 调大；阿里云指纹逻辑变更时需更新 solver.js 的浏览器 API 桩 |
| 额度一直是 0 / 401 | WAF 拦截：检查是否带全套身仿真头；错峰参数是否被调成 0 |
| 领取一直 ineligible | `identity.appVersion` 低于活动要求，升级配置值 |
| .zsb 导入解密失败 | 口令错误（错口令即失败无提示，是设计行为）；确认 KDF 迭代未被改 |
