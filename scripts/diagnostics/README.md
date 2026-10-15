# 诊断工具

这些脚本是 **2026-10-14「手写请求全 3012」根因判定**的可复现证据链。
不属于运行时代码，只在排查与回归验证时手动执行。

## 执行前提

- Bun 在 PATH 中（或用 `ZCODE_BUN_PATH` 指定）
- 项目内 `vendor/magpie_host.js` 与 `vendor/magpie-plugins/`（git 子模块）——
  与生产（`app/settings.py` 的 `PLUGIN_HOST_JS` / `PLUGIN_DIR`）用同一份副本；
  子模块未拉取时先 `git submodule update --init`。
  需要对着上游仓库调试时，用 `MAGPIE_HOST_JS` / `MAGPIE_PLUGIN_DIR` 覆盖。
- `/tmp/acc.json` 含 `{jwt, device_mid, install_id}`（由 `dump_acc.py` 生成）

## 脚本清单

| 脚本 | 判定的问题 |
|---|---|
| `dump_acc.py` | 从 accounts.db 导出 JWT/device 到 /tmp/acc.json |
| `probe_host_rpc.mjs` | host.js 能否独立运行（init + fetch 打通）→ 证明不需要 magpie Go 二进制 |
| `probe_dress_effect.mjs` | 有/无 dress 的对比 → 证明 dress 是必需条件 |
| `probe_slot_naming.mjs` | 5 种槽位命名形态是否可用 → 决定槽位命名方案 |
| `probe_auth_reload.mjs` | 改 auth.json 后不重启是否生效 → 决定写入策略 |
| `probe_trial_model.mjs` | 传 -Trial 与不传的行为差异 → 决定 model 透传策略 |
| `probe_usage_shape.mjs` | usage RPC 的返回结构 → 决定旁路对照的可行性 |
| `probe_concurrency_limit.mjs` | 上游单账号并发上限 → 得出「上限 = 3」 |
| `probe_init_cost.mjs` | 16 槽位 init 耗时与内存 → 决定同步 init |

## 用法

在项目根目录执行（路径随部署位置而定）：

```
bun scripts/diagnostics/probe_host_rpc.mjs
```

## 关键结论索引

| 结论 | 由哪个脚本得出 |
|---|---|
| dress 是必需条件（有 → 200，无 → 3012） | `probe_dress_effect.mjs` |
| 代理节点已被 ESA 拉黑（走代理 3012，直连 200） | `probe_dress_effect.mjs` |
| 上游单账号并发 = 3（并发 4 起 429 code:3008） | `probe_concurrency_limit.mjs` |
| 改 auth.json 必须重启进程 | `probe_auth_reload.mjs` |
| 16 槽位 init = 70 ms / RSS 29–54 MB | `probe_init_cost.mjs` |
| 槽位名可用中文 / UUID / 长英文 / 纯数字 | `probe_slot_naming.mjs` |
| -Trial 可走活动额度，响应 model 被归一 | `probe_trial_model.mjs` |
