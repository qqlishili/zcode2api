# Spec: plugin-channel-runtime

## 1. 契约与范围

定义 plugin 通道的运行时组件 —— **host.js 常驻宿主**的 Python 客户端生命周期与 RPC 契约。
范围：`app/channels/plugin_channel.py`、`app/channels/base.py`、`app/main.py` 的 lifespan 挂载。

---

## 2. 协议事实（官方源码，非推测）

`magpie/internal/plugin/host.js` 头部注释原文：

```
// magpie → host: {id, method, params}; the host answers {id, result} or
// {id, error}. A fetch streams its reply first: {id, event: "head", status,
// headers}, then {id, event: "chunk", data} (base64) as the body comes, then
// {id, result: null}. {method: "abort", params: {id}} cancels one, and
// {method: "credit", params: {id, n}} gives a fetch back the n charged bytes
// its reader took.
//
// host → magpie, unasked: {event: "log", level, message} and
// {event: "toast", ...}, {event: "auth", provider}
```

**本方案只依赖三个 RPC 方法**：`init`、`fetch`、`usage`。

---

## 3. 需求与场景契约

### REQ-PLUG-01: 启动与 init
- 以 `ZCODE_BUN_PATH` 启动 `vendor/magpie_host.js`；stdin/stdout 管道；stderr 继承（便于诊断）。
- **init 参数（实测正确形态）**：
  ```python
  {
    "authPath": str(plugin_auth_path),
    "modelsDevPath": "",
    "piPath": "",
    "directory": str(work_dir),
    "config": {}, "prefer": {}, "options": {},
    "plugins": [{"spec": "@magpie-community/opencode-zcode-auth",
                 "target": str(vendor_zcode_dir)}],
  }
  ```
- **场景 1（成功）**：`plugins[].provides` 含 `"zcode"` → 标记通道健康。
- **场景 2（加载失败）**：`plugins[].error` 非空 → 标记不健康，降级 native，写 evidence（含错误原文）。
- **场景 3（超时）**：init 超时 **5 s** → 视为失败并降级。
- **实测基线**：16 槽位 init **70 ms**。

### REQ-PLUG-02: fetch 参数完整性
- 必须传齐：`provider` / `account` / **`model`** / **`npm`** / `url` / `method` / `headers` / `body`(bytes) / `window` / `proxy`。
- **场景 1（缺 model/npm）**：上游返回 `400 {"code":3001,"msg":"parameter error"}`。
  **严禁**把 `3001` 当作 `3012` 处理（语义不同：3001 是参数错，3012 是风控）。
- **场景 2（proxy）**：代理时传 URL；**直连时必须传 `"direct"`**。

### REQ-PLUG-03: 流式重组
- **场景 1（正常流）**：`head`（记 status/headers）→ `chunk`（base64 解码并 yield）→ `result:null`（结束）。
- **场景 2（未知 event）**：必须**宽松忽略**（向前兼容），不得抛错。
- **场景 3（httpx 兼容）**：必须用 **`httpx.AsyncByteStream`（httpx 公开 API）** 包装，产出可供 `httpx.Response(stream=...)` 消费的流。

### REQ-PLUG-04: credit 流控回补
- **场景 1（消费即回补）**：每 yield 一块，必须发 `{method:"credit", params:{id, n}}`（n = 该块未经 base64 的长度）。
- **理由**：官方设计 —— 不回补会导致大响应被上游暂停（本项目的典型场景是 1M 上下文编码任务）。

### REQ-PLUG-05: 请求 ID 隔离与并发
- 每条 RPC 独立自增 `id`；响应按 `id` 路由。
- **场景 1（并发）**：同时 N 路 fetch，各自归入正确调用方（**实测 32 并发全并行**，RPC 非瓶颈）。
- **场景 2（无 id 的 host 主动事件）**：`{event:"log"/"toast"/"auth"}` 必须作为日志处理，不得误配到某个调用。

### REQ-PLUG-06: abort 与超时
- **场景 1（客户端断开）**：必须发送 `{method:"abort", params:{id}}` 释放 host 侧资源。

### REQ-PLUG-07: 就绪探测
- **场景 1（每请求探测）**：每次 `send()` 前检查 `is_alive()`（`proc.returncode is None`）。
- **场景 2（不健康即降级）**：不健康时**不尝试**发送，直接走 `native`。
- **禁止**引入心跳定时器（零成本探测即可）。

### REQ-PLUG-08: 崩溃恢复
- **场景 1（进程死亡/卡死）**：重启 host 子进程，**最多连续 3 次**。
- **场景 2（超限）**：标记不健康 + 降级 native + Bark 告警，**冷却 5 分钟后**再试。
- **场景 3（Bun 二进制本身不可用）**：**无法重启** → 直接降级 native + 告警。

### REQ-PLUG-09: 生命周期挂载
- **启动顺序**（`main.py` lifespan）：
  ```
  captcha_manager.start() → sentinel.start() → plugin_host.start()
  ```
- **关闭顺序**（finally，反向）：
  ```
  account_client_pool.stop_coordinator() → plugin_host.aclose() → captcha_manager.close()
  ```
- **理由**：启动时 host 早于请求；关闭时先停请求再停宿主。

### REQ-PLUG-10: 优雅关闭
- **场景 1**：`aclose()` 关闭 stdin（触发 host 的 `rl.on("close")`）→ 等进程退出 → 超时 SIGKILL 兜底。
- **场景 2**：必须无残留子进程。

### REQ-PLUG-11: 凭证文件
- 路径 `data/plugin-auth.json`；**权限 600**；**原子替换**（`os.replace`）。
- **场景 1（权限）**：文件创建/重写后 mode 必须为 600。
- **场景 2（原子性）**：并发写入不得产生半截文件。

### REQ-PLUG-12: JWT 不入日志
- **场景 1**：全文 JWT **严禁**出现在任何日志 / evidence / 异常消息中；只允许前 8 字符。

---

## 4. 验收标准

| 编号 | 验收 |
|---|---|
| AC-P1 | `init` 后 `provides` 含 `zcode`；16 槽位 init < 500 ms |
| AC-P2 | 缺 `model` → 400/3001；补齐 → 200 |
| AC-P3 | 并发 3 路 fetch 各自拿到正确响应 |
| AC-P4 | 未知 event 不导致异常 |
| AC-P5 | `aclose` 后 `ps` 无残留 bun 进程 |
| AC-P6 | 杀掉 host 进程 → 下次请求自动走 native；重启成功后可回到 plugin |
| AC-P7 | 日志中不含完整 JWT |
| AC-P8 | `plugin-auth.json` 权限 600 |
| AC-P9 | 流式响应逐块 yield（首字节不等待全部收完） |
