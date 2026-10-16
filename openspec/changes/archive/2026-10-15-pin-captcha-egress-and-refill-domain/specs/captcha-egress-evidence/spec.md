# Spec Delta

## ADDED Requirements

### Requirement: Durable Request and Solve Evidence

The system MUST persist one record per gateway request and one record per captcha solve attempt or reservoir hit into an on-disk store (`data/evidence.db`) so that availability regressions can be judged against a window longer than process lifetime. Records MUST use an explicit field whitelist (identifiers, endpoint, model, account name, mode, outcome, status, request id, timings, diagnostics codes, egress port, solve source, pool-hit flag) and MUST NOT contain credentials, cookies, JWTs, `verifyParam` values or raw upstream bodies. Retention MUST default to at least 7 days, pruning older rows incrementally. Evidence writing MUST be observational only: a storage failure MUST be logged and MUST NOT alter request handling, account state or scheduling.

#### Scenario: Request Outcome Is Recorded
- **WHEN** a gateway request finishes successfully or with an error
- **THEN** the store MUST contain a row carrying its request id, timings, outcome and, for failures, the bounded error text
- **AND** the same row MUST NOT contain any credential or verification parameter

#### Scenario: Solve Attempt Is Recorded With Its Port
- **WHEN** a captcha producer finishes (success or failure) or a request is served from the reservoir
- **THEN** the store MUST contain a row with the egress port, the solve source (`foreground`/`refill`/`hit`), the diagnostic reason, the attempt count and the phase timings

#### Scenario: Retention Prunes Old Rows
- **WHEN** records older than the configured retention window exist
- **THEN** they MUST be deleted without blocking request handling

#### Scenario: Storage Failure Is Non-Fatal
- **WHEN** the evidence database cannot be opened or written
- **THEN** requests and solves MUST still complete normally
- **AND** the failure MUST be visible as a warning only

### Requirement: Evidence Summaries for Availability Judgement

The system MUST expose an authenticated summary over the persisted evidence, reporting request counts and success rate, first-byte latency percentiles, solve success rate, reservoir hit rate, and per-port / per-reason distributions for a caller-selected window.

#### Scenario: Summary Supports Root-Cause Judgement
- **WHEN** an administrator requests the summary for the last 24 hours
- **THEN** the response MUST include request success rate, first-byte percentiles, solve success rate and reservoir hit rate
- **AND** it MUST break down solve outcomes by egress port and by diagnostic reason

#### Scenario: Empty Window Is Not An Error
- **WHEN** the requested window contains no evidence
- **THEN** the summary MUST return zeroed counts and empty distributions rather than failing
