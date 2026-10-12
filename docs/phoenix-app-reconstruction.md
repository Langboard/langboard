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

The host consumes the independent langboard-sdk 0.2.35 wheel with SHA256
0a73273c520e3789eb2c8aa54dbfed4fc2198d9897e56c03b771e2fb04bc433c.
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


## Direct dependency checks for human run intent

`CardDependencyState.card_dependency_state()` reads current direct `blocks` edges
on the primary transaction connection. `contains`, `references`, and unclassified
legacy display names do not participate. A prerequisite must be an undeleted,
unarchived card in the same board, in a current nonarchive column whose active
stage explicitly counts as completed. Archive alone does not satisfy a dependency.
Disabled relationship types do not introduce active prerequisites. Relationship types
and edges use the native hard-deletion model.

The response is limited to state, direct blocker count, and a digest of the
observed dependency revisions; prerequisite titles and identifiers are not
projected. Human execution-intent reads and writes reject unsatisfied
prerequisites, and the observed digest joins the configuration CAS snapshot.
This is not a runtime permit or a transaction fence for all concurrent card,
column, and relationship writers. Actual execution admission must revalidate the
current state under coordinated mutation locks. Runtime fences, cycle rejection,
MCP/UI blocker projection, and live acceptance remain pending.


## Transactional relationship replacement

The native relationship service now owns the transaction shared by REST and MCP
replacement callers. It locks the current project row, validates all proposed
cards and active types before deleting existing edges, and rejects a proposed
`blocks` edge if its dependent can already reach its prerequisite. Only active
`blocks` edges participate; contains/reference cycles are not execution deadlocks.
Pre-existing unrelated cycles do not prevent an independent replacement.

Relationship publication, activity recording and bot triggers are deferred until
commit. A PostgreSQL regression exercises three-card cycle rejection, invalid
replacement preserving existing edges, contains/reference reverse links,
rollback publication suppression, and two concurrent reverse prerequisite writes
where exactly one commits. Administrator semantic changes, all prerequisite
completion writers and runtime admission still need their coordinated fences.


## SDK request receipt read

The native app Bearer GET request path now matches SDK `AppExecution.read_request`.
It reads only the authenticated connection's human-created request, checks the
current requesting user, owning app, board grant, identity/resource selection,
workflow/dependencies and generation, and rejects a changed authority snapshot.
The SDK receipt is `requested`, `started=false`; it is neither acknowledgment nor
runtime permit. Human management keeps its separately authorized retained receipt
read even after disablement. App reads have no such stale-authority exemption.

The authority card revision uses integer UTC microseconds derived from the native
card timestamp. Runtime authorization, ACK/event validation, signed delivery,
lease issuance/recovery/heartbeat and start evidence remain unimplemented.


## Durable request event before delivery

Migration `e7bc206a954d` introduces a retained `AppExecutionOutbox` table with a
unique request identity and pending-state index. Human intent acceptance writes
exactly one execution-request event in the same transaction as generation and
request receipt. Stable event/request IDs, app/connection/board/card scope,
generation and selected resource IDs are stored; tokens and titles are omitted.
The records have no foreign keys so deletion cannot erase event evidence.

A duplicate request returns the original receipt without creating another event.
A failed event write rolls back generation and request acceptance. This is durable
storage only: pending events are not automatically delivered, signed, acknowledged
or authorized to run. Destination approval, bounded retry/worker claims,
revocation checks, signing and ACK receipt integration remain pending.

## Clean UI typecheck before Phoenix acceptance

Tailwind Variants 0.3.1 generates its responsive screen declaration when the
Tailwind configuration loads. A clean install initially contains default screens,
so checking TypeScript before a CSS build incorrectly rejects the existing `xs`
breakpoint. `yarn typecheck` first loads the existing Tailwind configuration through
its public loader, then checks both application and build configuration projects.
The regular UI build and Phoenix migration gate share this command. No screens
are duplicated and no type checks are suppressed.

The Tabbable plugin configuration also declares its public plugin type explicitly,
preventing TypeScript from naming an internal dependency file in exported types.
Clean generated-screen state and cleared incremental caches are verified locally;
the broader remote image, cluster, broker and analysis gates remain separate.

## App delivery acknowledgment

