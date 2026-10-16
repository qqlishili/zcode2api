# zcode2api 交接文档

> 生成时间：2026-10-16 13:30 (CST)
> 用途：把当前状态、已完成、待办、已知缺陷完整交代给下一个接手者（人或 AI）

---

## 一、当前部署形态（已验证）

### 代码流向

```
本机  D:\Temp\lizi\zcode2api
  │   HEAD: 9a6d971  「chore: 纳入 openspec 变更记录与 CI 配置」
  │   tracked: 411     未提交: 0
  │   remote: git@github.com:qqlishili/zcode2api.git
  │
  ├─ push ──→ GitHub **private** qqlishili/zcode2api
  │                    │
  │                    └─ git clone --recursive ──→ VPS
  │
VPS  /root/code/zcode2api
      HEAD: 9a6d971（与本机同一 commit）
      tracked: 411     未提交: 0
      子模块: 6a3fe9b49e7e638ed79f00d274ad6e1b93a384cd (heads/main)
```

### 服务（systemd）

| 单元 | 状态 | 说明 |
|---|---|---|
| `zcode2api.service` | enabled + active | 网关，监听 `0.0.0.0:18088` |
| `mihomo.service` | enabled + active | 出口中继，800 槽位（21100–21899） |
| `mihomo-probe-updater.timer` | enabled + active | 每 6h 跑 `mihomo_auto_probe.py` |

单元文件位于 `/etc/systemd/system/`，路径已指向 `/root/code/zcode2api`
（`WorkingDirectory` / `EnvironmentFile` / `ExecStart` 共 5 处）。

### VPS 运行时数据（不入库，必须随目录迁移）

```
/root/code/zcode2api/.env                         948 B   （含订阅 token）
/root/code/zcode2api/data/accounts.db            143 KB   （16 个账号）
/root/code/zcode2api/data/evidence.db            475 KB
/root/code/zcode2api/data/node_lifecycle.json    278 KB   （探活结果）
/root/code/zcode2api/data/egress_registry.json    99 KB   （800 槽位）
/root/code/zcode2api/data/plugin-auth.json         6 KB
/root/code/zcode2api/.venv                       153 MB   （shebang 已修）
/root/code/zcode2api/captcha_node/node_modules    25 MB
```

---

## 二、本次已完成的工作

### 1. 修复探活判据（commit `3382ca8`）

**问题**：`node_reachable()` 只拒 `effective >= 500`，而业务码 `3012` 被归一为
HTTP 405，落在兜底分支被判为"可达" → 一轮探活 143 个节点里 **124 个假 active**。

**修法**：改成三类判定，风控状态取自 `constants.RISK_CONTROL_HTTP_STATUSES`
（与网关路由、`egress_probe` 同源）：

- A 上游受理业务（200 / 402 / 403 / 429 / 404）→ 可达
- B 上游边缘拒绝（3012 / 归一后 405）→ 不可达
- C 上游或链路故障（5xx）→ 不可达

### 2. 修复探针账号选择（同一 commit）

**问题**：`probe_account_candidate()` 取"第一个 enabled 且有凭证"的账号，
而 `store.list_accounts` 把**已被风控**的账号排在首位 → 用它探任何出口都返回
3012（账号级拒绝，与出口无关）→ 整池误判。

**修法**：优先选 `status == 'active'`；无 active 时退回任意 enabled 且有凭证者。
`status` 用 `getattr` 读取，缺失按非 active 处理。

**效果**：探活从 `active=0` 恢复到 **`active=7`**（见下节实测）。

### 3. 消除硬编码机器路径（`2f3e2f8`）

- `app/settings.py` `BUN_PATH` 默认值 `/root/.bun/bin/bun` → `"bun"`（走 PATH）
- `scripts/plugin_preflight.py` 同样修改
- 8 个 `scripts/diagnostics/*.mjs` 的绝对路径 → 环境变量优先 + 位置反推
- `tests/unit/test_readloop_liveness.py` 删掉多余的 `sys.path.insert`

### 4. 文档补齐（`a666285` + `82489c6`）

- `.env.example` 补全至 47 个键（覆盖代码全部 `os.getenv` 调用）
- `docs/development/06-dev-guide.md` §1 补 `git clone --recursive`
- §5 新增「运行时依赖（Node / Bun / mihomo）」，讲清 systemd PATH 与 shell 不同的约束
- §5 补 `mihomo.service` 示例；修正过时端口示例（21081–21084 → 真实大区分段）

### 5. 清除真实账号标识（`b9fb6fa`）

扫描全仓发现 5 类真实标识共 12 处，替换为中性占位（保留形态以维持断言说明力）：

