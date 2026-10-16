# Execution

实施与验收记录（2026-10-13 ~ 10-14）。变更依据 [design.md](./design.md)，规范增量见 [specs/](./specs/)。

## 1. 代码改动

| 文件 | 改动 |
| --- | --- |
| `app/client_pool.py` | `resolve_proxy` 改为「注册表全量端口基准索引 + 环形避让」（`_binding_index` / `_bound_port`）；新增 `serviceable_ports()`（可服务账号绑定端口集合，高频路径无日志） |
| `app/captcha.py` | 删除 `POOL_MIN/POOL_MAX` 死常量；`_refill_loop`/`_refill_batch` 改用 `_refill_targets()`（= 可服务账号绑定端口）；新增端口级退避（`_note_port_success` / `_note_port_failure` / `port_backoff_remaining`）；`_produce` 记录端口成败与落盘证据；新增本地诊断码 `solver_port_backoff` |
| `app/settings.py` | `CAPTCHA_REGIONAL_POOL_MIN` 默认 2 → **1**；`CAPTCHA_SOLVE_TIMEOUT` 默认 40 → **25**；新增 `CAPTCHA_PORT_FAIL_THRESHOLD=2`、`CAPTCHA_PORT_COOLDOWN=60`、`EVIDENCE_ENABLED`、`EVIDENCE_RETENTION_DAYS=7`；删除 `CAPTCHA_POOL_MIN/MAX`；`APP_VERSION` → 2.6.7 |
| `app/evidence.py`（新增） | 落盘证据：`data/evidence.db`（WAL）、`requests` / `solve_events` 两表、字段白名单、7 天保留（`prune()`）、`summary(hours)` 汇总、`port_of()` |
| `app/reqlog.py` | `finish_ok` / `finish_error` 落库（`_persist`，纯观测、绝不抛出） |
| `app/routes/admin_api.py` | 新增 `GET /admin/api/evidence/summary?hours=` |
| `.env.example` | 删除死配置、补新参数与证据开关说明 |
| `frontend/version` | 2.6.6 → 2.6.7 |
| 文档 | `01-architecture.md` §4.7（改写"预解池多大区均衡采样"为"按可服务账号出口预热 + 每端口水位 1"，补端口退避与稳定绑定）、`03-data-formats.md`（环境变量表）、`06-dev-guide.md`（25s 预算、退避、证据入口）、`07-roadmap.md`（原"均衡采样"条目加修订说明） |
| `CONTEXT.md` | 增补术语：出口绑定、出口漂移、预解 token 池、池水位、端口产率、落盘证据 |

## 2. 本地验证

- `ruff check app tests` → All checks passed。
- 新增用例 **26 项全绿**（unit 23 + integration 3）：`test_egress_binding_stability.py`(6)、`test_refill_domain.py`(5)、`test_port_backoff.py`(6)、`test_evidence.py`(6，含"证据库不含 prompt/参数"断言)、`test_evidence_endpoint.py`(3)。
- 适配既有用例：`test_quota_bonus.py` 移除对已删除 `POOL_MIN/POOL_MAX` 的 monkeypatch。
- 全量回归（分片执行，两个目录各自跑到 summary，共 **946** 项：unit 556 + integration 390）：
  - `tests/unit` **仅 3 项失败**，全在 `test_notify.py`（Bark 投递），原因是本机 Windows 系统代理（`httpx` 解析 `NO_PROXY` 中的 `[::1]` → `Invalid port: ':1]'`）。
  - `tests/integration` **仅 16 项失败**，全在 `test_oauth_login.py` / `test_oauth_protocol.py`，同一代理环境原因。
  - **同一失败集在基线 `03a441d` 的独立 worktree 上完全复现**（`git worktree add .baseline HEAD` 后复跑上述文件得到同样的失败），故与本次改动无关；其余 927 项通过。
  - 说明：整进程一次跑完全部 946 项在本机因上述网络超时用例极慢（>25 分钟仍在 38%），故改为按目录分片 + 基线对照；两个目录的完整 summary 均已取得。