The native SDK acknowledgment route accepts only the request's owning connection,
matching event, board and card after current authority and generation validation.
Pending events cannot be acknowledged. An event must be marked delivering or
delivered by a delivery worker; that worker is not implemented yet. Tests explicitly
set this state as a fixture and do not establish signed delivery acceptance.

Acknowledgments are retained without foreign keys, unique by request and event.
Concurrent identical calls return the same receipt; changed runtime references
conflict. Credential revocation is serialized through the credential row and
authentication is repeated after the lock wait. Receipt writes and current
authority validation share the native atomic transaction. A failed write rolls
back; the SDK reports MutationOutcomeUnknown and must not infer success or retry
automatically. The receipt says received and started=false. It is not a runtime
permit. Signed delivery, runtime lease admission and HITL remain unimplemented.

The native GET on the same acknowledgment collection reports not_received or the
retained received receipt under current connection, generation and resource
authority. It does not renew or insert a receipt. After an ambiguous POST outcome,
clients can read before deciding what to do; not_received is only a snapshot and
does not authorize an automatic retry. SDK 0.2.34 adds read_acknowledgment and an explicit received/not_received union.
The host pins its independent wheel in all three existing locks. SDK-to-native
ASGI and PostgreSQL tests verify both receipt states without mutation retries.

## Human interaction reconstruction boundary

The prior app integration contains `AppCardInteraction`, immutable
`AppCardInteractionResponse`, choice validation, workflow destination snapshots,
and native human response controls. These are source material to reconnect, not
evidence of a working interaction endpoint on this review branch. The SDK request
and answer validators are available in the pinned 0.2.35 artifact; the host
interaction request/read/cancel and human response acceptance are reconstructed below;
reaction adapters, signed delivery and execution continuation remain pending.

The retained request/response models are reconstructed in schema-only revision
`a9de428cb76f`, after `f8cd317ba65e`. Requests retain authority and workflow
snapshots, expire explicitly, and are unique by connection/card/app request key.
Responses are unique by interaction and retain human actor, observed request
revision, idempotency key, optional execution generation and structured values.
Neither table has foreign keys that could erase audit receipts when a card or
account disappears. Database constraints reject nonpositive revisions, invalid
request states and nonpositive execution generations. These tables do not issue runtime permits; the native human response path is
described below.
The disposable PostgreSQL migration suite passed five tests in 2.19 seconds,
including uniqueness, constraint rejection, retained response after request
deletion and downgrade. The native request/response service and HTTP slice now
also pass a disposable PostgreSQL + ASGI test in 2.22 seconds: SDK create/read,
idempotent create, human-only response, concurrent answer CAS, conflicting
answer rejection, credential rejection, current resource/ownership fences,
response enqueue rollback, cancellation, expiry, no-lock GET, and workflow
mapping revision conflict. A normal full app-auth/foundation regression run
passed 78 tests in 55.84 seconds. `events.receive` is an explicit delivery
capability: without it, the human receipt is retained but no external event is
created.

The implemented host routes are intentionally split: app Bearer credentials
may create/read/cancel a request under current app consent; only an authenticated
human route may answer. Answering persists `io.langboard.app.interaction.answered.v1`
with app, connection, actor, values and optional execution context when event
delivery is granted. The event is a receipt, not a permit or resume. Comment
reaction presentation is rejected by the direct response endpoint. The native
comment reaction service accepts only a new human reaction on the exact declared
comment after request creation. It rechecks current card/app authority, request
revision, expiry, workflow snapshot and optional execution generation. At most
one pending reaction request may own a comment. Reaction, immutable response
and outbox insertion share one primary transaction; enqueue failure rolls all
three back. Unmapped reactions and bot reactions retain ordinary reaction UX.
Removing a reaction does not erase the immutable response. Ordinary reactions
without a pending request do not acquire the app policy mutation lock.

Disposable PostgreSQL exercised the real reaction repository/service, old
reaction rejection, direct endpoint denial, Seen exclusion, bot/outsider
exclusion, ambiguous request rejection, rollback on enqueue failure and durable
answer retention after reaction removal. Publishers and asynchronous activity/bot
dispatch were suppressed in that test; authority and database paths were not.

The earlier `AppCardInteractions` service depends on its previous
`AppExecutionGrant`, `AppExecutionAcknowledgments`, `AppEventDelivery`, and
`AppEventDestination` services. Its `AppCardScope` also assumes provider-era card
visibility and unattended-connection rules. Copying that service into the native
foundation would import a second authority engine. Adapt its retained models,
CAS response acceptance and workflow snapshots to current native connection,
card ownership, resource identity and policy checks instead.

