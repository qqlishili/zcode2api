## MODIFIED Requirements

### Requirement: Account isolated client instance
The system SHALL maintain independent HTTP clients for each account and request purpose, preventing connection or TLS state reuse across accounts or differently routed purposes. Nested operations SHALL reuse an admitted client only when account, purpose, and effective route match. Account closure SHALL retire all purpose clients while preserving in-flight references until release.

#### Scenario: Separate client instance per account
- **WHEN** requests are made for two different account IDs
- **THEN** the system provides distinct client instances with separate connection pools.

#### Scenario: Concurrent purposes for one account
- **WHEN** one account concurrently sends a direct chat request and a proxied claim
- **THEN** both retain their selected routes and distinct clients until completion, including SSE and captcha producers.

#### Scenario: Nested quota refresh changes purpose
- **WHEN** a chat operation schedules a claim-purpose quota refresh for the same account
- **THEN** the refresh obtains its own purpose client rather than inheriting the chat operation.

#### Scenario: Account deletion during streaming
- **WHEN** an account is removed while a chat stream or claim producer still references its client
- **THEN** new acquisitions stop and every purpose client closes after its remaining references release.

### Requirement: Consistent proxy routing
The system SHALL choose the effective purpose policy before account/global proxy resolution. An inherited proxy policy SHALL retain existing explicit-account and stable regional/global routing. Explicit direct SHALL override stored proxies; explicit chat proxy SHALL use its configured local endpoint; explicit claim proxy SHALL use its fixed same-region slot identity. No proxy failure SHALL silently switch to direct or another purpose's route.

#### Scenario: Explicit account proxy takes priority
- **WHEN** an account has a non-empty proxy field and its purpose inherits the legacy proxy policy
- **THEN** the client uses that account proxy, subject to existing admission and retirement checks.

#### Scenario: Consistent hashing over global proxy list
- **WHEN** the purpose inherits proxy routing, global proxies are configured, and the account has no explicit proxy or assigned region
- **THEN** the existing stable account mapping selects an eligible proxy.

#### Scenario: Purpose override wins
- **WHEN** global mode is direct and an account has an explicit claim binding and a direct chat override
- **THEN** claim uses its binding and chat remains direct regardless of the stored account proxy.

### Requirement: Graceful fallback without proxy
The system SHALL use an account-isolated direct client for an explicit effective direct policy, or for the existing unmanaged inherited case with no proxy configured. A proxy-required policy with a missing binding, offline region, invalid identity, or unavailable endpoint SHALL fail explicitly and SHALL NOT create an unproxied fallback client.

#### Scenario: Graceful fallback when no proxies configured
- **WHEN** an unmanaged inherited policy has no account/global proxy and no assigned proxy region
- **THEN** the existing independent direct connection behavior is retained.

#### Scenario: Explicit direct with offline stored region
- **WHEN** effective chat mode is direct and the stored HK / JP region has no chat-active proxy
- **THEN** the account remains eligible for direct chat, subject to its quota and account status.

#### Scenario: Claim proxy missing while chat works
- **WHEN** the claim policy requires a binding that is absent or invalid
- **THEN** claim fails before upstream traffic while the account's separately routed chat remains available.
