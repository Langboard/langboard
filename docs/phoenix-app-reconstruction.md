# Phoenix main compatibility audit

Authoritative snapshots: main `45171c109`, fixed PR 683 base
`bdcc1eeb943235024e17d08aebd86f01e547695d`, candidate
`6a4ece1d4f2d83f178730df5af7c1b8fab30da2b`. The fixed candidate is preserved.

## Reuse and reconstruction boundaries

| Candidate-owned area | Reuse | Required work on current main |
| --- | --- | --- |
| SDK wheel/tarball/panel packages and provenance | Preserve exact published versions and hashes as contract inputs | Restore the SDK facade and dependency wiring; none of the eleven SDK delta paths exists on main |
| App manifest, governance, resource binding and consent models | Reuse contract semantics and test cases | Reconstruct prerequisite tables/services and ordered Alembic migrations before applying the twelve app-model and eleven app-service delta paths missing from main |
| App cards, human interaction and execution review/outbox | Reuse permission, revision, lease and receipt requirements | Reconnect to current Python GraphApprovalRequestService/InternalBotRunService; preserve native card/comment/checklist behavior and current authority checks |
| App MCP catalog/consent and settings review routes | Reuse public API contracts | Rebuild registration and dependency wiring; all five route delta paths are absent on main |
| React app panels, card interactions and consent settings | Reuse components after dependency audit | Restore native API/SDK controllers and map wire events to Phoenix; only twelve of sixty UI delta paths exist on main |
| Graph transport | Keep response/schema semantics | Use Phoenix Graph.StreamClient/ResumeClient and current Python claim endpoints, not Node request/event implementations |
| Socket run, approval and recovery adapters | Retain domain acceptance requirements | Keep BoardChat/Editor RunWorker, ResumeWorker and AcceptedRecovery, current run leases, project/user checks and Kafka ownership |

Main does not contain the earlier AppRegistry/CardVisibility/CardWorkState
foundation. Applying only the candidate delta is therefore insufficient.
Copying the whole legacy candidate would delete Phoenix source and restore Node
package, Docker and workflow paths. Neither action is an accepted reconstruction.

PR 684 contains the Graph authentication/history bridge. The dependent
app foundation review branch contains the reconstruction described below. It remains an incomplete
reconstruction, rather than full app/HITL acceptance. It does not prove app/HITL acceptance, Phoenix compilation
or operational deployment. All existing review PRs and the local canary union
remain preserved; no merge or protected deployment-reference update is made.


## App database foundation reconstructed

The first dependency slice reuses these models from fixed candidate
`6a4ece1d4`: Organization, AppDefinition, AppConnection, BoardAppBinding,
AppGovernancePolicy, AppComposition and AppCompositionMember. The original
AppRegistry name denotes a service; the persisted declaration is AppDefinition.
No provider-specific identifiers, adapters or company defaults are added.

Migration `a7c9e31b6204` follows current main head `0cc6abc4b020`. It adds seven
new tables and does not rewrite existing card, run, approval or Phoenix tables.
Fresh model metadata and the migration both enforce connection ownership/state,
instance/organization policy scope and one app binding per board/app key.
Bindings default to disabled, connections to pending. Registry declarations
remain administrator approval records; importing their model does not install
an app or grant any execution permission.

For new uniquely keyed fields, redundant `index=True` flags are removed: the
UNIQUE constraint already provides an index in PostgreSQL. Foreign-key/query
indexes are retained. This avoids duplicating write/storage overhead while
preserving lookup and uniqueness behavior. The shared ORM is unchanged.

The PostgreSQL migration regression applies upgrade on the native user/project
prerequisites, persists personal and organization connections, rejects invalid
ownership/state/policy and duplicate board bindings, checks index duplication,
then downgrades without deleting existing user/project rows and upgrades again.
This is the new migration delta; it does not claim a replay of all historical
migrations or a migration from the old canary schema.

Remaining dependencies include organization membership/project boundaries,
CardVisibility/CardWorkState, SDK dependency/registry services and ordered app
consent/resource/execution/receipt migrations. App registration endpoints,
policy execution and second-app integration are not enabled by this schema.
Phoenix workers, Kafka ownership, fixed PR683 and old union remain preserved.

Validation: the combined 133-test regression passed in 31.89 seconds before
the final board binding-state constraint. The final migration delta, including
that constraint and independent negative fixtures, passed its PostgreSQL
integration test in 1.28 seconds. Ruff and diff checks pass.


## Current app policy and shared transaction boundary

AppGovernance is reconstructed from the preserved candidate without provider
branches. Project.organization_id is added in migration b6d3e71f9042 following
a7c9e31b6204; existing boards remain unassigned. Unlike the prior implicit
CASCADE foreign key, deleting a referenced organization is rejected so it
cannot delete existing boards. Organization creation/assignment routes and
membership management remain pending; current policy uses existing board
membership and organization ownership contracts.