| 类别 | 替换为 | 处数 |
|---|---|---|
| 账号显示名（中文前缀 + 数字） | `账号A` / `账号B` | 6 |
| 账号显示名（含空格与大小写） | `账号C` | 4 |
| 账号邮箱 | `user@example.com` | 1 |
| 设备 UUID | `11111111-2222-3333-4444-555555555555` | 1 |
| 设备长标识 | `sample-long-id-88634403` | 1 |

**注意**：只做了工作树脱敏，**未重写历史**。旧提交仍可通过 SHA 检索到这些标识。

### 6. 重建仓库为「真 submodule」形态（`aa993eb`）

**背景**：仓库曾两次删库重建（原库为 public，含真实账号名）。最终 pin 到
GitHub private 仓库。

**关键修正**：`vendor/magpie-plugins` 此前被当**普通文件**提交
（mode `100644`，245 个文件），导致 `app/vendor_check.py` 两个方法都拿到父仓库身份：

- `check_plugin()` 的 `current` 取到父仓库 HEAD（`7134751`），与上游 plugins HEAD
  永远不等 → **每天假告警**
- `apply_plugin_update()` 在父仓库上下文执行 `git fetch` + `git reset --hard`
  → **会把整个项目重置到上游 plugins 的 HEAD**

**修法**：改为 `git submodule add`，`git ls-tree` 现为 `160000 commit 6a3fe9b4`。

**子模块 pin**：`6a3fe9b4`，已验证其 `packages/zcode/index.mjs` 的 blob sha
（`166e881c148950776b267dd05ad361758984e590`）与迁移前一致 → **零行为变化**。

**效果**：`check_plugin` 现返回 `current == remote`，`update_available: false` ✅

### 7. 纳入 openspec 与 CI（`9a6d971`）

- 从 `.gitignore` 移除 `openspec/`（该行由 `d2acbe1`（2026-10-07）引入，
  当时仓库为 public，故忽略内部变更记录；现为 private，顾虑不成立）
- 合并两个来源的 openspec 记录：archive **15 个** change（10-11 批次 8 个 +
  10-15 批次 7 个），未归档 **13 个**，共 228 文件
- 补回 `.github/workflows/ci.yml`（ruff + mypy strict scope + pytest
  `--cov-fail-under=50`）

---

## 三、当前实测状态

### 探活（2026-10-16 07:49 那轮）

```
节点状态:  stale 136 / active 7     （共 143）
槽位:      draining 134 / empty 659 / active 7   （共 800）
日志末尾:  phase=committed active=7
```

### vendor 检查（2026-10-16 05:22）

```
check_plugin: current = remote = 6a3fe9b4…   update_available: false  ✅
check_hostjs: local fb06d4ca… / remote 4bfe7881…  update_available: **true**
```

### 真实上游请求（迁移后验证）

```
账号 16 个，active 3
用 akbgpryk 测试：
  [直连]     HTTP 200
  [经 21200] HTTP 200
```

---

## 四、待办事项（未完成）

### 🔴 P1：`egress_registry.json` 的 `health` 字段始终为 `None`

```
实测: health 分布: {None: 800}
      admission:  {'draining': 134, 'empty': 659, 'active': 7}
```

**含义**：探活的健康结论写入了 `node_lifecycle.json`，但**没有回写到
`egress_registry.json` 的 `health` 维度**。需要查清：

- `health` 字段的设计意图是什么（哪段代码负责写它）
- 是设计如此（health 由别的路径维护），还是探活结果漏写
- 相关代码入口：`app/egress.py`、`app/egress_probe.py`、`scripts/mihomo_auto_probe.py`
  的 provider 生成段

**判断依据**：`admission` 有正确分布说明 registry 本身在工作，唯独 `health` 空缺。

### 🔴 P2：143 个节点里 136 个 `stale`（95% 不可用）

**现象**：全部对 zcode 返回 `status=405 outcome=3012`（或 `network_fail`）。

**已知根因**：机场出口 IP 被上游 ESA 边缘层拉黑
（证据：`server: ESA`、`{"code":3012,"msg":"request has been blocked due to unusual activity"}`）。

**已验证的对照实验**：

```
cloudflare .../trace  → cf-ray: ...-HKG / ...-TPE   （证明隧道通）
Google 204            → 20/20                        （证明节点本身健康）
zcode 请求             → 0/20                        （对 zcode 全被拒）
```

**结论**：**节点健康 ≠ zcode 可接受**。需要更换机场（IP 信誉问题，非配置问题）。

**当前唯二可用的出口**：`yydjc.top` 的 `pq-hinet.tw2.yydjc.top`（AS3462 Chunghwa
Telecom 住宅 IP，端口 21200），另有 7 个 active 槽位（探活筛出）。

### 🟡 P3：`host.js` 上游有更新（`update_available: true`）

**差异分析**（已完成）：