Reconstruction must preserve the following boundaries:

- Requests require current owning-app consent for `interactions.request` and
  current selected resources. GET reads must not acquire mutation row locks.
- Only an authenticated human with current native card access and mutation
  authority can answer. An app Bearer credential cannot approve its own request.
- Persist the immutable question, request revision, current ownership/resource
  context and optional execution request/generation. Accept a response once;
  an identical idempotency retry reads the same receipt and conflicting values
  return a conflict. Expired, cancelled or superseded requests cannot be answered.
- Reaction approval requires a new human reaction on the exact declared comment
  after request creation. Seen, earlier reactions and unrelated reactions never
  constitute approval.
- An `approval` request records `approve` or `reject` explicitly. A question or
  action answer is not an approval. A runtime permit must verify the same current
  request, generation and immutable approved response revision; missing, pending,
  rejected or invalidated evidence denies admission.
- Choice outcomes target an explicitly enabled mapping to existing builtin
  workflow stages. Revalidate the saved mapping/column/stage revisions before a
  native move; answering alone is neither a move nor a runtime resume.
- Human response acceptance, event enqueue, signed delivery, app ACK and runtime
  admission are separate receipts. Keep card comments, relationships and
  checklists compatible with native card behavior.

At source `712fbd4c60024b1c2c8aa49def34961209f652a9`, native Python CI
`38186486009` and Phoenix CI `38186489750` both completed successfully. These
checks do not establish the missing interaction flow or deployed canary acceptance.
SDK CI `38186292284` was cancelled by workflow concurrency when the authorized
runner-recovery session dispatched `38187878432` against the same SDK source.
The authorized recovery session subsequently reported replacement run
`38187878432` successful at SDK source `913692621e17db21cd95e09185eaabfa8a18f0fe`.
The current host interaction source has not run remote CI or deployed acceptance.
Do not publish deployment details or treat local SDK tests as a successful remote run.

### Native interaction reception recovery

The owning app can POST an acknowledgment for the exact answered event and GET
the retained acknowledgment without retrying POST. The current connection, card
resources, owning app snapshot and optional execution generation are checked.
POST requires the event to be delivering or delivered; pending/foreign events
are denied. Exact retries return the original receipt; changing the event or
runtime reference conflicts. GET uses no mutation row locks. Both routes report
execution_resumed=false and workflow_transitioned=false. They reuse the retained
AppExecutionAcknowledgment model keyed by immutable response ID, so no new
schema or authority engine is introduced.

SDK 0.2.35 source is 97c398ce5edc87b8999b4c268dc158b5d61f25aa. Standalone
Python 94 tests and Node contracts passed locally. Remote CI for this new source
and native host delivery/Active lifecycle integration remain unverified.

Native PostgreSQL regression: 78 tests passed in 53.34 seconds, including
interaction HTTP/SDK, connection authentication, governance and foundation
migrations. A final focused interaction rerun covers rejection of tampered
answer payloads as well. Delivery state was advanced by the test fixture; this
does not prove a signed delivery worker, live runtime or deployed canary.

### Exact-byte native webhook signing

`WebhookTask.sign_webhook_bytes` implements the existing v1 HMAC protocol over
the exact timestamp and transmitted bytes. Ordinary `signed_request` delegates
to it after its existing metadata projection. App answer payloads can therefore
preserve question/response values and non-ASCII text without using the ordinary
webhook projection or creating a second signing format. The helper bounds body
size, rejects invalid header identity/version/timestamps and accepts the
existing unsigned administrator-webhook mode only when no secret is supplied.
App delivery must require a vault-backed secret at its destination boundary.

The standalone SDK verifier authenticates the raw app event; altered values,
added whitespace, wrong secrets and expired timestamps fail. Nine new cases
and the existing webhook contract regression passed together: 41 tests in
1.34 seconds. No app destination binding, network delivery worker or lease is
implemented by this helper. These remain necessary before deployed acceptance.

### Administrator-bound app destinations

Native AppEventDestination now binds one existing WebhookSetting to a connection
under instance administrator authority. Settings endpoints expose safe revision
receipts, explicit disable and rebind, not signing secrets. A signed destination
must use HTTPS, the existing URL policy and an approved app data origin. The
binding freezes webhook URL/secret-reference/event filters and app trust
fingerprint; edits invalidate the current destination until explicit reapproval.