## 3. VPS 部署与实际验证（107.174.244.167，`/root/code/dengyie-zcode2api`）

- 备份：`/root/backup-2.6.6/pre-deploy-2.6.6.tar.gz`（app/docs/tests/frontend/version/.env.example/CONTEXT.md）。
- 部署：上传 → 解包 → `sed -i` 删除 `.env` 中 `CAPTCHA_POOL_MIN=8` / `CAPTCHA_POOL_MAX=20` → `systemctl restart zcode2api`。
- 启动：`active`、`NRestarts=0`、banner `zcode-hub v2.6.7`、监听 `0.0.0.0:18088`、`import app.*` 自检通过。

### 3.1 预热域收窄（新能力实证）

`data/evidence.db` 记录的实际求解端口（重启后 10 分钟窗口，12 次求解 = 1.2 枚/min）：

```
00:51:34 21205 refill ok
00:51:34 21200 refill ok
00:51:52 21302 refill solver_budget_exhausted (19.8s)
00:51:54 21200 refill ok
00:52:19 21302 refill solver_budget_exhausted (25.0s)
00:52:21 21200 refill ok
00:52:28 21303 refill ok (6.7s)
...
```

- 端口全部落在 TW/JP，**没有任何 HK/SG/US/KR/EU/OTHER 端口被预热**（改造前是 8 个大区各占 1 个首端口）。
- 端口集合与可服务账号绑定完全一致：`serviceable_ports = [21302(JP/jatnea), 21200(TW/laozuf)]`，同时刻 `juelaj` 仍为 active → 其绑定 21205 也在集合内（随后被额度刷新标记 exhausted 后自动退出）。
- 速率 1.2 枚/min，落在设计目标（2 端口 × 0.8 = 1.6/min）之下；改造前目标速率是 12.8 枚/min。

### 3.2 端口退避（新能力实证）

```
[~] captcha 端口 http://127.0.0.1:21302 连续 2 次求解失败，退避 85s
```

退避后补货自动转移到同区 21303（`_bound_port` 环形避让），且求解耗时回到 **6.5–8.5s**（被压端口此前为 14–19s）。

### 3.3 水库命中与快速失败（新能力实证）

- 一次真实 `/v1/messages`（`ping`，8 tokens）在 **0.9s** 内完成分类：`账号 laozuf 额度用完，切换下一个` → `code=mixed_unavailability stop_reason=no_candidate` → HTTP 503；**无 500、无 11–20s 验证码等待**。
- 同一次请求在证据库留下 `solve_events(port=21200, source=hit, hit=1)`：**验证码阶段由水库命中承担**（这是改造前 25 小时窗口里几乎不出现的事件）。
- 落盘证据同时在 `requests` 表留下该请求（account/mode/status/error/t_first），证明观测链路贯通。

### 3.4 资源

- 服务进程：`%CPU 3.8`（重启后均值），load average **0.26**（改造前 1.38–2.24，且被 8 大区补货持续占用）；可用内存 825MB（改造前 652MB）。
- 旧进程在停机时累计 **10h 7min CPU time**，即此次改造前的常态空转成本。

## 4. Git 发布流程（2026-10-14）

本地 → GitHub → VPS，全部经 git（不再依赖文件直传；`openspec/` 被 `.gitignore` 忽略，规划与验收记录按项目既有约定另行显式传输）。

| 提交 | 内容 |
| --- | --- |
| `03f91b7` feat(evidence) | 落盘证据模块 + reqlog 落库 + 汇总端点 + 用例 |
| `fc2c0aa` fix(captcha) | 预热域收窄、稳定出口绑定、端口退避、死配置清理、预算 25s、版本 2.6.7 |
| `df372d0` docs(captcha) | 架构/参数/排障/路线图同步 |
| `3c12dfc` fix(test) | 默认关闭落盘证据，避免测试污染真实证据库（见下） |

