## MODIFIED Requirements

### Requirement: Proxy-Affinity Regional Captcha Pre-solved Reservoir
The system MUST partition pre-solved tokens by actual egress and managed identity, matching solve and consumption routes. Periodic refill MUST target selectable Plan accounts' effective chat egress only, stop at its minimum, and preserve one shared direct reservoir. Claim solves MUST be demand-driven and MUST NOT create periodic or consumption-triggered claim-only refill.

#### Scenario: Sub-millisecond Token Delivery from Matching Proxy Reservoir
- **WHEN** a chat operation uses proxy `21200` and that exact identity has a valid token
- **THEN** the system immediately consumes it without starting Node and requests a bounded refill for that chat target.

#### Scenario: Preventing Cross-Proxy Token Poisoning
- **WHEN** an operation uses `21200` but only `21201` or direct has valid tokens
- **THEN** it MUST NOT consume those tokens and MUST solve through its admitted `21200` route.

#### Scenario: Direct reservoir reaches its minimum
- **WHEN** effective chat mode is direct and its reservoir is full
- **THEN** no refill is scheduled for the stored account proxy, fixed claim proxy, or full direct reservoir.

#### Scenario: Direct reservoir is empty
- **WHEN** effective chat mode is direct, a selectable Plan account exists, and its reservoir is below minimum
- **THEN** refill selects exactly one no-proxy target regardless of account regions or claim bindings.

#### Scenario: No selectable Plan account
- **WHEN** no account can be selected for Plan chat
- **THEN** periodic chat refill has no targets; a billable exhausted account may still solve on demand for an eligible claim.

#### Scenario: Proxy mode retains its own water level
- **WHEN** chat proxy `21200` is below minimum and the direct reservoir is full
- **THEN** refill targets `21200` and does not count direct tokens toward its water level.

#### Scenario: Claim solves without permanent warm-up
- **WHEN** chat is direct and an operator claims through fixed HK `21100`
- **THEN** claim solves through `21100` on demand, does not trigger claim-only refill, and does not add `21100` to the periodic chat target set.

### Requirement: Singleflight Empty-Take Consolidation
Concurrent empty-take requests for the same actual egress and managed identity MUST share one solve. Its producer MUST retain the admitted route and reference until completion or cancellation cleanup; callers on other egresses or identities MUST NOT join it. Foreground broadcast, single-use refill banking, shared solver limits and bounded budgets MUST retain existing behavior.

#### Scenario: Merging Concurrent Solves on Empty Queue
- **WHEN** 3 foreground requests concurrently need a token for the same `21100` identity
- **THEN** exactly one Node solve is initiated and its successful result is broadcast to those waiters.

#### Scenario: Mixed purposes with different routes
- **WHEN** the same account concurrently needs a direct chat token and an HK claim token
- **THEN** the solves retain distinct routes and references; joining or cancellation cannot redirect the other producer.

### Requirement: Three-State Node Circuit Breaker (CLOSED / OPEN / HALF-OPEN)
The system MUST retain CLOSED / OPEN / HALF-OPEN breakers, quarantine and bounded recovery admission per purpose and actual proxy identity. Explicit chat WAF refusal MUST affect chat admission without implying claim refusal. Claim errors MUST affect claim only; retirement and identity invalidation still apply to every purpose.

#### Scenario: WAF Ban Rapid Tripping
- **WHEN** chat receives repeated WAF F001 / 3012 at `21102`
- **THEN** its chat breaker MUST be OPEN for the configured quarantine (default 600s), no later than the legacy two-response limit; a single deterministic rejection may trip earlier.
- **AND** this alone MUST NOT open that slot's claim breaker or silently send chat direct.

#### Scenario: Half-Open Probe Recovery
- **WHEN** a purpose breaker finishes its quarantine
- **THEN** allow exactly one recovery request for that purpose/identity; success closes it, failure reopens it, and cancellation releases the permit neutrally.

#### Scenario: Claim recovery without claiming rewards
- **WHEN** a quarantined claim route needs recovery
- **THEN** a bounded claim-purpose activation/preview or an explicitly requested normal claim may provide evidence; no scheduled recovery probe may POST a reward claim just to test connectivity.

### Requirement: Target-Scoped Pool Invalidation (Blast Radius Control)
An upstream 3007 challenge MUST invalidate only the actual failed egress/identity token pool. The account's stored region MUST NOT broaden invalidation to a differently routed purpose, and direct failure MUST NOT mean global purge. Other actual egresses and identities MUST retain their tokens.

#### Scenario: Scoped Invalidation on 3007 Challenge
- **WHEN** an HK claim through `21100` receives 3007
- **THEN** invalidate that actual HK target only; direct chat and TW / JP / SG pools remain intact.

#### Scenario: Direct challenge under split routing
- **WHEN** direct chat receives a challenge while the account has an HK claim binding
- **THEN** invalidate only the direct reservoir and retain the HK claim tokens.