```
本地: 91452 B（正文 90861 B，2241 行），来源 commit 2076943…，fetched_at 2026-10-15
上游: 98612 B，2425 行
6 个 hunk，新增 187 行，删除 3 行
```

**差异内容**：单一主题 —— 上游新增「工具（tools）」能力：

```
withProviders() 增加 tools 字段
新增整节「tools」：toolsOf(spec) / jsonSchema(zod v3/v4 → JSON Schema) / toolList
用途：把插件的 OpenCode tool hook 暴露给 agent（经 magpie 的 tools MCP server）
```

**影响评估**：

- **纯增量**，未改动现有 `init` / `fetch` / `usage` 协议
- 本项目用的 `packages/zcode` **不导出 tool hook**（只做鉴权 / 额度 / 请求改写）
  → **此更新对本项目无功能影响**

**处置建议**：按 spec `REQ-VENDOR-04`（host.js 是协议规格，**严禁自动替换**）
保持"待人工确认"状态。**如需升级**：整文件替换 + 更新来源标注的 commit
+ 跑 plugin 通道回归。

### 🟡 P4：磁盘占用 93%

```
/dev/vda2  34G  已用 30G  剩 2.6G

可回收：
  /root/code/dengyie-zcode2api     241M   旧目录（回滚备份，稳定后可删）
  /root/zcode2api-runtime-stage    219M   迁移时的运行时备份
  /tmp/dengyie-zcode2api-full.tar.gz  14M
  /tmp/ref-zcode.log               11.8M  18089 实例的遗留日志
  ─────────────────────────────────────
  合计约 485M
```

**注意**：删除旧目录前请确认新部署已稳定运行若干天。

### 🟡 P5：旧目录 `/root/code/dengyie-zcode2api` 有 18 项未提交改动

```
18 × D openspec/changes/{两个目录}/...   （归档后本地删除的残留）
 1 × m vendor/magpie-plugins            （submodule 状态差异）
```

这些是好几个版本前遗留的脏状态，**无同步价值**（archive 副本已完整进新仓库）。
删除旧目录即可一并消除。

### 🟢 P6：openspec 里有真实标识（仓库已 private，暂不处理）

```
扫描 openspec/ 发现 45 处：
  VPS 公网 IP          15 处
  真实账号名（两种）    13 处
  真实邮箱             13 处
  /root/code/...       16 处（机器路径）
```

**当前决定**：仓库为 private，**不脱敏，保留原始记录可读性**。

**若将来需要公开 / 加协作者 / 转 fork**：这些需先处理，且**部分（如
`clarify-gateway-unavailability-reasons/` 的部署日志）是首次提交就带入的，
届时需要重写历史**。

---

## 五、关键约束与踩过的坑

### 1. systemd 的 PATH ≠ 登录 shell 的 PATH

```
服务进程实际 PATH = /usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/snap/bin

  node  → /usr/local/bin/node    ✅（且是指向 nvm 版本的**符号链接**，同一文件）
  bun   → **找不到**             ❌

→ 结论：ZCODE_BUN_PATH 必须设绝对路径（删了插件通道会崩）
        ZCODE_NODE_PATH 可省（默认 "node" 能解析到同一文件）
```

核对方法：`systemctl show <服务名> -p Environment` +
`tr '\0' '\n' < /proc/<MainPID>/environ`。

### 2. `.venv` 跨路径迁移要修 shebang

`cp -a` 复制 `.venv` 后，`bin/` 下 24 个脚本（pytest / uvicorn / fastapi /
httpx / coverage…）的 shebang 仍指向旧路径。必须批量替换：

```bash
grep -rl "<旧路径>" .venv/bin/ | while read -r f; do
  sed -i "s|<旧路径>|<新路径>|g" "$f"
done
```

`python -m venv --upgrade` **修不了这个**（实测残留 24 个）。

### 3. PowerShell `Copy-Item -Recurse -Force` 是**合并**不是覆盖

源目录被整个塞进目标下（产生 `openspec/openspec/`），删嵌套层时可能连带删掉
本该保留的内容。**跨目录同步建议用 `git` 或明确的文件级复制**。

### 4. 提交前必须做的检查

```bash
git status --short                    # 确认无被忽略项混入
git ls-files -s vendor/magpie-plugins # 应为 160000（submodule）
git submodule status                  # 子模块是否检出
```

### 5. 本机 `data/accounts.db` 是空的

真实账号数据**只在 VPS**。做隐私比对时，比对源必须从 VPS 的 DB 提取
（`accounts.data` 是 JSON 列，真值在里面，需递归展开）。

---

## 六、常用命令

### 本机 → 推送

```powershell
cd D:\Temp\lizi\zcode2api
git status --short
git add -A
git commit -m "<type>(<scope>): <描述>"
git push origin main
```

### VPS → 同步 + 重启