Instance policy remains an upper limit for organization policy. Personal
connection credentials do not become visible to administrators, and personal
connections cannot power unattended automation on organizational/shared boards.
Policy saves require current primary actor/organization authority and a current
revision. Runtime policy reads avoid global/organization row locks. Reusing the
already current project and the policy's organization validation avoids duplicate
project/organization reads in connection authorization.

DbSession.atomic shares one primary transaction across nested repository calls;
readonly requests inside that unit use the same connection. after_commit runs
best-effort invalidation only after commit, and savepoint rollback discards its
callbacks. A SELECT failure inside the atomic unit propagates rather than
retrying with another session and losing its locks. Existing non-atomic behavior
is retained. Do not share this synchronous transaction across spawned async
tasks: those tasks must open independent units. apps:changed contains no app,
board or credential data; Phoenix UI event consumption remains unverified.

PostgreSQL tests cover effective policy ceilings/revisions, current admin and
account revocation, organization suspension, connection privacy and unattended
ownership. Additional checks count one connection checkout for nested units,
confirm savepoint/outer rollback and deferred notifications, and reject fresh
session retries after a transactional query error. API routes and complete app
execution fences remain pending; these service tests are not deployed acceptance.

Validation: combined regression passes 148 tests in 45.72 seconds. After
removing redundant scope reads and owner membership lookup, final native
PostgreSQL governance regression passes 16 tests in 13.24 seconds, including
six SELECTs for the board owner's organization connection authorization.
Nested atomic repository calls check out one connection. Ruff and diff checks
pass. Historical/canary schema migration and deployed runtime remain pending.


## Governance HTTP settings boundary

AppGovernanceSettingsApi now exposes instance policy read/update, bounded
organization policy scope discovery and organization policy read/update through
the native AppRouter and ApiAuthMiddleware. Global routes require an admin;
organization routes still check current ownership or current admin status in
the service. Organization UID paths validate their shape before decoding.
Policy conflicts return EX3004/409, unauthorized operations return 403 and
invalid mode/cursor input returns validation errors. No identity, token or app
connection data is returned by organization discovery.

The PostgreSQL HTTP regression uses real signed access/refresh credentials and
the real user auth cache, with cached users populated before requests. It does
not replace the authentication middleware or policy implementation. Anonymous
requests, foreign organization access, bounded pagination, malformed UIDs,
stale revisions, inheritance and invalid input are exercised. Current admin
removal and account deactivation are rejected even while their prior identity
remains cached. This covers local ASGI authentication and policy behavior;
production SSO, browser settings and Phoenix event consumption are pending.

Final combined local regression: 150 tests pass in 55.92 seconds, including
the native authenticated policy routes on PostgreSQL. Ruff and diff checks pass.


## Connection authentication and owner MCP consent

The host consumes the independent langboard-sdk 0.2.33 wheel with SHA256
b021a7a849b3a712851069eac887dca3c937f669eea6095edf1ba1c5d4f3bd12.
The shared, API and Graph locks retain their existing package versions and
include that exact artifact. API and Graph image contexts copy the artifact
before dependency installation; private SDK repository access is not required.

Migration c8f4a62d190e adds hashed inbound credentials and owner MCP consent
following b6d3e71f9042. Authentication rechecks the connected state, active
owner, enabled declaration, current instance/organization policy and declared
trust identity. Credentials expire, support explicit revocation and never
expose their stored hash in bounded receipt listings. Instance administrators
cannot mint credentials or consent for another person's private connection.

Consent is explicitly versioned and separate from authentication. It preserves
unchanged tool scopes, rejects stale revisions and requires consent for scope
expansion or trust changes. Current owner deactivation and removed declared
capabilities deny effective scopes. No credential or consent is an execution
grant; the receipt explicitly reports execution_supported=false until the
separate execution boundary is restored.

Native PostgreSQL regression: 32 tests pass in 21.44 seconds, covering policy,
credential/consent services and migration upgrade/downgrade. No SQLite was used
for this acceptance run. Public connection routes, app registration and update
fences, external MCP execution, outbox receipts, Phoenix event consumption and
real independent-app acceptance remain pending. No merge or deployment.


## Native connection management HTTP boundary

The API now exposes credential issue/list/revoke, connection MCP consent
read/replace and the separate /apps/v1/identity endpoint. User management
operations use the existing ApiAuthMiddleware and current service ownership
checks. App bearer credentials cannot self-approve user consent, and a user
JWT cannot authenticate through the app identity endpoint. Successful credential
and consent responses set Cache-Control: no-store; receipt lists exclude token
values and hashes. UID paths, cursors, lifetime and consent forms are bounded.