- 推送：`origin main`（`github.com:qqlishili/zcode2api`）→ `03a441d..3c12dfc`。
- VPS 侧：`git stash push`（保存早前 tarball 落地的同内容改动）→ 把 6 个"未跟踪但将被提交覆盖"的新文件先备份到 `/root/backup-2.6.6/untracked-pre-pull/` 再删除（git 拒绝覆盖未跟踪文件）→ `git pull --ff-only` → `systemctl restart zcode2api`。
- 结果：`HEAD = 3c12dfc`、工作树干净（仅未跟踪 `CONTEXT.md`）、服务 `active`、`NRestarts=0`、banner `v2.6.7`、监听 18088。
- 备注：VPS 上另有一个早于本次的 `stash@{1}: On main: stash before sync`（非本次产生），未触碰。

### 4.1 VPS 干净环境回归（受影响模块 + 全部新增用例）

`ruff check app tests` → All checks passed；`pytest` 51 项 → **仅 1 项失败**，即下述既有隔离缺陷；新增用例 26 项全绿。

### 4.2 发现并修复：测试污染生产证据库（本次引入）

VPS 首次跑验证时，经过 `reqlog`/`captcha` 的用例把夹具行写进了**真实** `data/evidence.db`：`requests` 555 条（`req_id` 为 `r`/`r1`/`r549` 等夹具值）+ `solve_events` 60 条（端口 `NULL`/`21100`/`21200`/`21301`…），会污染 7 天可用性基线；本机同样复现（606 条请求 + 79 条求解）。

- 数据清理（VPS）：按"`req_id` 长度 ≤8"删除 555 条请求；按夹具簇秒（01:21:18/19/20/31）删除 60 条求解；清理后 `requests` 剩 1 条（真实 `ping` 请求）、`solve_events` 剩 36 条（00:51–01:23 生产节奏 1 枚/75s），本机残留库直接删除。
- 根因修复（`3c12dfc`）：`tests/conftest.py` 新增 autouse fixture `isolated_evidence`（默认 `EVIDENCE_ENABLED=0`，前后 `reset()`）；`tests/integration/test_evidence_endpoint.py` 改为显式开启并指向 `tmp_path`。
- 复验（VPS）：跑 51 项后证据库 **`requests` 1→1、`solve_events` 37→38**（唯一 +1 是生产补货求解），污染归零。

### 4.3 发现但未修（与本改动无关，如实记录）

`tests/unit/test_client_pool.py::test_consistent_hash_proxy_distribution` 用**默认注册表路径**构造连接池，因此在数据目录存在 `egress_registry.json` 的机器（VPS）上必然失败：`resolve_proxy` 的全局兜底优先使用注册表槽位而非 `ZCODE_PROXIES`。已在基线 `03a441d` 的独立 worktree 上用同一数据目录复现（同断言、同失败），属既有测试隔离缺陷；按"不顺手修历史问题"的约定未纳入本次提交，修法是把该用例的 fixture 指向 `tmp_path` 注册表。

## 5. 未完成 / 限制（如实标注）

1. **真实端到端 20 请求验收未做**：4 个 Plan 的 `ends_at = 2026-10-13 23:59:59`，用户已确认接受到期后全线失败（Q1-C）。当前账号池为 13 exhausted / 1 active / 1 cooling，无足够额度跑批量真实请求。待新账号入库后执行（tasks 8.1）。
2. 本机 `tests/unit` + `tests/integration` 存在 **19 项环境性失败**（Bark/httpx 系统代理解析），与本次改动无关且在基线 worktree 上复现；未在本次修复（属本机环境问题，非代码问题）。VPS（干净 Linux）同一批用例无此问题。
3. `21302`（JP 第二端口）在改造前后都出现 `solver_budget_exhausted`，与出口断路器反复熔断该端口的历史记录一致；本次只保证"不再把算力摊到无人消费的大区、并按端口退避隔离坏端口"，未改出口探测本身。
4. 无消费者时维持 1 枚/75s 的预热仍属空转成本（比改造前低约 10 倍，但不是零）；是否需要"按最近需求门控预热"留待观察到真实到达率后再决定。
5. `CONTEXT.md`（根目录术语表）在本地与 VPS 均**未跟踪**（本次会话前即如此），因此未包含在 git 发布中；VPS 上的副本由显式传输保持同步，是否纳入版本库由用户决定。
