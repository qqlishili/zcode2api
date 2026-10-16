# 架构与详细设计：多轨代理预解水库与三态断路器 (pre-solved-regional-captcha-pool)

## 1. 架构总览与链路拓扑

```
[ 用户请求到达 Gateway (/v1/messages) ]
                   │
                   ▼ (1) 账号出口解析 resolve_proxy(account)
           [ 物理代理出口 (Proxy Port) ]
                   │
                   ▼ (2) 尝试从代理专属水库弹出 Token (O(1))
       ┌───────────┴───────────┐
  [ 水库命中 ]            [ 水库空仓 ]
       │                       │
       ▼ (耗时 0ms)            ▼ (3) Singleflight 单飞防惊群
 立即附带 Header 发往上游    并发请求挂起，等待单任务求解并广播
                               │
                               ▼ (4) 成功：响应首发并异步补仓
                               ▼ (5) 失败：触发大区换号避让
```

---

## 2. 核心模块重构与详细契约

### 2.1 预解水库存储与亲和性（`app/captcha.py`）
- **数据结构升级**：
  由单纯的大区字典重构成**代理亲和队列**，并采用 `collections.deque` 保证 O(1) 的头部时间轮淘汰：
  ```python
  class TokenEntry:
      __slots__ = ("param", "region", "proxy", "born_at", "certify_id")

  class RegionalCaptchaManager:
      def __init__(self):
          # proxy_url -> deque[TokenEntry] (按生成时间保序)
          self._proxy_pools: dict[str, deque[TokenEntry]] = defaultdict(deque)
          # proxy_url -> asyncio.Future (Singleflight 单飞现解任务)
          self._in_flight_solves: dict[str, asyncio.Future] = {}
          # certifyId 去重注册表：certify_id -> timestamp (TTL 120s 自动剪枝)
          self._seen_certify_ids: dict[str, float] = {}
          # CPU 自适应并发信号量：单核 VPS 钳制为 1，多核为 max(1, os.cpu_count() - 1)
          self._cpu_semaphore: asyncio.Semaphore = None
  ```
- **Token 出池算法**：
  1. 根据 `account_client_pool.resolve_proxy(account)` 获取目标代理 `target_proxy`；
  2. 检查 `self._proxy_pools[target_proxy]` 队头元素是否过期（`born_at + TTL < now`），原地 `popleft()` 丢弃；
  3. 若有未过期有效 Token，直接弹出并异步触发补货；
  4. 若队列为空，进入 **Singleflight**：
     - 若当前代理已存在 `_in_flight_solves[target_proxy]`，则所有并发请求直接 `await future`，合并为一次求解；
     - 否则创建 Future 并派发单个 Node 求解任务；求解成功后广播给所有等待请求，并将次生 Token 压入水库。

### 2.2 靶向定向清池（Scoped Invalidation）
- 废除原有的全局暴力清空，定义精准的失效扩散边界：
  ```python
  def invalidate(self, region: str | None = None, proxy: str | None = None) -> None:
      """靶向清空失效 Token，严禁跨大区株连。
      - 若指定 proxy：仅清空该代理出口的水库；
      - 若指定 region：清空属于该大区所有代理出口的水库；
      - 仅在未指定任何参数且明确全局配置重置时才清空全库。
      """
  ```

### 2.3 代理节点三态断路器（收敛至 `app/client_pool.py`）
断路器作为出网层核心资产，由 `AccountClientPool` 统管，提供 Single Source of Truth：

```
       [ 正常通信 / 求解成功 ]
                 │
                 ▼
        ┌─────────────────┐
        │     CLOSED      │ ◄────────────────────────┐
        └─────────────────┘                          │
                 │ 连续失败达到阈值                    │ 单次探针成功
                 ▼                                   │
        ┌─────────────────┐                 ┌─────────────────┐
        │      OPEN       │ ── 冷却 600s ──▶ │    HALF-OPEN    │
        │  (Quarantine)   │                 │   (单次探针放行)  │
        └─────────────────┘                 └─────────────────┘
                 ▲                                   │
                 └────────── 单次探针失败 ────────────┘
```

- **失败分类处理**：
  - **网络瞬态抖动**（`TimeoutError`, `ConnectError`）：连续失败 3 次才触发熔断；
  - **WAF 确定性封控**（阿里返回 `F001`, `3012` 或 HTTP 403 明确封 IP 报文）：连续 2 次立即触发熔断。
- **状态感知契约**：
  `client_pool.resolve_proxy(account)` 自动过滤处于 `OPEN` 状态的端口。
  若端口处于 `HALF-OPEN`，仅允许 1 个请求作为探针通行。探针成功则重置失败计数切回 `CLOSED`；失败则重新进入 `OPEN` 并重新冷却 600 秒。

### 2.4 活动领取错峰串行门禁（`app/claim.py`）
- **多账号同节点防连坐**：
  ```python
  class ClaimPacingGate:
      def __init__(self, min_interval_sec: float = 20.0, max_wait_sec: float = 45.0):
          self._proxy_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
          self._proxy_last_claim: dict[str, float] = {}
          self._min_interval = min_interval_sec
          self._max_wait = max_wait_sec
  ```
- **门禁执行契约**：
  1. 每个代理端口分配独立的 `asyncio.Lock`；
  2. 请求尝试获取对应代理锁，超时时间为 `max_wait_sec`（45 秒）。若超时未获取到锁，**立即抛出 `ClaimPacingQueueFullError` 快速失败**，避免客户端 499 挂死；
  3. 获取到锁后，计算 `elapsed = now - last_claim`。若 `elapsed < min_interval`，当前协程休眠 `min_interval - elapsed` 秒后放行；
  4. 领额度全程复用该代理水库中预解的 Token，禁止在领额度阶段同秒唤起 Node 进程。

### 2.5 CPU 资源防护与常驻进程
- **动态并发控制**：
  ```python
  def get_cpu_concurrency_limit() -> int:
      cores = os.cpu_count() or 1
      return 1 if cores <= 1 else max(1, cores - 1)
  ```
- 求解前必须 `asyncio.wait_for(semaphore.acquire(), timeout=10.0)`，求解完毕确保在 `finally` 释放，防止因 Node 僵死导致信号量泄漏。

---

## 3. 风险、故障边界与二阶推论

1. **“账号增加”与“节点扩容”的刚性物理绑定**：
   - 增加账号不能作为节点冷冻的替代方案；
   - 必须保持“住宅出口节点数 ≥ 账号数”；若账号增加，必须同比例在 `config.yaml` 扩充百位代理端口。
2. **内存剪枝（Prune）防泄漏**：
   - 后台守护协程每 30 秒扫描一次 `_seen_certify_ids`，删除生存时间超过 120 秒的历史记录，控制内存常驻在数 MB 以内。
