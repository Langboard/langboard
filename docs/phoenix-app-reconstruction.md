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

## Independent 1:N resource selections

A board app binding can select multiple resources per connection, identified by
connection/type/external ID. Native PostgreSQL migration d9a5b73e201f adds unique
identity, foreign keys, bounded access-state/health values and nonnegative
revision constraints. Selection is explicit and revision checked; duplicate stale
writes conflict. Declared resource types and current board grants, app policy,
connection ownership, connection state and owner activation are enforced.
Personal connections stay private even to instance administrators; organization
connections must match the board organization. Disconnected accounts retain
selection receipts and support withdrawal. Selection stores unknown access and
health; it does not prove access to an external provider. A client cannot supply
access verification fields. Page size is at most 50 with keyset pagination.

Lock order for selection is actor, definition, board, connection, binding and
resource. The board authorization helper can defer its binding lock to preserve
the registry's definition/connection/binding order. No provider names are used.
Provider discovery and verification, connection identity snapshot consent,
workflow mapping, execution fences and deployed independent-app acceptance
remain pending. Resource metadata alone must not be used as an execution grant.

## Connection identity consent and SDK resource read boundary

Selection updates now require a current connection identity revision from the
human management receipt and persist that identity hash. Resource mutations
increment a board selection generation included in the binding revision, so
multi-page clients can detect changed selections. Migration e1b6c84f312a retains
old resource receipts but revokes selections that have no historical identity
proof; it never infers new consent from the currently connected account.

The app-Bearer GET /apps/v1/boards/{uid}/resources returns the SDK's versioned
resource page, scoped to the authenticated app/connection and current board
capability, ownership, user read membership, policy and declared resource types.
Only selected resources with granted access and a matching current identity are
returned. Unknown selections stay excluded. Tests use a synthetic stored provider
verification receipt to exercise the positive response shape; that fixture is
not actual external provider validation. A changed identity rejects old tokens
and hides old selections even with a newly issued token. Provider verification
and runtime execution authority are still pending; no execution grant is implied.

## Policy mutation serialization

Policy-sensitive approval writes use a PostgreSQL transaction-scoped shared
advisory lock before actor/organization/definition locks. Policy changes take the
matching exclusive lock. This covers default scopes with no policy rows and
keeps ordinary approvals concurrent; runtime policy reads acquire neither lock.
Board consent preparation/update, resource selection, MCP consent, inbound
connection creation and credential issuance participate. Commit/rollback releases
the lock automatically. The contract is PostgreSQL-specific; it is not a SQLite
or other database compatibility guarantee.

Two-connection regressions force policy disablement to hold the lock while a
board/MCP/credential mutation attempts entry. Instance and organization scopes
are exercised with both absent and existing policy rows. Mutations observe the
committed disabled policy and fail instead of publishing a new approval after it.

## Built-in workflow mapping foundation

The native six workflow definitions are restored with English/Korean/Japanese/
Chinese defaults. Column descriptions, translations and explicit stage keys are
persisted. Migration f2c7d95a423b keeps existing names and leaves stage bindings
unclassified, adds a restricted stage foreign key and avoids duplicate unique
indexes. It does not infer workflow meaning from column titles.

Generic app requirements come from the approved SDK declaration; no provider
registry or provider-specific presets are restored. Required and optional stages
resolve against active built-in definitions and current nondeleted nonarchive
board columns. Ambiguous stages require an explicit choice; stale choices are
invalid instead of silently moving to another candidate. Human authenticated
mapping APIs require current board authority, app/binding revisions and explicit
transition opt-in under current policy. Saved opt-ins are reported ineffective
when current mapping or policy is invalid. Stage and selected column descriptions
are composed into workflow guidance for API consumers.

This is configuration and discovery, not an executed card transition. Stage
editor/template UI, event publication, card side effects, MCP projection and
execution/HITL integration remain pending.

## Workflow metadata editing API

