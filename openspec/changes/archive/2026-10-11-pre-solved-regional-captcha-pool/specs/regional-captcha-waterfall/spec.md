# Spec Delta: regional-captcha-waterfall

## Purpose

本规范定义基于代理出口物理亲和的多轨预解水库、三态断路器（CLOSED/OPEN/HALF-OPEN）、Singleflight 单飞合并与活动错峰门禁契约，彻底消除热路径求解延迟、跨出口 Token 毒化、3007 全局株连及多账号领额度 IP 连坐风险。

## ADDED Requirements

### Requirement: Proxy-Affinity Regional Captcha Pre-solved Reservoir
The system MUST maintain distinct in-memory pre-solved token queues partitioned by individual egress proxy (`(region, proxy_url)`), continuously refilled by background workers using the exact proxy assigned to that queue, ensuring \(O(1)\) token delivery while strictly guaranteeing that the solving egress IP matches the request outbound IP.

#### Scenario: Sub-millisecond Token Delivery from Matching Proxy Reservoir
- **WHEN** a request arrives for an account whose assigned proxy is `http://127.0.0.1:21200`
- **AND** the reservoir for `21200` contains a valid, unexpired token
- **THEN** the system MUST immediately pop and return that token without spawning Node.js child processes
- **AND** the system MUST trigger an asynchronous background task to replenish `21200`'s reservoir.

#### Scenario: Preventing Cross-Proxy Token Poisoning
- **WHEN** account `A` is mapped to proxy `21200` and proxy `21201` has 3 pre-solved tokens while `21200` is empty
- **THEN** the system MUST NOT assign tokens from `21201` to account `A`
- **AND** the system MUST resolve a fresh token specifically through `21200` to prevent Alibaba WAF Remote IP mismatch rejections.

### Requirement: Singleflight Empty-Take Consolidation
When a burst of concurrent requests targets an empty proxy reservoir, the system MUST consolidate those concurrent requests into a single in-flight solving operation, broadcasting the resulting token to the active requests while banking secondary tokens into the queue.

#### Scenario: Merging Concurrent Solves on Empty Queue
- **WHEN** 3 concurrent requests arrive simultaneously for proxy `21100` whose reservoir is empty
- **THEN** the system MUST initiate exactly ONE Node.js solve task for `21100`
- **AND** all 3 requests MUST await the completion of this single in-flight task without launching redundant solver processes.

### Requirement: Three-State Node Circuit Breaker (CLOSED / OPEN / HALF-OPEN)
The system MUST implement a three-state circuit breaker per proxy port in `AccountClientPool`. The breaker tracks consecutive failures (distinguishing network timeouts from explicit WAF bans), transitions to `OPEN` (quarantine for 600s), and tests recovery via a single `HALF-OPEN` probe.

#### Scenario: WAF Ban Rapid Tripping
- **WHEN** proxy port `21102` receives 2 consecutive Alibaba WAF `F001` or `3012` ban responses
- **THEN** the breaker MUST immediately transition `21102` to `OPEN` for 600 seconds
- **AND** `AccountClientPool` MUST bypass `21102` during request routing.

#### Scenario: Half-Open Probe Recovery
- **WHEN** proxy port `21102` has been in `OPEN` for 600 seconds
- **THEN** the breaker MUST transition to `HALF-OPEN`, allowing exactly one probe request through
- **AND** if the probe succeeds, the breaker MUST transition to `CLOSED` and clear all failure counts
- **AND** if the probe fails, the breaker MUST revert to `OPEN` and reset the 600-second quarantine.

### Requirement: Target-Scoped Pool Invalidation (Blast Radius Control)
When an upstream 3007 challenge occurs, the system MUST invalidate tokens exclusively for the affected proxy or region, strictly preventing cross-region or global token purging.

#### Scenario: Scoped Invalidation on 3007 Challenge
- **WHEN** a request on proxy `http://127.0.0.1:21100` (HK) receives an upstream 3007 challenge
- **THEN** the system MUST invalidate tokens for proxy `21100` or region `HK` only
- **AND** pre-solved tokens for other regions (`TW`, `JP`, `SG`, `US`) MUST remain untouched and valid in memory.

### Requirement: Staggered Pacing Gate with Fail-Fast Timeout for Activity Claims
The system MUST enforce a serialized pacing interval (default 20 seconds) per proxy port during daily plan claiming, and MUST reject queued requests that exceed a maximum wait timeout (default 45 seconds) to prevent client-side HTTP 499 read timeouts.

#### Scenario: Serializing Concurrent Claims with Fail-Fast
- **WHEN** 4 accounts sharing proxy `21100` attempt to claim daily plans at the same time
- **THEN** account 1 MUST execute immediately
- **AND** account 2 MUST wait 20 seconds after account 1 completes
- **AND** account 4 (whose estimated queue wait is 60 seconds, exceeding the 45-second ceiling) MUST immediately fail-fast with a retry advice error without hanging the connection.

### Requirement: Adaptive CPU Concurrency Guard
The system MUST dynamically calculate solver concurrency limits based on host CPU core count (1 on single-core systems, `max(1, cpu_count - 1)` on multi-core systems) and enforce acquisition timeouts to prevent solver deadlocks.

#### Scenario: Single-Core VPS Concurrency Clamping
- **WHEN** running on a host with 1 CPU core
- **THEN** the global solver semaphore MUST limit active Node.js processes to strictly 1, preventing CPU exhaustion and preserving event-loop responsiveness.