Signature preparation rechecks current native app/card/resources and immutable
answer or execution intent before signing exact outbox bytes. The destination
UID/revision is frozen in the event and cannot silently follow a changed binding.
Existing configuration lock order precedes interaction/outbox row locks.
Execution authority remains Ready-only; archive events are denied pending native
lifecycle evidence. No network sending, retry queue or runtime permit is claimed.

Migration b0f1a2c3d4e5 follows a9de428cb76f and retains connection/webhook identities
without cascading foreign keys. Disposable PostgreSQL regression passed 79 tests
in 47.97 seconds, including destination migration roundtrip/duplicate connection
constraint; focused administrator API/signature tests exercise current refusal
after disable, URL edits, resource revocation and payload tampering.


### Retained app event delivery and recovery

The native Celery delivery task now claims retained events with a 60-second
lease, sends exact signed bytes through the existing public URL policy, and
rechecks current authority after DNS resolution. At most five delivery attempts
are admitted. HTTP 429/5xx and transport errors use bounded backoff; permanent
HTTP rejection and current-authority refusal terminate delivery. Event identity
is stable across retries, so receiving apps must deduplicate by event ID.

Claim, destination revision and outcome evidence is appended to the existing
outbox row, bounded by that attempt limit. Success preserves earlier failures;
expired claims retain an uncertain outcome, and a stale claim cannot overwrite
the current attempt. Receipts omit payloads, destination URLs and secrets. An
instance administrator can read delivery receipts and request pending-event
redelivery through settings APIs. Worker startup recovery pages only pending
or expired events in batches of 100. It does not reset failed attempt budgets.

Migration c1f2a3b4d5e6 follows b0f1a2c3d4e5. Queue delivery and ACK are transport
evidence only: neither starts runtime execution nor grants a workflow transition.
Live Celery/broker and canary acceptance remain required; local HTTP tests use
a real disposable PostgreSQL database and a controlled HTTP transport.

Disposable PostgreSQL regression passed all 80 tests in 47.69 seconds after
fixing a migration declaration error and test session-lifetime error. Coverage
includes 503 failure followed by success with retained history, expired-claim
recovery, stale-claim rejection, current revocation during DNS, and migration
upgrade/downgrade. Administrator history reads exclude signing secrets and
claim tokens. The interaction cancel HTTP adapter was also corrected to invoke
only the cancel service, with an HTTP regression added.


### Recovering a lost interaction publication receipt

SDK 0.2.36 adds `AppCardInteractions.recover(board_uid, card_uid, request)`.
The native collection GET accepts the bounded `app_request_key` and rechecks
current app/connection/card authority before looking up an app-owned request.
It returns the retained immutable request, current state and human response,
or a missing receipt with no request/response. It neither recreates requests
nor claims runtime continuation. Other app or connection receipts are not
exposed. Authority denial is not converted into a missing receipt.

The SDK compares the recovered request with the original validated input and
rejects mismatches. A caller must persist original input before creating the
request and use GET recovery after an uncertain response instead of a second
POST. Pending, answered and cancelled state are retained without fabrication.
Standalone SDK suites passed 95 Python tests and Node contract tests; actual
PostgreSQL plus ASGI/HTTP and the installed wheel exercises request equality,
missing, cross-owner isolation, current revocation and retained answers.

Final focused PostgreSQL/HTTP/installed SDK recovery regression passed in
2.85 seconds, including cancelled-state retention and no `FOR UPDATE` during
GET recovery. All three host lockfiles pass offline `uv lock --check`.
Source: SDK c344ac1f17d45e8655122e6d9cd229db5154c837; wheel SHA256
36b87d3b478e0c724f8c65fd33e92888fe9a48aed52cab9e8175465b7f1b0cf0.


### Native bounded runtime admission and stop-only recovery

The current native human intent and retained acknowledgment now back SDK runtime
permit admission. App Bearer authorization rechecks current request/generation,
resources, dependencies and credential under the shared policy lock. One lease
per request is retained with a SHA-256 hash of the caller's privately persisted
32-byte token. Same-input retries return the same lease; another token, expired
or terminal lease is refused. Admission stays separate from actual start proof.

