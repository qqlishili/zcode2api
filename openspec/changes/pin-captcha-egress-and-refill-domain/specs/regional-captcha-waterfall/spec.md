# Spec Delta

## MODIFIED Requirements

### Requirement: Proxy-Affinity Regional Captcha Pre-solved Reservoir

The system MUST maintain distinct in-memory pre-solved token queues partitioned by individual egress proxy (`(region, proxy_url)`), continuously refilled by background workers using the exact proxy assigned to that queue, ensuring \(O(1)\) token delivery while strictly guaranteeing that the solving egress IP matches the request outbound IP. The refill domain MUST be derived from the egress proxies actually bound to currently serviceable accounts: a region or port without a serviceable account MUST NOT be warmed. The per-port watermark MUST default to exactly one live token, because the sustainable rate of one port (`watermark / TTL`) MUST stay below the measured per-port solve pressure threshold.

#### Scenario: Sub-millisecond Token Delivery from Matching Proxy Reservoir
- **WHEN** a request arrives for an account whose assigned proxy is `http://127.0.0.1:21200`
- **AND** the reservoir for `21200` contains a valid, unexpired token
- **THEN** the system MUST immediately pop and return that token without spawning Node.js child processes
- **AND** the system MUST trigger an asynchronous background task to replenish `21200`'s reservoir

#### Scenario: Preventing Cross-Proxy Token Poisoning
- **WHEN** account `A` is mapped to proxy `21200` and proxy `21201` has 3 pre-solved tokens while `21200` is empty
- **THEN** the system MUST NOT assign tokens from `21201` to account `A`
- **AND** the system MUST resolve a fresh token specifically through `21200` to prevent Alibaba WAF Remote IP mismatch rejections

#### Scenario: Refill Domain Follows Serviceable Accounts
- **WHEN** the account pool contains serviceable Plan accounts bound to ports `21301`, `21303` and `21203`, while other healthy regions have no serviceable account
- **THEN** the background refill MUST target only `21301`, `21303` and `21203`
- **AND** it MUST NOT warm ports of regions that currently hold no serviceable account, even when those ports are healthy

#### Scenario: No Serviceable Account Stops Warming
- **WHEN** every account is exhausted, cooling, invalid or disabled
- **THEN** the refill loop MUST only evict expired tokens and MUST NOT start any solve
- **AND** it MUST resume warming automatically once a serviceable account appears again

#### Scenario: Per-Port Watermark Bounds Production Rate
- **WHEN** a port's reservoir already holds the configured watermark of live tokens
- **THEN** no further solve MUST be scheduled for that port until a token is consumed or expires
- **AND** the default watermark MUST be one token per port

## ADDED Requirements

### Requirement: Stable Egress Binding with Ring Failover

`AccountClientPool.resolve_proxy` MUST bind an account to a stable port inside its assigned region. The binding index MUST be derived from the full registered port list of that region, never from the currently healthy subset, so that a quarantine or registry refresh MUST NOT silently re-assign an account to a different egress IP. When the bound port is unavailable, the resolver MUST walk the region's registered ports in a fixed order starting at the bound port and return the first eligible, non-quarantined port; when the bound port recovers, resolution MUST return to it.

#### Scenario: Binding Survives Quarantine
- **WHEN** an account is bound to port `21203` and `21203` becomes quarantined
- **THEN** resolution MUST fall over to another eligible port of the same region in a deterministic order
- **AND** after the quarantine is released, resolution MUST return to `21203`

#### Scenario: Healthy Subset Changes Do Not Move The Binding
- **WHEN** the number of healthy ports in a region changes while the bound port stays eligible
- **THEN** the account MUST keep resolving to the same port
- **AND** pre-solved tokens for that port MUST remain usable

#### Scenario: Region Without Eligible Egress Never Hops
- **WHEN** every port of the account's assigned region is unavailable
- **THEN** resolution MUST return no proxy so the existing region-offline fence switches accounts
- **AND** the resolver MUST NOT borrow a port from another region or fall back to direct egress

### Requirement: Port-Level Solver Backoff

The captcha manager MUST track consecutive solve failures per egress port and MUST stop starting new solves on a port that failed twice in a row, for a randomized cooldown window (default 60 seconds scaled by 0.75–1.5). Backoff MUST be strictly local to the captcha scheduling path: it MUST NOT increment egress circuit-breaker failures, MUST NOT change account quota or credential state, and MUST NOT block other ports. A successful solve or a reservoir hit on that port MUST clear its failure counter. Requests that hit a backed-off port with an empty reservoir MUST fail fast with the dedicated local diagnostic instead of occupying the single solver slot.

#### Scenario: Third Attempt Fails Fast
- **WHEN** a port fails two consecutive solves and a further request needs a token from that port while its reservoir is empty
- **THEN** the request MUST fail immediately without spawning a Node.js process
- **AND** the failure MUST be reported with the local `solver_port_backoff` diagnostic

#### Scenario: Success Clears Backoff
- **WHEN** a solve on a backed-off port succeeds, or a request is served from that port's reservoir
- **THEN** the port's consecutive failure counter and cooldown MUST be cleared

#### Scenario: Backoff Is Not Egress Evidence
- **WHEN** a port is in solver backoff
- **THEN** the egress circuit-breaker state for that port MUST remain unchanged
- **AND** no account's status, quota or credential state MUST be modified by the backoff

### Requirement: Solver Budget Convergence

The producer budget covering configuration retrieval, capacity wait, Node execution and retry backoff MUST default to 25 seconds, kept overridable by `ZCODE_CAPTCHA_TIMEOUT`. The budget MUST remain bound to a single producer and MUST NOT be extended to model calls or SSE streams.

#### Scenario: Default Budget Bounds One Producer
- **WHEN** a producer starts with default settings
- **THEN** its configuration, queue wait, execution and backoff MUST all finish inside 25 seconds
- **AND** exceeding it MUST terminate the producer with the existing budget-exhausted diagnostic
