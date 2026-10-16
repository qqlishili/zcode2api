# Spec Delta

## ADDED Requirements

### Requirement: Account Creation Assigns A Region

Creating an account that holds a credential for the `zai` provider MUST assign a permanent region in the same operation, so that every entry point producing accounts converges on one behaviour. The entry points covered are the admin bulk add, the OAuth login callback, the OAuth API-key exchange and the backup import; all of them create accounts through `Store.add_account`.

#### Scenario: OAuth Callback Assigns A Region

- **WHEN** an OAuth login callback stores a new account through `Store.add_account`
- **THEN** the returned account MUST carry a non-empty region drawn from the assignable tiers

#### Scenario: Bulk Add And Import Assign A Region

- **WHEN** accounts are created through the admin bulk add or the backup import path
- **THEN** each created account MUST carry a non-empty assignable region

#### Scenario: Credential-Less Account Is Not Assigned

- **WHEN** an account is created without a usable credential
- **THEN** region assignment MUST NOT fabricate a binding for it

### Requirement: Assignment Avoids Regions Without Active Egress

Region assignment MUST read the current egress registry and choose only among regions that currently hold at least one admission-active egress. Assigning a region whose registry entry is empty MUST NOT happen, because the account pool then resolves no proxy for that account and the account becomes unserviceable.

#### Scenario: Empty Region Is Skipped

- **WHEN** the registry lists a candidate region with no active egress
- **THEN** that region MUST NOT be selected
- **AND** the account MUST be assigned a region that does have active egress

#### Scenario: No Registry Means No Fabricated Binding

- **WHEN** the egress registry cannot be read or holds no active egress at all
- **THEN** assignment MUST leave the account without a region rather than pick an arbitrary one
- **AND** the account MUST remain eligible for a later backfill

### Requirement: Assignment Range Follows The Supply Tiers

Assignment MUST be limited to the P0 (HK/JP/TW) and P1 (SG/US) tiers already used for offline seeding, with weights taken from the existing seeding definition. The P2 tier (KR/EU/OTHER) MUST NOT be assigned at runtime.

#### Scenario: P2 Region Is Never Assigned

- **WHEN** an account is created while the registry has active egress in P2 regions only
- **THEN** the account MUST NOT be assigned a P2 region

#### Scenario: Distribution Follows Existing Weights

- **WHEN** many accounts are created against the same registry
- **THEN** the assigned regions MUST follow the P0/P1 weighting already used by the offline seeding script

### Requirement: Assignment Is Idempotent

An account that already holds a region MUST keep it. Repeated assignment calls, backfill runs and restarts MUST NOT change an existing binding.

#### Scenario: Existing Binding Is Preserved

- **WHEN** an account with a region passes through the creation path or the startup backfill
- **THEN** its region MUST remain unchanged

#### Scenario: Backfill Only Fills Gaps

- **WHEN** the startup backfill runs over a pool mixing assigned and unassigned accounts
- **THEN** only the unassigned accounts MUST receive a region
- **AND** the number of changed accounts MUST be reported

### Requirement: Startup Backfill Repairs Existing Gaps

Application startup MUST backfill regions for stored accounts that hold a credential but no region, alongside the existing fingerprint and install-id backfills, so that accounts created before this behaviour shipped become serviceable without manual seeding.

#### Scenario: Stored Gap Is Filled At Startup

- **WHEN** the service starts with stored accounts missing a region
- **THEN** those accounts MUST be assigned a region and persisted
- **AND** accounts that already hold a region MUST be left untouched