A current-authority heartbeat renews a valid 120-second lease. Recovery with the
same private token never renews or creates a lease. Revocation or expiry changes
authorized state to stop_requested; a caller's stopped report is an attestation
and preserves stopped terminal state. The token remains usable for this stop-only
receipt after the app credential is revoked, without granting other app APIs.
The host cannot recall external changes and does not claim that reporting stop
proves process reaping. The caller must perform and verify that separately.

Migration d2f3a4b5c6d7 follows c1f2a3b4d5e6 and retains identities without cascading
foreign keys. Real PostgreSQL/HTTP/installed SDK admission tests verify concurrent
requests converge, wrong-token rejection, hashed storage, recovery without renewal,
heartbeat renewal, revocation and irreversible stopped state. Native Active
transition, start/report receipts and provider continuation remain unimplemented.

Disposable PostgreSQL suite passed 81 tests in 47.66 seconds; focused expiry
and stop-only regression passed in 2.66 seconds. The current-authority checker
also converts policy conflicts into stop_requested. Operational admission is
not complete: current Ready-only configuration means an Active move is refused
until native start/report and an explicitly fenced Active snapshot are added.


### Immutable native start reports and current runtime evidence

The native start-report API now matches SDK `report_start`. A current live
permit, app credential, request/generation and private runtime token are
required. Concurrent identical reports retain one immutable receipt; a different
execution reference is refused. Reports are explicitly `app_attestation`, not
independent proof that an external process started, and do not move the card.

SDK `runtime_report` reads current permit state and retained start evidence
without renewal. Before a start report its evidence kind is none; after a
report it exposes the retained start reference. Revocation, expiry or stopped
state clears active_report while preserving the original start evidence. Raw
runtime tokens remain private and are not stored in start receipts.

Migration e3f4a5b6c7d8 follows d2f3a4b5c6d7. Focused real PostgreSQL/HTTP/installed
SDK regression passed in 2.67 seconds. Ready-to-Active workflow handoff and
fenced Active snapshot renewal remain outstanding; report_start itself follows
the existing SDK contract and intentionally has no workflow side effect.

Disposable PostgreSQL full regression passed 82 tests in 47.07 seconds,
including both lease/start migration upgrade, duplicate-identity rejection and
downgrade. SDK Node 22 remote contract validation revealed a separate panel
fixture wait/cleanup defect; its test-only fix b41722b82611bda015cdc687abb64d69f6d63de7
is being validated remotely. This does not change the installed 0.2.36 wheel.


### Explicit Active handoff and immutable ambiguity receipt

SDK 0.2.37 adds `activate_runtime` and `read_active_handoff`. The mutation follows
a retained app start report and requires current live lease/token/credential,
original observed card/binding revisions, an explicitly mapped built-in Active
column and clear dependencies. The existing native card ordering/movement path
is reused; its socket/activity/webhook publication is deferred until commit.
A failed handoff receipt insert rolls back the card move without publishing it.

The original human execution intent remains immutable. A separate retained
Active receipt records request/card/lease/generation, original inputs, destination
and the complete post-transition authority snapshot. Active heartbeat validates
that snapshot; it does not ignore card edits, resources, ownership, mapping,
policy, dependencies or generation. Moving outside the mapped column refuses
continued execution. Same-input replay retains one receipt; different original
inputs are refused.

Receipt recovery is a read-only POST with the private runtime token in the body,
not the URL. It does not renew a lease, dispatch a process or repeat the Active
mutation. Recorded and not-recorded results retain scope identity. The existing
report_start remains free of workflow side effects. Actual process execution and
reaped-stop/Review handoff still require independent acceptance.

Standalone SDK Python 96 tests and Node contracts passed. The installed wheel
PostgreSQL/HTTP focused test passed in 4.48 seconds; full migration/current
authority regression is pending. SDK036 test-only Node22 fix b41722b remotely
passed CI38192139500, independently of the new037 runtime artifact.

Disposable PostgreSQL full suite passed 83 tests in 47.94 seconds, including
Active migration roundtrip, duplicate lease identity denial, current revision
fences and no publication on rollback. Final native ordering-helper focused
regression passed separately. All three offline host locks are consistent.
SDK037 exact-head remote CI38192655354 passed for d138622. This confirms the
SDK contract gate, not deployed host or provider acceptance.

### Active HITL authority continuity

