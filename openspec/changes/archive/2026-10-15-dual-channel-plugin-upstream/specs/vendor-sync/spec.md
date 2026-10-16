# Spec: vendor-sync

## 1. 契约与范围

定义 `vendor/` 层的内容、来源标注、版本监控与更新策略。
范围：`vendor/`、`.gitmodules`、`scripts/plugin_update.py`。

---

## 2. 内容与实测依据

| 项 | 来源 | 体积 | 引入方式 |
|---|---|---|---|
| `vendor/magpie-plugins/` | `github.com/magpie-community/plugins` | 3.2 MB（去 .git） | **git submodule** |
| `vendor/magpie_host.js` | `github.com/yetone/magpie` 的 `internal/plugin/host.js` | 89 KB | **复制单文件 + 来源标注** |

**为何 host.js 不 submodule**：
- magpie 全仓 **257 MB**（`site/` 60 MB 站点资源、`.git` 148 MB），为单文件引入整仓不成比例；
- host.js 属**协议规格**，不能独立更新，变更需回归测试 → 必须人工确认。

**复用边界**：**只用 `packages/zcode`**；`magpie-plugins` 其余包不加载、不引用。

---

## 3. 需求与场景契约

### REQ-VENDOR-01: submodule 引入
- **场景 1（初始化）**：全新克隆后 `git submodule update --init --recursive` → `vendor/magpie-plugins/packages/zcode/index.mjs` 必须存在。
- **场景 2（版本锁定）**：主仓库必须记录 submodule 的 commit sha（`git ls-tree HEAD vendor/`）。

### REQ-VENDOR-02: host.js 来源标注
- **文件头必备字段**：
  ```
  vendored from yetone/magpie
  path: internal/plugin/host.js
  commit: <40 位 sha>
  fetched_at: <ISO 日期>
  更新方式：人工确认（协议规格；替换前需跑 plugin 通道回归）
  ```
- **场景 1（可审计）**：任何人可凭标注定位上游确切版本。

### REQ-VENDOR-03: 插件自动更新（每日）
- **场景 1（有新版本）**：
  - `origin/main` 领先本地 submodule 时，记录前后 commit 到 evidence；
  - `ZCODE_PLUGIN_AUTOUPDATE=1` 时执行 `git submodule update --remote` + **重启 host**；
  - `=0` 时仅告警。
- **场景 2（更新后回归失败）**：reload 后 smoke test 失败 → **回滚 submodule 到上一个 commit** + 再次重启 + 记录证据。

### REQ-VENDOR-04: host.js 版本监控（每周，仅告警）
- **场景 1（有差异）**：比对上游 sha256，差异时经 Bark 通知告警 + 管理页标记待确认。
- **严禁自动替换。**

### REQ-VENDOR-05: 依赖可用性预检
- **场景 1（缺失）**：`ZCODE_BUN_PATH` 不可执行 / `vendor/magpie_host.js` 不存在 / `vendor/magpie-plugins/packages/zcode` 不存在 时：
  - 告警 + plugin 通道标记不可用；
  - `ZCODE_REQUEST_CHANNEL=plugin` 时**降级 native**，**不得启动失败**。

### REQ-VENDOR-06: 不污染主仓库
- **场景 1（洁净）**：submodule 内 `git status` 必须 clean。
- **严禁**在 `vendor/magpie-plugins` 内安装依赖（`node_modules` 等）。

---

## 4. 非功能需求

### REQ-VENDOR-NF-01: 体积上限
- `vendor/` 总体积（含 submodule 工作树）**< 10 MB**。

### REQ-VENDOR-NF-02: 运行期离线
- 已初始化后，运行期**不依赖网络**获取插件（仅更新检查时需要）。

### REQ-VENDOR-NF-03: 可回滚
- 任何 vendor 更新都可一条命令回滚（`git submodule update --init` + 指定 sha）。

---

## 5. 验收标准

| 编号 | 验收 |
|---|---|
| AC-V1 | `git submodule status` 显示正确 sha |
| AC-V2 | `vendor/magpie_host.js` 头部含完整来源标注 |
| AC-V3 | 检查任务无新版本时不产生副作用 |
| AC-V4 | 模拟更新失败 → 自动回滚且重启成功 |
| AC-V5 | Bun 缺失时服务仍能启动（走 native） |
| AC-V6 | `du -sh vendor/` < 10 MB |
| AC-V7 | submodule 内 `git status` 干净 |