Current instance administrators can update existing built-in stage display names,
descriptions, hex colors and bounded language translations using a current
revision. Stage identity and policy fields are not editable through this route;
apps cannot add stage types. Active users can discover the native definitions.
Board owners/administrators/current members with update authority can read or
update a column's optional stage, description and localized name/description.
Deleted, foreign-board and archive-stage assignments are rejected; stale column
revisions conflict. Translation languages and fields are validated, including
new languages beyond the four seeded defaults. Responses compose stage and
column guidance. No card movement or policy side effect is performed.

Authorization is shared with board app consent rather than duplicated in the
editor service. Regression uses actual native authenticated HTTP and PostgreSQL
for administrator revocation, nonmember rejection, extra field injection,
translation validation, stale revisions, archive rejection and guidance updates.
The rendered editor, real-time column publication and MCP guidance integration
remain pending.

## Schema-only migration and explicit defaults bootstrap

Workflow revision f2c7d95a423b is now generated by the Alembic CLI against a
throwaway PostgreSQL predecessor schema. It contains only schema operations;
no built-in rows, language literals or ID allocation loop. Model metadata owns
the column-stage foreign key so autogenerated and runtime schemas agree.

The six defaults live in an explicit JSON locale resource. The idempotent
WorkflowStageBootstrap runs once in the main API worker after successful schema
upgrade. A transaction advisory lock serializes concurrent bootstrap attempts.
It inserts missing built-in keys using the normal model identity mechanism and
preserves existing names, descriptions, translations and policies. Repeated
bootstrap is not a full-file reconciler and does not overwrite administrator edits.

PostgreSQL upgrade/downgrade checks distinguish the empty migrated table from
bootstrapped defaults, verify all four locale entries, and prove repeat execution
preserves a user-edited name. Existing deployments of the earlier draft revision
require deployment-specific handling; this branch has not been deployed or merged.

## Column metadata publication

Column workflow edits now publish the existing board and dashboard column change
envelopes only after the database transaction commits. Payload adds description,
translations and workflow stage while retaining uid/name for older readers.
Publication stays scoped to the board's existing channels; column contents are
not sent through the global app invalidation event. Both UI handlers use one
ProjectColumn metadata method and preserve existing values when optional fields
are absent from older name-only payloads; an explicit null stage clears it.

The PostgreSQL regression verifies a separate observer sees committed metadata
when publication runs, rollback emits nothing, stale writes emit nothing, and
both board-scoped envelopes include metadata. Changed TypeScript files pass
esbuild transformation; that is not a full UI typecheck or rendered/browser
acceptance. Live Phoenix broker delivery and browser synchronization remain pending.

## Nonlocking settings reads

Resource-selection listing, workflow-mapping inspection, column-workflow reads,
and the built-in stage list now read current primary authority without row write
locks. The shared authority helpers expose an explicit read mode; mutation callers
retain their existing default locks and policy advisory fence. This avoids a
second authorization implementation or weaker cached authority checks.

A PostgreSQL regression holds each GET service transaction open while a separate
connection updates the authority, definition, column, connection and binding rows
with a database lock timeout. It also inspects executed statements for unintended
row or advisory locks. Existing mutation concurrency and stale-authority tests
remain part of the same suite. This is service-level database proof, not a live
canary latency measurement or complete application acceptance.

## Native MCP workflow guidance

The existing project-column service now includes explicit stage metadata and
composed stage/column descriptions. Referenced stage definitions are fetched in
one query for the whole column page, avoiding one query per column. Unmapped
columns retain only their own description; display names never imply a stage.
Inactive stages are reported as inactive rather than treated as executable.

The native card-workspace adapter retains this context in project identity and
card bundle workflow DTOs. Metadata editing, app mappings and MCP projection
share the same description composition function. A PostgreSQL integration check
uses actual DomainService and native adapter/query DTOs, checks the single batch
lookup and observes changed stage descriptions/activity on a subsequent read.
The existing bounded-source and privacy projection tests remain exercised.
Transport authorization remains owned by the existing MCP role filter; this test
does not claim an authenticated live canary MCP session or provider execution.

## Explicit card app ownership

Card app ownership is an independent record rather than a card author, assignee,
label or signal. Human-authenticated board managers can read, assign or release
it through the native card settings API. App Bearer credentials cannot claim a
card. Assignment requires a current enabled approved definition, an enabled board
binding with cards.create consent, a lifecycle declaration and current policy.
App revision and ownership revision guard stale changes; repeated identical
assignments do not append duplicate audit records. Disabled apps can be released.

