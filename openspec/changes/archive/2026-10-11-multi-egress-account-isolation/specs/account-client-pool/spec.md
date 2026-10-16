# Spec Delta

## Purpose

Provides account-isolated HTTP/TCP connection pool management, consistent hash proxy routing, and graceful direct fallback to prevent upstream WAF correlation.

## ADDED Requirements

### Requirement: Account isolated client instance
The system SHALL allocate and maintain an independent `httpx.AsyncClient` instance for each account ID, preventing connection and TLS state reuse across accounts.

#### Scenario: Separate client instance per account
- **WHEN** requests are made for two different account IDs
- **THEN** the system provides distinct `httpx.AsyncClient` instances with separate connection pools

### Requirement: Consistent proxy routing
The system SHALL bind each account to a stable proxy endpoint via consistent hashing when global proxy list `ZCODE_PROXIES` is configured, unless an explicit `account.proxy` is specified.

#### Scenario: Explicit account proxy takes priority
- **WHEN** an account has a non-empty `proxy` field configured
- **THEN** the client instance for this account uses the account-level proxy

#### Scenario: Consistent hashing over global proxy list
- **WHEN** global `ZCODE_PROXIES` contains multiple proxy URLs and the account has no explicit proxy
- **THEN** the system consistently maps the account to the same proxy URL based on hash of account ID

### Requirement: Graceful fallback without proxy
The system SHALL gracefully fall back to direct internet connection with an account-isolated client when neither `account.proxy` nor `ZCODE_PROXIES` is configured.

#### Scenario: Graceful fallback when no proxies configured
- **WHEN** no proxy is configured in the account or global settings
- **THEN** the system creates an isolated client without proxy and serves the request without raising errors

### Requirement: Idle client cleanup
The system SHALL track client activity and automatically close and evict client instances that have remained idle beyond the configured TTL (default 300 seconds).

#### Scenario: Evict client after idle timeout
- **WHEN** a client instance has not been accessed for more than the idle timeout
- **THEN** the pool closes the client and frees resources on the next prune cycle