Request receipt reads, heartbeat, human interaction responses and signed event
authority now reuse one current execution configuration check. It selects the
immutable Ready intent or the separately retained Active snapshot. Active also
requires the current authorized, unexpired lease and connection credential.
The original intent remains unchanged; SDK request reads return the current
Active authority version and card revision after the explicit transition.

The installed SDK created a question through the native HTTP API after Active;
a human response and its event authority validation succeeded. Revoking the
resource then denied request reads, responses and event delivery. Disposable
PostgreSQL full regression passed 83 tests in 48.80 seconds. Actual provider
execution, stopped-to-Review handoff, independent Monitor acceptance and canary
browser/broker acceptance remain outstanding.

### Stopped-to-Review handoff

The existing SDK `request_review` and `read_review` contracts now have native
host routes. A retained start, Active handoff and terminal stopped lease are
required. Current actor, app, consent, selected resources, execution generation
and the complete retained Active snapshot still gate the transition. An expired
or stopped permit is never reused as runtime permission. The explicit built-in
Review mapping must be active, non-archived and non-completing.

The mutation compares the original observed card/binding revisions and stores
all four original inputs, including the app's evidence reference. It uses the
existing native movement path and retains a separate post-Review snapshot.
Identical concurrent calls converge on one receipt; changed input is rejected.
A receipt persistence failure rolls back both movement and publication. Neither
the mutation nor recovery approves the result, marks Done or resumes execution.

Recovery GET returns `recorded` with the retained receipt or `not_recorded` with
current revisions under current authority. Consumers may retain Active revisions
before stop, or read `not_recorded.current` after stop before their first attempt.
An ambiguous mutation must use recovery with frozen inputs, not repeat the POST.
Current revocation also denies recovery. The installed SDK/HTTP/PostgreSQL focused
regression passed in 3.13 seconds. Full PostgreSQL regression passed 84 tests
in 48.31 seconds, including Review migration roundtrip and unique receipt identity.
Provider termination remains an external attestation acceptance gate.

### Native current app-owned card content

SDK `AppCards.read` now has its native GET route. It authenticates the inbound
connection and reuses current owning-app/card/resource configuration with
`cards.read` consent. It returns the existing Markdown in windows of at most
16,000 characters, total size and next offset, title, source connection,
execution generation, card/ownership/selection/binding revisions and scoped
external resource identities/access revisions. Reading neither issues nor
renews a runtime permit and remains available after a reported stop.

Installed SDK HTTP/PostgreSQL acceptance passed in 3.13 seconds: complete Korean
content across pages, end-of-content, invalid windows, unowned card refusal,
post-stop revision consistency and resource revocation refusal. This checkout's
existing app configuration requires board update authority and does not contain
private/whisper/shared card visibility models. This change does not claim that
cross-union visibility acceptance or read-only board-role support is complete.
Actual consumer and independent app acceptance remain required.

### Independent Monitor wrapper acceptance

The independently maintained Example Monitor source c9ae74a was built as its
own 0.1.2 wheel and installed without editing host runtime code or monkeypatching
its SDK methods. Wheel SHA256:
`125b13e6108f5a62161baa10175d0cbae6f35e8093cc1468fdd94b42e5b5c060`.
Its signals-only baseline declaration is unchanged; the disposable fixture
explicitly declares and grants HITL scopes and assigns an app-owned card.

The actual `Monitor.question/recover_question/current_response/acknowledge_response`
methods called native ASGI HTTP over real PostgreSQL. Missing recovery, immutable
request recovery, changed-request refusal, a real human response, receipt-only
ACK and current resource revocation were exercised. Exactly one interaction and
one response were retained, with no card move or execution resume. The queue
transport was suppressed and delivery was marked confirmed by the fixture;
this is not live broker/provider acceptance. The SDK037 environment passed in
1.95 seconds. The pinned SDK036 isolated environment result is recorded below.

The optional test skips when the independent package is not installed; a skip
is not acceptance. Registry installation/lifecycle acceptance and current wrapper
HITL acceptance are separate proof layers.

The isolated environment installed the Monitor repository's pinned SDK036 wheel
and the built Monitor wheel. It passed the same native HTTP/PostgreSQL test in
2.11 seconds; the log confirms SDK0.2.36 loaded from that environment. An initial
collection failed because host-only boto3 was absent; host dependencies were
added as test-runtime search paths while the independent SDK036 remained first.
No SDK methods or authority decisions were replaced.