The HTTP regression uses real signed user access/refresh tokens and a populated
native auth cache. It exercises anonymous 401, private connection access 403
for administrators/outsiders, current deactivation, app-versus-user identity
separation, revocation, invalid bodies/UIDs/pagination and stale consent 409.
The Graph locked environment passes 33 tests in 23.45 seconds; the separately
frozen API environment passes the same 33 tests in 25.82 seconds (FastMCP
4.0.0b3 remains pinned). This does not prove
production SSO, native inbound onboarding, board resource grants or execution.


## Generic registry approval and durable consent withdrawal

AppRegistry now approves declarations and strictly newer version updates using
the independent SDK validator and consent update contract. It has no provider
name reservation or built-in provider fallback. Instance-admin authority is
rechecked from primary rows. The native registry HTTP routes expose bounded
keyset pages (maximum 50), approval and disable operations with revision checks.
Registry listing uses two bounded SELECTs and does not lock the administrator
row for reading; mutation paths retain authority locks.

Updates retain unchanged capabilities, durably withdraw changed MCP tool scopes
and do not restore old consent when a subsequent version restores an earlier
tool list. Trust changes revoke connections and remove board grants; disable
clears grants and retained MCP consent. Reenabling a declaration does not grant
owner consent or board access. Cards-producing declarations require a lifecycle
policy even when using schema version 1. Archive synchronization is withdrawn
when its lifecycle/event prerequisites change. Existing explicitly enabled stage
transitions are preserved across same-trust updates with retained grants.

The owner-consent transaction now locks definition before connection and checks
that ownership did not change between its authorization and lock. A PostgreSQL
concurrency regression executes trust update and old owner consent on independent
connections; the stale consent is rejected without a deadlock or restored grant.
Local frozen-API PostgreSQL suite passes 38 tests in 25.14 seconds, including
native authenticated registry routes, current admin revocation, paging, lifecycle
validation and a second app name using the same API. This is not full independent
app execution acceptance. Inbound onboarding, 1:N resource grants, common stage
mapping/events, execution fences, outbox/permanent receipts, HITL and deployed
Phoenix/canary acceptance remain pending. No merge or deployment.


## Native inbound connection onboarding

Users can create a host-owned inbound identity for an approved app declaration,
list their personal identities or an explicitly managed organization scope, and
disconnect using the current identity revision. Personal ownership always comes
from the current actor. Organization connection creation requires current
organization ownership or instance administrator authority. Forms accept no
caller-selected owner, provider credential reference, destination or grants.
This is inbound service identity registration, not external account verification.

Disconnect uses the existing definition/connection/consent lock ordering, revokes
all unrevoked credentials for that connection and durably empties retained MCP
scopes. It preserves connection and credential receipts. Stale disconnect
revisions return a conflict; management discovery remains available after app
policy disablement. Provider-backed identities are excluded from native inbound
management by their metadata shape rather than provider-name hardcoding.

Personal connection pages issue three SELECTs (current actor, declaration,
bounded connection page), with one declaration reused for all identity hashes.
Maximum page size is 50. Actual API auth and PostgreSQL regressions cover owner
isolation, organization authority, injected fields, stale revisions, current
account deactivation, disconnect/token invalidation and paging query counts.
Frozen API environment: 41 tests pass in 29.23 seconds. Board resource consent,
common workflow stages/events, execution fences/outbox/permanent receipts/HITL
and deployed independent-app acceptance remain pending. No merge or deployment.

## Review corrections: inbound identity and installed package preservation

Inbound credential issuance and authentication both require the native inbound
metadata shape. A provider-backed connection cannot receive a host inbound token;
changing any of credential reference, instance URL or external account ID also
rejects an already-issued token. Owners retain receipt discovery and revocation.
This boundary is independent of provider names and does not classify a GitHub
user comment as human initiated or automated.

Declaration-only approvals preserve the existing installed package metadata.
A non-null package supplied by a package installation path replaces it; package
removal is not exposed by the declaration approval API. The authenticated HTTP
regression verifies the stored private storage reference and public installation
receipt survive a newer declaration. Regular imports in the policy service and
PostgreSQL/HTTP suites now live at module scope.

These corrections do not complete board resource grants, workflow mapping,
execution/outbox/HITL or deployed independent-app acceptance.

## Explicit board app consent

Board owners, current instance administrators, or current board members with the
native update role can prepare a disabled binding and explicitly approve a
subset of the approved app's capabilities. Preparation grants no capabilities.
Updates compare both binding and app revisions. Every operation reads current
primary account, board and membership/role state. Mutation lock order is actor,
app definition, board, then binding; registry updates use definition then binding.

Human-authenticated prepare/update routes reject app Bearer self-approval and
extra fields such as caller-selected stage transition flags. App/global/org
policy must allow new grants; empty withdrawals remain possible after disablement.
Stage transitions are not automatically activated. Removing event delivery also
clears archive sync opt-ins. 1:N resource selection, stage/archive opt-in editors,
runtime execution fences and deployed acceptance still require further work.
