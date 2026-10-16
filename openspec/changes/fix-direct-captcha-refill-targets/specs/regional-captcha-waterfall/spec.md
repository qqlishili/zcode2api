## MODIFIED Requirements

### Requirement: Proxy-Affinity Regional Captcha Pre-solved Reservoir
The system MUST maintain distinct in-memory pre-solved token queues partitioned by actual egress, continuously refilled by background workers using the exact egress assigned to that queue, ensuring O(1) token delivery and matching the solving egress to the request outbound egress. In explicit direct mode, all eligible Plan accounts MUST share one no-proxy reservoir; proxy mode MUST retain per-proxy isolation. Refill decisions MUST use the water level of the actual target reservoir and MUST stop while that reservoir has reached its configured minimum. Accounts that cannot be selected for Plan requests MUST NOT create background refill targets.

#### Scenario: Sub-millisecond Token Delivery from Matching Proxy Reservoir
- **WHEN** a request arrives for an account whose assigned proxy is `http://127.0.0.1:21200`
- **AND** the reservoir for `21200` contains a valid, unexpired token
- **THEN** the system MUST immediately pop and return that token without spawning Node.js child processes
- **AND** the system MUST trigger an asynchronous background task to replenish `21200`'s reservoir.

#### Scenario: Preventing Cross-Proxy Token Poisoning
- **WHEN** account A is mapped to proxy `21200` and proxy `21201` has 3 pre-solved tokens while `21200` is empty
- **THEN** the system MUST NOT assign tokens from `21201` to account A
- **AND** the system MUST resolve a fresh token specifically through `21200`.

#### Scenario: Direct reservoir reaches its minimum
- **WHEN** explicit direct mode is enabled and a selectable Plan account has a stored region or explicit proxy
- **AND** the no-proxy reservoir has reached the configured minimum with valid tokens
- **THEN** background refill MUST NOT schedule a solve for that account's stored proxy or for the already-filled no-proxy reservoir.

#### Scenario: Direct reservoir is empty
- **WHEN** explicit direct mode is enabled, a selectable Plan account exists, and the no-proxy reservoir is below its minimum
- **THEN** background refill MUST select exactly one no-proxy target regardless of account regions or explicit proxies.

#### Scenario: No selectable Plan account
- **WHEN** no account can be selected for Plan requests
- **THEN** background refill MUST have no targets in either egress mode.

#### Scenario: Proxy mode retains its own water level
- **WHEN** proxy mode is enabled, a selectable account uses proxy `21200`, its reservoir is empty, and the no-proxy reservoir is full
- **THEN** background refill MUST select `21200` and MUST NOT treat the no-proxy tokens as its water level.
