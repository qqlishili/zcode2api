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