```bash
cd /root/code/zcode2api
git pull --ff-only origin main
git submodule update --init --recursive    # 若子模块有变
systemctl restart zcode2api                 # 业务改动才需重启
```

### 验证

```bash
systemctl is-active zcode2api mihomo mihomo-probe-updater.timer
curl -s http://127.0.0.1:18088/meta
journalctl -u zcode2api -n 30 --no-pager
journalctl -u mihomo-probe-updater -n 30 --no-pager   # 探活日志
```

### 手动跑一轮探活

```bash
cd /root/code/zcode2api
.venv/bin/python scripts/mihomo_auto_probe.py
```

### 插件通道自检

```bash
.venv/bin/python scripts/plugin_preflight.py
```

---

## 七、仓库结构速查

```
zcode2api/
├── app/
│   ├── channels/          plugin 通道（host.js RPC + 凭证派生）
│   ├── routes/gateway.py  上游请求 / 账号切换 / 错误归一化（_classify_business_error）
│   ├── client_pool.py     账号→出口绑定（一致性哈希）
│   ├── egress_probe.py    出口活性探针 + 断路器
│   ├── vendor_check.py    vendor 层版本监控（只告警，不自动应用）
│   └── settings.py        全部环境变量（含默认值）
├── scripts/
│   ├── mihomo_auto_probe.py   订阅拉取 → 探活 → 生成 mihomo 配置（64 KB，核心）
│   ├── plugin_preflight.py    bun / host.js / 插件目录自检
│   └── diagnostics/*.mjs      8 个历史诊断脚本（2026-10-14 根因判定证据链）
├── vendor/
│   ├── magpie-plugins/        **真 submodule** → pin 6a3fe9b4
│   └── magpie_host.js         单文件复制 + 来源标注（协议规格，人工升级）
├── openspec/
│   ├── changes/archive/       15 个已归档 change
│   ├── changes/               13 个未归档 change
│   └── specs/                 从 archive 提升的 spec
├── docs/
│   ├── development/           架构 / 移植 / 数据格式 / API / 上游协议 / 开发指南 / 路线图
│   ├── adr/0001-vendor-official-zcode-plugin.md
│   └── gateway_retry_controlflow.md   gateway 重试链路只读梳理（848 行）
└── tests/                     单元 + 集成（含 mock 上游）
```

---

## 八、外部依赖清单

| 依赖 | 位置 | 版本 | 能否内联 |
|---|---|---|---|
| **mihomo** | `/usr/local/bin/mihomo` | v1.19.1 | ❌ 30 MB Go 二进制，需外部装 |
| **bun** | `/root/.bun/bin/bun` | 1.4.3 | ❌ 运行时，需外部装 |
| **node** | `/root/.nvm/versions/node/v24.19.0/bin/node` | v24.19.0 | ❌ 运行时（`/usr/local/bin/node` 是它的符号链接） |
| **Python** | `/usr/bin/python3.12` | 3.12.3 | ✅ venv 在项目内 |
| **magpie-plugins** | `vendor/magpie-plugins` | pin 6a3fe9b4 | ✅ submodule |
| **magpie_host.js** | `vendor/magpie_host.js` | commit 2076943… | ✅ 单文件 vendored |

---

## 九、换机器部署清单

```bash
# 1. 依赖
#    Python 3.12+ / Node 20+（验证码求解）/ Bun（插件通道）/ mihomo（出口中继）

# 2. 拉代码（--recursive 必需）
git clone --recursive <repo> && cd zcode2api
#    漏了子模块时：git submodule update --init --recursive

# 3. 虚拟环境
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cd captcha_node && npm ci && cd ..

# 4. 配置
cp .env.example .env
#    必填：ZCODE_ADMIN_KEY / AIRPORT_SUB_URLS / ZCODE_BUN_PATH（若 bun 不在服务 PATH）
#    数据：从旧机器拷 data/（accounts.db / evidence.db / node_lifecycle.json /
#          egress_registry.json / plugin-auth.json）

# 5. 自检
.venv/bin/python scripts/plugin_preflight.py

# 6. systemd
#    按 docs/development/06-dev-guide.md §5 的示例，替换 <PROJECT_DIR>
#    zcode2api.service / mihomo.service / mihomo-probe-updater.{service,timer}
systemctl daemon-reload && systemctl enable --now zcode2api mihomo mihomo-probe-updater.timer

# 7. 验证
curl -s http://127.0.0.1:18088/meta
```

---

## 十、接手者第一件该看的事

1. **读 `openspec/changes/` 下的未归档 change**（13 个）—— 那里有进行中的设计意图
2. **看 `docs/development/06-dev-guide.md`** —— 部署与运维约定都在这
3. **跑一次探活**（`scripts/mihomo_auto_probe.py`）—— 确认出口池现状
4. **查 P1（`health` 字段全 None）** —— 这是当前最明确的代码缺陷