### Installed consumer Review acceptance

The independently packaged consumer wheel was built and installed with SDK037.
Wheel SHA256:
`93981e679526b754677f7b86c68b15ab51161930561655973d010d9208ae0af9`.
Its actual `deliver_review` and `reconcile_review` called native card-read,
stop-report, Review mutation and Review recovery routes over ASGI/PostgreSQL.
The consumer's real SQL runtime store retained original inputs and the native
receipt. Recovery used a new database Session and did not repeat the mutation.
The native handoff and consumer result each retained one receipt.

Only external PR facts and process-reap evidence were fixture data; current
reap checks, durable storage, SDK methods and native authority were unchanged.
This proves protocol/storage integration, not a live GitHub result, real provider
termination, broker delivery or deployed operation. The private local harness
and logs remain in `.verification/consumer-http-postgres.*`; provider-specific
harness code is not added to the generic host source. An initial pytest plugin
version conflict was resolved by disabling plugin autoload; no product check
was bypassed. Union visibility and operational acceptance remain outstanding.

The same harness also called the actual installed consumer `read_requested_card`
against native full-content pages and before/after execution authority reads.
It reconstructed the full Korean Markdown and matched the native authority.
Final combined content/Review/recovery PostgreSQL run passed in 3.17 seconds
with two pytest configuration warnings after plugin autoload was disabled.

The shared native execution fixture now has an optional independent consumer
hook instead of maintaining a copied native test. Set
`LANGBOARD_EXECUTION_CONSUMER_TEST_MODULE` to an importable installed test module
exporting `APP_KEY`, `read_requested_card` and `exercise_review`; provide that
consumer's package and dependencies in the test environment. No configured
module skips the extra branch and does not count as consumer acceptance. Run
`pytest src/graph/tests/test_app_connection_auth_postgres.py::test_human_execution_intent_cas_idempotency_and_current_resource_fences`.
Both the ordinary and installed-consumer branches passed in 4.69 seconds against
disposable PostgreSQL. The provider-specific helper remains consumer-owned.

### Read-only board role for current app content

The current app/card/resource scope now passes the requested board action through
its existing consent/configuration checks. `cards.read` uses the native `read`
role; other capabilities retain the existing `update` default. A read-only
member owning the personal connection can read the current app-owned card, but
cannot read execution-request authority or change configuration. Removing `read`
immediately denies content. The focused installed-SDK PostgreSQL test passed
in 3.63 seconds (one optional consumer branch skipped without a configured
module). App consent, connection ownership and selected-resource checks remain
independent of the human board role.

Retained union source has `CardVisibilityPolicy` plus the primary membership
resolver and internal access modes. The Phoenix base lacks the associated
schema/configuration/SCIM employee policy. These dependencies must be integrated
together; this role fix does not replace or claim the union visibility gate.

The regression-count audit found that the consumer hook preparation had
accidentally narrowed two pre-existing policy test axes to `[True]`. Both
`organization` and `existing_policy` axes are restored to `[False, True]`.
The reduced 75-test result is not the complete prior regression scope; the
restored suite result is recorded after rerun.

Restored full PostgreSQL regression passed 84 tests in 49.00 seconds; the
optional unconfigured consumer branch skipped once. Ruff and diff checks passed.


### Same-human, same-board app isolation

The installed-SDK native PostgreSQL execution fixture now registers a second
approved app, connected credential, enabled board binding, owned card and
selected resource under the same human and board. Each app reads its own card;
cross-app card reads, interaction reads/recovery/create/cancel, execution-request
reads and Review reads/writes return 403. The second app can create and read its
own pending question, and a foreign cancellation leaves it pending. No foreign
Review receipt or card transition is persisted. Interaction POST uses the actual
`request` envelope; the first attempt's 400 was malformed test input, not scope
acceptance. The first app question was removed by earlier fixture cleanup, so
its UID checks prove rejection at the card scope; the second app question is a
live row and proves rejection without destroying the owner's request.

Focused ordinary fixture passed in 3.47 seconds (unconfigured consumer branch
skipped). With the independently installed Symphony consumer, both branches
passed in 4.91 seconds. Ruff and diff checks passed. These checks use disposable
PostgreSQL and native HTTP; they do not prove deployed visibility, signed broker
delivery or a real provider process lifecycle. The broader acceptance remains
open.
