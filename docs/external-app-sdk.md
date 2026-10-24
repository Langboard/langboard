# External service app SDK

An external Langboard app runs as a separate service. Install its public panel
package, connect an account, select resources and explicitly grant the board
capabilities it needs. Langboard retains control of current permissions, board
settings and native card operations. An app definition cannot upload executable
server code, select an authenticated principal or replace native authorization.

This guide describes the API and package behavior of this repository version.
Availability on an instance depends on its installed host/SDK versions and
configuration. An accepted request, delivered event, runtime permit and reported
process start have distinct meanings; none alone establishes completed work.

## SDKs and examples

- [Python installation and API guide](https://github.com/yamonco/langboard-sdk/blob/main/packages/python/GUIDE.md): caller-owned authenticated REST/MCP transports,
  registry management, workflow mapping, card presentation and resources.
- [Panel SDK guide](https://github.com/yamonco/langboard-sdk/blob/main/packages/app-panel/README.md): isolated browser lifecycle, host theme resources and session drafts.
- [Native command composition](native-extension-contract.md): native host commands.
- [App human interactions](app-human-interactions.md): requests, human responses,
  acknowledgment and workflow application.
- [Example monitor app](https://github.com/yamonco/langboard-app-example-monitor): an independent consumer of public SDK/API contracts.

Python clients use a caller-owned transport. Keep native user credentials separate
from an app's dedicated connection Bearer credential. SDK clients do not silently
start work, refresh tokens, crawl pages or retry mutations. After an uncertain
write, read the corresponding saved state before deciding whether to retry.

## Registration, updates and board consent

An instance administrator approves a versioned app definition. Built-in keys
cannot be overwritten. Updates require the current revision and a newer version.
Approval allows discovery; a board administrator must separately grant specific
capabilities. A panel-only app may receive `panels.render` without card mutation,
workflow transitions or automation authority.

Same-trust updates retain the intersection of previously approved capabilities.
Added capabilities need new consent. Changing publisher, authentication/MCP/data
origins, MCP identity or the panel URL clears affected grants and revokes affected
connections. Disabling registration clears board grants. A declaration-only update
preserves installed package metadata. Restoring an old declaration does not
restore withdrawn consent.

Prepare a binding with
`POST /board/{project_uid}/settings/apps/{app_key}/consent`, sending the current
`app_revision`. It creates a disabled draft with no grants or automatic workflow
transitions, including for apps without workflow requirements. Repeating
preparation returns the existing binding without resetting it.

Use `PUT` on the same path to replace grants. Supply `app_revision`, `binding_uid`,
`expected_revision` and an explicit unique `capabilities` list drawn from the
approved declaration. Current board update authority and effective policy are
required. Stale revisions return 409; duplicate or excess capabilities are
rejected. Clearing all capabilities disables the binding and remains available
for cleanup after policy or registration disablement. This does not install an
app, create a connection, select a resource or enable transitions.

## Workflow destinations

Apps use built-in workflow stage keys and existing board columns. Board settings
provide the mapping editor for approved apps with workflow requirements, including
apps without a panel. Each board/app binding has its own destinations; two apps
can select different columns for the same stage.

A unique stage match can be saved automatically when a mapping or new consent
draft is prepared. Missing or ambiguous stages remain unmapped. Apps without
workflow requirements have an empty mapping. Preparation grants no capabilities
and leaves transitions disabled. Repeating preparation preserves saved choices.

Runtime actions use the saved destination. Adding a second column with the same
stage does not redirect an existing app. Deleting the saved column or changing
its stage invalidates it. Create or assign a replacement stage/column, then
explicitly select and save that destination with current revisions. Repairing a
column alone does not redirect the app or enable transitions.

The mapping editor also offers **Manage stage columns** for a resolved stage.
An administrator can assign the stage to an existing unclassified column, then
explicitly select and save its UID as the app destination. Existing destinations
remain unchanged until that save. Names are never used to infer completion, and
this flow neither creates duplicate columns nor moves cards. Assigning a stage
changes the meaning and counts of every card in that column; review and approval
evidence remains independent.

Enabling transitions with a missing, ambiguous, deleted or changed required
destination fails atomically, preserving both the previous mapping and transition
setting. Save valid explicit destinations before opting into automatic transitions.

## Policy and connection ownership

Instance policy sets the ceiling: `disabled`, `approved_only` or
`personal_allowed`. Organization policy can restrict it further. Personal app
distribution and personal account ownership are separate concepts: an approved
organization app may use a user-owned account for that user's requested work.

Unattended work on organization boards or boards with collaborators requires an
organization-owned connection matching the board's active organization. Personal
unattended work is limited to the account owner's unshared personal board.
Deleted boards cannot authorize apps. Instance administration and board ownership
do not expose another user's personal connection, credentials or private data.

Organization owners and instance administrators manage Apps policy in Settings.
`GET /settings/apps/governance/organizations` returns authorized active
organizations with `uid` and `name`; `limit` defaults to 25 and is at most 50,
and `cursor` follows the previous page. Administrators see active organizations;
other users see only organizations they own. Per-organization GET/PUT at
`/settings/apps/governance/organizations/{uid}` rechecks management authority and
revision. `mode: null` inherits instance policy. Discovery does not grant authority.

Board catalogs remain readable with current board read access under disabled
policy. A disabled definition is shown only for an existing retained binding,
with removal revisions and no declared capabilities. `is_available` indicates
current policy/registration availability; it is not execution approval.

## Connect an inbound service

For an approved external app, use
`POST /settings/apps/registry/{app_key}/inbound-connections` with its current
`app_revision` and optional `organization_uid`. Personal connections belong to
the caller. Organization connections require the organization owner or instance
administrator. The request accepts no upstream URL, secret reference, external
account identity or capabilities. It establishes a Langboard inbound identity;
it does not verify an upstream login. Built-in providers use their native verified
connection routes instead.

Recover receipts with `GET` on the same path. The default scope contains only the
caller's personal connections. An explicit `organization_uid` requires current
management authority. Use `after` and `limit` (1–50, default 25). Results contain
ownership, state and disconnect revision, without credentials or upstream account
data. Recovery remains available after policy/app disablement.

The board App Store offers **Manage service connections** when the catalog
supports inbound connection management. Register, refresh or disconnect explicitly.
Failed writes require a successful manual refresh before another mutation.
Resource selection, **Review permissions** and credential issuance are separate
controls. Grant only the displayed scopes needed for the integration.

Disconnect with
`POST /settings/apps/inbound-connections/{connection_uid}/disconnect`, supplying
`expected_revision`. Disconnection invalidates existing credentials and prevents
new issuance, while preserving retained connection/history metadata.

## Select resources and manage credentials

Use GET/PUT at
`/board/{project_uid}/settings/apps/{app_key}/inbound-connections/{connection_uid}/resources`.
Management requires both current board management and connection ownership.
Supply app revision, binding UID/revision, declared `resource_type` and
`external_resource_id`. Each stable `resource_uid` identifies one selected
external resource; it grants no permission inside the external service.
Multiple selections share a connection without replacing one another.

Subsequent writes require the current `expected_access_revision`; stale writes
return 409. Set `selected: false` to remove access. Bounded receipt reads use
`after` and `limit` (1–50). Cleanup remains available after app/policy disablement
and returns no credentials or upstream data. Selecting resources grants no board
capability, credential or workflow transition.

Issue a credential with
`POST /settings/apps/connections/{connection_uid}/credentials`. Optional
`expires_in_seconds` is an integer from 60 to 86400, default 3600. The response
returns `credential_uid`, `connection_uid`, `token` and `expires_at`. The token is
shown once. Store it securely; the host retains only its hash. Responses use
`Cache-Control: no-store`. Personal management is owner-only; organization
management requires current organization authority.

`GET` on that path returns bounded credential history (`limit` 1–50, default 25;
`after` is the credential cursor), including creation/expiry/revocation timestamps
but no tokens, hashes or provider secrets. After lost issuance, inspect history
and revoke the affected credential before explicitly issuing a replacement.
Do not automatically repeat issuance. Revoke with
`POST /settings/apps/connections/{connection_uid}/credentials/{credential_uid}/revoke`.
Revocation is idempotent and remains available after app/policy disablement.
The manager clears a newly displayed token on hide, refresh, paging or exit.

Send `Authorization: Bearer <token>` to `GET /apps/v1/identity`. Its version-1
response identifies the app, connection, credential and optional organization.
Identity comes from the saved connection. Expiry, revocation, connection revision,
trust changes and current policy can invalidate credentials; unchanged same-trust
version updates preserve them. An app credential cannot authenticate as a user
or bot, and identity alone grants no card mutation or execution capability.
Provider-backed connections cannot issue these host inbound credentials.

Resource approvals are bound to the approved connection identity. An identity
change requires renewed approval; issuing a new credential does not restore
resource access approved under an earlier identity.

## Read resources and select card execution inputs

`GET /apps/v1/boards/{project_uid}/resources` accepts the connection Bearer
credential. Use `limit` (1–50, default 25) and `after` resource cursor. The response
contains `schema_version`, `connection_uid`, `binding_revision`, `items` and
`next_cursor`. Invalid credentials return 401; missing current authority returns
403. Pages recheck current policy, owner membership, `resources.read` declaration
and grant, connection scope and selected access.

Items expose resource UID/type, external ID/path, access revision and health,
without secrets. A health observation is not an execution grant. Python
`AppResources(app_transport).list(board_uid, limit=25, after_uid=None)` fetches one
explicit page; pass `next_cursor` to fetch another.

Human administrators select a card's execution resources with GET/PUT at
`/board/{board_uid}/card/{card_uid}/apps/connections/{connection_uid}/resources`.
PUT accepts at most 20 distinct `resource_uids` and `expected_revision` (null for
initial selection, otherwise the current positive revision). Current card
visibility, administrator/connection-owner authority, app ownership, consent and
selected resource access apply. Stale writes return 409. Empty selections unlink
resources after revocation without granting execution. Another user's personal
selection cannot be read or cleared by an administrator.

The card resource editor and native MCP `get_card_app_resources` /
`set_card_app_resource_selection` use the same current permissions and revisions.
Read unknown outcomes before another write. GET at
`/board/{board_uid}/card/{card_uid}/apps/resource-selections` returns up to 25
retained selection receipts per page for cleanup, including disconnected
connections. Python `CardResources.list_selections` fetches one page explicitly.

## Card ownership, creation and native compatibility

Human-authenticated GET/PUT at
`/board/{project_uid}/card/{card_uid}/app-ownership` reads or assigns an owning app.
Assignment requires current administration and approval; it cannot expose another
user's private card. Initial assignment uses null `expected_revision`; transfer
or release requires the current revision. Release remains possible after app
disablement. App Bearer credentials cannot claim arbitrary existing cards.

App ownership is separate from visibility, presentation, creator and private
owner. Users and user-delegated MCP clients retain native editing, comments,
reactions, attachments, labels, relationships, checklists and timers with their
existing permissions. An ordinary native edit does not approve a pending app
interaction. Unbound automation cannot use native mutation paths to impersonate
the owning app. See [native card compatibility](app-human-interactions.md#native-card-compatibility).

`POST /apps/v1/boards/{project_uid}/cards` creates an external issue using the
connection credential, `resource_uid`, stable `external_id`, `title`, declared
`card_type`, optional `description` and optional `presentation`. The approved
`card_lifecycle` must contain that card type. `external_id` and title are limited
to 200 characters; description to 100000. Declaration and board consent must grant
`cards.create` and `resources.read`; presentation also requires
`cards.presentation` and a key in `app.<app-key>.*`.

Current connection ownership, native card permissions and resource access apply.
The destination is the binding's saved built-in `backlog` column. Archived,
deleted or differently staged destinations are denied. Creation produces INTERNAL
cards, owning-app lineage and the initial resource selection. It cannot create
human-only PRIVATE cards or grant SHARED visibility through display metadata.
Creation does not grant execution or enable workflow transitions.

The source identity includes board, connection, resource and external issue ID.
New creation returns 201; an identical explicit replay returns 200 with the same
`card_uid` and `receipt_uid`. Changed input under that identity returns 409 and
does not overwrite the original. Current authority is checked again before
replay; deleted or unreadable cards are denied. Later issue edits require a
separate authorized operation.

`effects_state` reports pending, dispatched or failed native side effects.
A failed effect preserves the committed card and sanitized error. Replaying card
creation does not automatically resend potentially delivered effects. There is
no exactly-once external side-effect guarantee.

App credentials can read their own currently authorized card through
`GET /apps/v1/boards/{project_uid}/cards/{card_uid}` with `cards.read` consent.
Use `offset` (nonnegative) and `limit` (1–16000, default 16000) for description
windows. The response includes native `card_revision`, `execution_generation`,
resource/ownership/binding revisions and a `next_offset`; it grants no execution
authority. Current ownership, selected resources and connection scope are
rechecked on every read.

## Execution requests and runtime permits

Declare and obtain explicit `execution.run` consent separately from resource
reads. `GET /apps/v1/boards/{project_uid}/cards/{card_uid}/execution-authority?generation=N`
uses the app credential and checks current ownership, policy, visibility, resources,
consent, saved ready destination, prerequisites, approvals and positive generation.
An eligible result includes `authority_version` and `started: false`. It is an
inspection, not a transferable mutation token or runtime lease.

POST to the same card's `/execution-requests` with only `generation` and
`expected_authority_version` (64 lowercase hexadecimal characters). Authority is
rechecked when accepting the request. A version mismatch returns 409 without
accepting an event. Current retries reuse the same request/generation identity;
changed accepted authority conflicts and revoked authority denies replay.
Python `AppExecution.authority` and `.request` use these routes. Generation-only
requests from SDK 0.2.5 are incompatible; use the authority-version contract.

Acceptance returns `state: requested`, `started: false` and the accepted fixed
snapshot. Request and event identities are stable. Read the request with GET at
`/apps/v1/boards/{project_uid}/cards/{card_uid}/execution-requests/{request_uid}`.
After receiving the matching event, POST its event UID and bounded runtime
reference to `/acknowledgments`. An idempotent `received` acknowledgment still
reports `started: false`. Changing its runtime reference conflicts. Current
connection, card, resource and event authority remain required.

After acknowledgment, explicitly request a runtime permit with a privately
stored random 32-byte token encoded as 64 lowercase hex characters. The permit
lasts 120 seconds. `permit_execution: true` authorizes the receiver's own work;
it does not start a process. Explicit `check_runtime` heartbeats recheck current
authority and renew a valid permit. Revocation, resource drift, stage departure
without a valid Active handoff, or expiry requests a stop. The receiver must stop
when `permit_execution` is false. Stop acknowledgment records `stopped`; stopped
or expired permits never resume automatically.

Store the runtime token durably before requesting authorization. After an
ambiguous authorization response, do not dispatch. `recover_runtime` resolves an
existing permit without creating, rotating, renewing or resuming it. Recovery
with a scoped runtime token remains possible after app credential revocation,
but cannot restore permission. Replacement app credentials cannot take over an
existing permit. Keep tokens out of URLs, logs, event payloads and references.

`report_start` records the app's observed process start using its app credential,
live permit, runtime token and bounded execution reference. Identical reports
reuse the receipt; different references conflict. The response is
`state: start_reported`, `evidence_kind: app_attestation`. It is the app's claim,
not independent Langboard process observation. It does not renew the permit,
move a card or mark completion. Runtime timestamps include a UTC offset.

`runtime_report` checks current permit and start attestation without renewal.
`active_report: true` means current authorization and a retained start report;
it does not establish process health or progress. Old start history remains
readable with `active_report: false` after authority ends. Heartbeat renewal is a
separate explicit operation.

### Active, Review and human controls

POST to the request's `/active` route with app credentials, `lease_uid`,
`runtime_token`, `expected_card_revision` and `expected_binding_revision` to
explicitly move to its saved Active destination. This requires a live permit,
matching start report, declared Active workflow requirement and enabled
transitions. `/active-receipt` reads the saved handoff without replaying it.
A valid handoff allows continued runtime authority in Active; it does not
restart or resume a process.

POST to the request's `/review` with `generation`, observed card/binding revisions
and bounded `evidence_reference` to request its saved Review destination.
Current app authority, a stopped permit, matching start report, Review workflow
requirement and enabled transitions are required. Identical inputs reuse the
saved receipt; changed inputs or destination revisions conflict. GET reads the
receipt without moving the card. Review records an app attestation, not human
approval or completed work.

Panel runtime listing uses
`POST /board/{project_uid}/apps/{app_key}/panel/runtimes` with `card_uid`, optional
`cursor` and current app/binding revisions. It returns bounded retained runtime
identities without tokens. Human stop/resume routes under
`/panel/cards/{card_uid}/runtimes/{request_uid}/{stop|resume}` require current card
update authority, app/binding revisions and observed generation. Stop requests
revoke permission; they do not independently prove external process termination.
Resume requires a newer producer-issued ready generation and preserves stopped
lease history.

Panel SDK `runtimes.open` accepts only `card_uid` and opens a host confirmation
dialog. Opening it is not approval. Direct SDK stop/resume/new operations are
rejected. After panel disablement, authorized native card controls remain
available for retained-runtime cleanup. Apps must not automate human controls.

## Signed events and delivery recovery

The board readiness webhook is not an app-scoped execution grant. In particular,
`execution.is_ready` alone does not establish current app consent, connection
ownership, selected resource access or human approval. Receivers must obtain
current native runtime authorization before acting.

App request acceptance retains one `io.langboard.app.execution.requested.v1`
event with stable request/event identities. Events carry app/connection,
board/card, generation and selected resource identities without credentials or
the complete authority snapshot. A saved event represents delivery intent until
its separate delivery and acknowledgment steps succeed.

Instance administrators configure GET/POST at
`/settings/apps/connections/{connection_uid}/event-destination`. Binding supplies
`webhook_uid` and `expected_revision` (null initially). The approved app declaration
must include `events.receive`. The destination must use an approved HTTPS origin, an allowed event and a signing secret. Target, secret,
allowlist or trust changes require explicit rebinding. Accepted events cannot be
silently retargeted; signatures include the pinned destination UID/revision.
POST `/event-destination/disable` requires its current revision.

Delivery rechecks authority and destination before sending, uses bounded retries
(up to four attempts), and preserves delivery history. Concurrent delivery is
leased for 300 seconds; late results cannot overwrite recovered claims. Current
revocation can block an event without deleting history. The HTTP transport checks
public destinations, follows no redirects and uses a bounded timeout. A change
after the final authorization check cannot undo an external effect already sent.

Instance administrators inspect
`GET /settings/apps/events/{event_uid}/delivery` and explicitly request
`POST /settings/apps/events/{event_uid}/redeliver` for pending or delivering events.
This queues an explicit attempt; it does not reset an exhausted retry budget.
Pending-event recovery and worker execution depend on instance configuration; a saved event or delivery
receipt is not proof that an external runtime started. Revocation does not claim
that an external action was reversed.

### Verify webhook bytes

Use `verify_webhook` on the exact raw body before JSON parsing or side effects.
Headers are case-insensitive; duplicate authentication headers are rejected.
Verification requires version 1, HMAC-SHA256, a nonempty secret, age at most five
minutes, at most 30 seconds future skew and a body no larger than 1 MiB.
Keep receiver clocks synchronized.

```python
import json
from langboard_sdk import verify_webhook

verify_webhook(raw_body, request_headers, configured_signing_secret)
event = json.loads(raw_body)

# Commit event_id atomically with the business effect and return its saved

# receipt for a duplicate delivery.
```

Signature validity does not grant board/card access or execution authority.
Validate event identity and current authority separately. The verifier rejects
unsigned input even if a generic webhook was configured without signing.

## Signals and evidence

An optional `signal_policy` declares version 1, finite unique `event_types`,
a subset of the app's `resource_types`, finite `outcome_states`, `commit_mode`
and `time_basis`. Time basis accepts a scalar `provider_occurrence` or
`observation`, or a complete map from every declared event type to one of those
values. The host retains the bounded map as immutable entries and resolves the
selected event's scalar basis when presenting evidence. Generic occurrence events
use `external_event`; generic observations use `external_observation`. Built-in
check, issue observation and deployment meanings are preserved.

Inbox and card evidence keep their existing external identity grouping and
occurrence ordering. Equal-time rows with different declared bases have null
`time_basis` and generic `evidence_kind`, including when their outcomes agree.
The selected `signal_uid` and mixed-policy `event_type` identify the same stored
row; they do not resolve the aggregate's ambiguous time semantics. Mixed policies
with no proof likewise expose null basis and kind. Stale proof retains its
selected-row provenance while remaining `stale`. Contradictory outcomes remain
`conflict`; unknown semantics do not become check evidence. A changed map entry or
scalar/map representation withdraws affected signal consent and requires current
revisions and renewed grants. Map entry order alone preserves consent.

`signals.publish` requires this policy and separate board
consent. Changes to policy/resource scope withdraw affected signal consent while
preserving unrelated grants; reordering an unchanged set preserves consent.

`POST /apps/v1/boards/{board_uid}/signals` accepts at most 64 KiB of exact signed
JSON bytes. Use the inbound connection Bearer credential and v1 webhook headers
with that credential as the HMAC key. Version 1 contains `board_uid`,
`resource_uid`, current `app_revision` and `binding_revision`, `event_id`,
`event_type`, timezone-aware `occurred_at`, `external_id`, `outcome` and
`commit_sha`. Unknown or duplicate JSON fields are rejected. Current publication
consent, selected access, board update and unattended connection authority apply.
Provider-backed connections also require their current provider secret authority;
inbound identities use their valid host credential.

Identical resource/event retries return the saved receipt. Harmless JSON
whitespace changes do not change replay identity; changed content conflicts.
Out-of-order observations remain retained while projection uses occurrence order.
`AppSignals.publish` sends one signed request without automatic mutation retry.
The response does not create a card, triage an inbox or change workflow. Manual
linking/creation and app-owned workflow cards have their own permission boundaries.

Evidence states come from approved policy. Unmapped outcomes remain `unknown`;
contradictory observations remain `conflict`. `deployment`, `issue_observation`,
`external_observation` and ordinary checks retain their distinct meanings. A
reported running check is not automatically external execution or reviewer
approval. Revoked evidence is hidden while history remains saved; cursors into
revoked resources fail. Personal connection data remains owner-only even for
administrators or callers with a previously known cursor.

## Panel lifecycle, reads and drafts

Only the selected visible panel is mounted in an isolated frame. Every mount
reads current board access and consent. Closing or disposing a panel cancels its
requests. Provider credentials are never passed into the iframe.

`resources.list` uses the current app/board and app/binding revisions supplied by
the host, accepting only an optional bounded cursor. Native reads require both
`panels.render` and explicitly granted `resources.read`. Each page rechecks owner,
connection, declared types, selected resources and policy. Pages are bounded to
25 resources. Older hosts may reject this operation; it was added in panel SDK
0.2.1 without changing protocol v1.

`signals.list` accepts a bounded adapter key. External declarations must name
1–16 unique `signal_sources` and request `signals.read`. Read consent covers only
the approved sources and resource types; it cannot install an adapter or access
another account. A source change withdraws signal read consent; order alone does
not. Old declarations without explicit sources must be updated and consented
again. Unsupported sources are denied. Registered external signal policies use
the signed ingestion contract above; panel registration alone cannot install a
native provider adapter.

Resource/signal responses 401/403/404/409 invalidate the frame, clear cached panel
state and abort pending requests. Reopening requires fresh authorization.
Transport and 5xx failures are request failures; they do not silently declare
revocation. Global app-change invalidation also closes affected sessions.

Draft state is disposable UI data. Each snapshot is at most 16 KiB; the session
cache holds at most 32 entries and 256 KiB total, scoped by user, board, app and
version. Account change and observed revocation clear drafts. Writes coalesce
over 150 ms; closing before transmission can discard the latest value. There is
no close-time flush or browser-restart persistence guarantee. Never store
credentials, execution queues or business receipts in panel drafts.

Use SDK host design resources and theme synchronization. Theme channels carry
presentation only. Shared public assets must work within the isolated frame;
they do not convey authenticated API authority.

## Human interaction and MCP consent

Apps may request a native reaction, approval or structured choice on their owned
cards. Approval, question response and instruction are distinct purposes; seen
receipts do not imply approval. AI-generated requests retain attribution and
explain the required human response. Choice destinations use existing board
stage mappings.

Create/read/cancel requests and acknowledge received responses through the
[app human interaction APIs](app-human-interactions.md). The server validates the
current responder, app, generation and mapped destination. Historical reactions
cannot approve new requests, duplicate clicks cannot replay an accepted action,
and expired/canceled/revoked requests cannot resume work. Response delivery,
acknowledgment and explicit workflow application have distinct saved receipts;
receiving a response does not itself establish an external runtime continuation.

The human connection manager reads
`GET /settings/apps/connections/{connection_uid}/mcp-consent` to show actual
`available_tools`, retained `granted_tools` and consent/app/trust revisions before
explicit scope selection. A declaration change can permanently withdraw affected
grants; restoring the declaration does not restore consent. A host unable to
describe the tool set cannot grant it.

Native user MCP `get_card_app_mcp_tools(project_uid, card_uid, connection_uid)`
uses current connection-owner, board/card/resource and consent checks plus native
MCP visibility/tool-group authorization. It does not issue or forward an app
credential. Tool discovery and consent do not implement remote MCP execution;
remote tool calls remain unsupported by this catalog contract.

## Revocation and cleanup

Board administrators may disable bindings or withdraw panel consent after app or
policy disablement, with current board update access and exact revisions. Cleanup
does not restore discovery, execution or another user's personal connection.

GitHub, GlitchTip and Dokploy account owners can remove saved resource selections
or disconnect after policy/trust revocation without resolving provider secrets
or contacting the provider. Retained metadata lists support obtaining current
removal revisions. Discovery and activation still need active policy and a valid
connection. Read consent preserves independently approved panel/workflow grants
and never enables transitions.

GlitchTip and Dokploy **Revoke board read access** removes the provider's read
grants using current connection/binding revisions, retaining connections,
selections, observations and unrelated grants. **Load saved selections** remains
available when provider discovery fails or grants are partial. Repeated removal
is idempotent; stale revisions conflict. GitHub deselection similarly requires
current ownership, board update authority and resource revision. Adding resources
still requires active policy and verified provider authority.

Disconnecting or deselecting retains cards and history. Current signal reads,
provider refreshes and delivery actions recheck registration and connection
scope. Organization connections must match the board's active organization;
personal automation is blocked on shared boards. Invalidation clears active
panels after committed changes. These controls do not cancel or undo an external
operation already accepted by a provider.

## Static package installation

Administrators install official stable GitHub release ZIP assets or upload a
manual ZIP. Both validate declarations, paths, archive limits and assets before
approval. Official downloads additionally verify the catalog-owned repository,
exact asset name, stable version and GitHub SHA-256 digest. Board grants,
connections and resource selections remain separate explicit steps.

A ZIP contains `manifest.json` and public static files under `panel/`.
The manifest has `package_version: 1`, `declaration`, `panel_entry` and optional
`store` metadata. `store.icon`, `store.icon_dark` and up to five
`store.screenshots` reference images in the package. Panel ESM imports must use
relative paths. The host serves content-addressed assets and supplies
`panel/host.mjs` for its configured UI origin. No executable server, credential
or private configuration belongs in a static package.

Python SDK `python -m langboard_sdk.package` creates portable ZIPs.
Server SDK 0.1.6 also exposes `@langboard/app-server/packaging` for supported
reference layouts; other layouts can use the Python packager. GitHub, GlitchTip
and Dokploy reference commands select explicit read-only, workflow, publication
or supported interaction profiles. Unknown options and incomplete profiles are
rejected. Optional server features require a configured HTTPS `PUBLIC_URL` for
the separately deployed service. A ZIP does not deploy that service or grant
connection/card/transition authority.

Updates require the current installation revision and follow the trust-change
consent rules above. Disabling an app also blocks hosted panel assets. See
[app discovery and mobile navigation](mobile-app-rail.md) for installation and
shortcut recovery behavior.
