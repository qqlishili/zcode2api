# Spec Delta

## ADDED Requirements

### Requirement: Business Response Counts As Node Reachability

Node reachability MUST be decided by the gateway business-error normalisation (`app.routes.gateway._classify_business_error`) rather than by an HTTP status check inside the probe script. A response whose normalised effective status is below 500 MUST count as reachable: quota exhaustion (`code=1005`), rate limiting (`429`) and captcha challenges (`403`) all prove the egress chain completed end to end (connection, upstream acceptance, authentication). A normalised effective status of 500 or above, a connection failure, a TLS failure or a timeout MUST NOT count as reachable.

#### Scenario: Quota Exhausted Node Is Reachable

- **WHEN** a probe request through a candidate egress returns HTTP 200 with body `{"code":1005,"msg":"exceed quota limit"}`
- **THEN** the normalisation MUST yield effective status 402
- **AND** the node MUST be kept `active` and its failure counter reset

#### Scenario: Captcha Challenge Is Reachable

- **WHEN** the response carries the captcha challenge header or business code 3007
- **THEN** the node MUST be counted as reachable

#### Scenario: Upstream 5xx Is Not Reachable

- **WHEN** the response normalises to an effective status of 500 or above
- **THEN** the node MUST NOT be counted as reachable

#### Scenario: Network Failure Is Not Reachable

- **WHEN** the connection is refused, TLS fails or the request exceeds the configured timeout on both the attempt and its retry
- **THEN** the node failure counter MUST increase and the node MUST be marked `stale`

### Requirement: Probe Identity Requires Only Valid Credentials

The probe identity MUST be selected from accounts that are enabled and hold a credential (JWT or API key). The selection MUST NOT require a serviceable account status. Quota-exhausted accounts have credentials that remain valid and a `max_tokens=1` probe request consumes no quota, so they are the lowest-cost probe identity. Only when no enabled account holds a credential may the round be reported uncommitted.

#### Scenario: Exhausted Account Serves As Probe Identity

- **WHEN** every account in the pool is `exhausted`, enabled and holds a JWT
- **THEN** one of them MUST be selected as the probe identity
- **AND** the round MUST NOT fail with a missing-probe-account condition

#### Scenario: Missing Credentials Keep The Round Uncommitted

- **WHEN** no enabled account holds a credential
- **THEN** the round MUST be reported uncommitted with a missing-probe-account reason
- **AND** no empty routing snapshot may be published

### Requirement: Probe Request Shape Comes From The Project Builder

The probe request URL, headers and body MUST be produced by `app.agent.build_request`. Hand-written request shapes MUST NOT be used as a reachability input, because the upstream rejects shapes missing the trace headers or the body transformation with a risk-control response that would otherwise be misread as node failure.

#### Scenario: Trace Headers And Body Transformation Are Present

- **WHEN** a probe request is assembled
- **THEN** it MUST carry `x-request-id`, `x-zcode-session-type` and `x-zcode-trace-id`
- **AND** its body MUST have passed the body transformation

### Requirement: Bounded Retry Before Failure Attribution

A single candidate MUST be allowed one bounded network retry before its failure counter increases, so that a transient first-request failure does not retire a usable node. The retry budget MUST stay inside the round scan deadline.

#### Scenario: First Attempt Jitter Does Not Retire The Node

- **WHEN** the first attempt fails at the network layer and the retry succeeds
- **THEN** the node MUST be counted as reachable and its failure counter MUST NOT increase

#### Scenario: Repeated Network Failure Retires The Node

- **WHEN** both the attempt and its retry fail at the network layer
- **THEN** the node failure counter MUST increase and the node MUST be marked `stale`