The Alembic CLI generates the two ownership tables from model metadata. Ownership
is unique per card; positive revisions are database constraints. Card deletion
cascades the current assignment but not its independent audit ledger. Neither
assignment nor its receipt grants execution, selects resources or resumes work.
The current core card fields and native checklist/comment/relationship services
are not replaced by app ownership. UI controls, app creation lineage, execution
fences and deployed app acceptance are separate remaining work.

## Card-scoped resource snapshots

Human board configuration selects at most twenty verified resources for one
owning app and connection. It reuses board authority, connection privacy and
current policy checks. Assignment requires explicit current app, binding,
connection identity and card ownership revisions. The resource rows must belong
to that exact board binding and connection, remain selected and granted, match
the current identity hash, and use a declared resource type. The client cannot
write access state or grant its own provider verification.

The selection records connection identity and card ownership revision. Releasing
and reassigning the same app does not restore an old snapshot; reconnecting an
account also invalidates it. snapshot_current reports only those snapshot fences,
not an execution permit. Execution must still recheck current resource access,
policy, consent, lifecycle, stage, dependencies and generation. Clearing remains
available after definition disablement or connection disconnection, under current
human board authority and connection privacy. Replacing a prior connection also
requires access to it, preventing takeover of another person's private selection.

The Alembic-generated schema enforces one selection per card/app and positive
revisions; independent audit rows survive deletion of the card or connection.
PostgreSQL regressions exercise drift rejection, cleanup, native HTTP revision
and input constraints, app-token rejection and migration upgrade/downgrade. A
granted resource in these tests is an explicit database fixture; live provider
verification, UI and execution/runtime integration remain unproven.

## Explicit human execution intent and generation

A human board manager can persist a new run intent for an owning app/connection
using the current Ready mapping and observed configuration. The snapshot binds
app approval, board consent, connection identity, card ownership/resource selection,
card revision, built-in stage revision and each verified resource revision. Current
resource access and stage activity are checked again before accepting the request.
The card lock serializes generation increments and request-key deduplication.

The same request key and input return the original receipt. Different input or
stale generation conflicts; concurrent identical submissions create one request
and increment once. The generated PostgreSQL schema adds a nonnegative counter
and a retained intent ledger with unique card/request and card/generation keys.
Deleting a card removes its counter while preserving its intent records. A
human-authenticated point-read returns an existing receipt under current board
and connection privacy even when the app or stage has been disabled.

Every receipt reports started=false and execution_resumed=false. This endpoint
records intent only; it does not grant runtime admission, dispatch an event, move
a card or resume an agent. Dependency and pending-human-approval fences, unattended
connection authority, execution leases, signed outbox and external provider proof
remain required at the subsequent app runtime admission boundary. Current tests
prove PostgreSQL concurrency, CAS, idempotency, current resource denial and native
HTTP inputs/identity; they do not prove a running external app or live Phoenix flow.


## Explicit relationship semantics

The Phoenix-compatible foundation retains the existing global relationship table
and edge identities. Migration `d6ab195f843c` adds `machine_semantic`,
`is_system_default`, `is_active`, and `affects_readiness`; it does not classify
legacy rows by their display names. Legacy meanings remain unset until an audited,
explicit administrator mapping is implemented.

Main API startup inserts missing built-in `contains`, `blocks`, and `references`
types from a packaged locale resource under a transaction advisory lock. This is
an idempotent global bootstrap, not a per-board reconciler. Renamed and disabled
defaults are preserved. The existing single and bulk deletion paths reject
built-in types, including a mixed bulk request, before deleting custom rows.
Relationship read responses and bounded MCP relationship projections retain the
explicit semantic fields.

This restores the data and response prerequisites only. Execution dependency
checks, hidden-blocker redaction, cycle policy, administrator semantic mapping,
localized relationship UI, and deployed REST/MCP/browser acceptance remain
pending. A `blocks` metadata row is not itself proof of an execution gate.
