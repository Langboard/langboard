# Independently versioned SDK consumption

Python SDK 0.2.31 adds `AppExecution.request_review`, an explicit execution-result
transition to the mapped built-in Review column under current owning-app consent.
The host requires retained start/stopped reports, current generation and native
card/binding revisions; it retains an immutable replay receipt. The independently
built wheel is exercised over native PostgreSQL HTTP. It does not approve work,
move to Done/Archive, retry automatically or establish deployed acceptance.

SDK 0.2.29 added user-authorized
`AppManager.set_archive_sync` and connection-authenticated
`AppCards.report_archive_outcome` plus `AppCards.read_archive_outcome` for ambiguous report reconciliation. The PostgreSQL public HTTP archive tests
exercise the installed wheel through `HttpTransport`, including explicit consent,
unknown-to-readback reconciliation and identical report replay. These checks do
not establish deployed browser acceptance or actual provider mutation.

Python SDK 0.2.28 includes `AppCards.read(board_uid, card_uid, offset=0, limit=16000)`
for current app-owned content through `GET /apps/v1/boards/{board}/cards/{card}`.
It requires separately declared and approved `cards.read` consent. Each page
checks current connection, app ownership, selected resources and card visibility;
PRIVATE, archived, foreign-owned and revoked resources are denied. The response
contains title, bounded Markdown, current card/ownership/selection/binding
revisions and the card's selected resource identities/access revisions. It does
not contain credentials, execution grants or runtime authority. Recheck the
execution authority separately before starting work; compare content revisions
when assembling more than one page. Withdrawn HITL consent does not revoke an
independently retained read grant. Both Python manifests and locks consume the
same reviewed 0.2.29 wheel.

SDK source and package tests live in [langboard-sdk](https://github.com/yamonco/langboard-sdk).
This host consumes the same reviewed npm tarball and Python wheel as external apps.
`vendor/provenance.json` records package versions and SHA256 digests. The UI lockfile
pins the npm artifact; both Python lockfiles pin the wheel hash. This snapshot is
not a public npm/PyPI registry publication.

Update the SDK in its repository, run its standalone checks, build artifacts, then
update this directory and the host lockfiles in a reviewed change. Never edit the
package bytes here or restore parallel SDK source implementations. Native host
integration tests remain in this repository because current authentication,
permissions, consent and resource projections are owned by the host.

Panel protocol v1 and package semver are distinct. Existing lifecycle-only panels
remain supported. `signals.list` additionally needs the native panel read bridge,
explicit `signals.read` board consent and current provider resource authority.
An older host without the bridge returns an unavailable error; no permission or
provider operation is inferred from SDK installation.

Python SDK 0.2.1 adds `GlitchTipManager.disable_read` and
`DokployManager.disable_read`. The host and shared Python manifests/lockfiles pin
the same wheel SHA256 recorded in provenance. Integration tests import that wheel
directly and exercise authenticated native read, revocation, denied refresh and
explicit re-consent with persistent observations. No SDK source-directory import
or editable installation is used. The panel package is 0.2.1/protocol v1.
This local package update is not a registry publication or deployed host claim.

Python SDK 0.2.2 adds `ConnectionCredentials` for explicit owner-managed inbound
credential issue/revoke and `AppIdentity` for the server-derived connection
identity. Use separate owner and app HTTP sessions. The host imports the reviewed
wheel for an authenticated issue → identity → denied user impersonation → revoke
→ 401 roundtrip. No editable SDK installation or host-private SDK import is used.
Identity grants no execution or card mutation capability. The local package and
lockfiles are pinned by digest; deployed compatibility remains pending.

Python SDK 0.2.6 makes `expected_authority_version` mandatory for execution
requests. Pass the version from the preceding authority lookup; the host compares
the current snapshot before persisting either request or outbox event. A stale
version returns 409. The receipt returns its fixed `authority` snapshot; the GET
response additionally has `state: eligible` and `started: false`, so compare the
snapshot fields rather than GET-only status fields. SDK 0.2.5 generation-only
requests fail input validation. This package update is not a deployment or an
execution start; callers need an explicit upgrade.

Python SDK 0.2.7 adds app-authenticated request readback and reception
acknowledgment. `read_request` returns the fixed accepted authority snapshot;
`acknowledge` records `received/started:false` with the matching event UID and
runtime reference. These operations do not start execution or approve work.
The independently built wheel and both lockfiles remain digest pinned.

Python SDK 0.2.8 adds an explicit runtime permit boundary. `authorize_runtime`
requires the accepted request and its `acknowledgment_uid`, plus a random
32-byte lowercase-hex runtime token. It returns a 120-second
`permit_execution: true` authorization with `started: false`; acquiring it
does not start a process. `check_runtime` renews only while current authority
still matches. A revoked connection, stage/resource drift, or expiry changes
the permit to `stop_requested`; the receiver may then submit `stopped: true`.
The opaque token is scoped to that lease and remains valid for stop-only
recovery after app credentials are revoked. Stopped or expired permits never
resume; a new accepted request and generation are required. The host preserves
the stop history, and scheduling, external process start, and HITL resume are
separate integrations.

Python SDK 0.2.9 adds `RuntimePermit` identity types and `recover_runtime`.
Recovery uses the accepted request UID and the privately persisted token to
find an existing permit; it applies the same primary-database authority and
expiry checks as heartbeat. It cannot create, rotate, or resurrect a permit.
An unknown recovery outcome is not permission to execute and is never retried
implicitly by the SDK.

Python SDK 0.2.10 adds `report_start` and `ExecutionStartReport`. The native
app-authenticated endpoint records a receiver-reported execution reference
only under a current live permit. `start_reported/app_attestation` describes
the evidence source explicitly; it is not host process observation, dispatch,
workflow transition, or completion. Duplicate reports are idempotent only for
the same execution reference. Permit receipts still return `started:false`.

Python SDK 0.2.11 adds `runtime_report` and `RuntimeReport`. The read revalidates
current authority without extending the permit. It retains historical start
attestation separately from `active_report`; stopped, expired or revoked
permits cannot appear active. This is an app-attestation projection, not
independent process health. Use explicit heartbeat calls for renewal.

Python SDK 0.2.12 introduced `CardResources.read/select` for human administrator
configuration through the native card-resource GET/PUT routes. Use a user
transport separately from app-scoped `AppResources`. Read current revision
before replacement; an empty selection unlinks resources. No automatic retry,
execution grant or workflow transition is performed. The reviewed wheel is
pinned with its digest in `vendor/provenance.json`.

Panel SDK 0.2.1 adds `resources.list` over the existing bounded request bridge.
The host supplies board/app identity and authority revisions; panels send only
an optional cursor. Reads require current panel and resource consent and are
limited to the current app before pagination. Disposal cancels in-flight work.
The artifact digest is recorded in `vendor/provenance.json`. This panel read
operation does not implement the app export outbox.

The current checkout retains only the consumed panel package and current Python
wheel/sdist. Superseded binary packages and their digest entries are recoverable
from Git history; they are not current dependency candidates. Panel CI resolves
its archive from the UI manifest and verifies the recorded digest/version.

Python SDK 0.2.13 adds `CardResources.list_selections` for an explicit page of retained card connection selections and revisions. Disconnected selections remain visible for owner-authorized cleanup. No automatic page scan, credential exposure or execution grant is added.

Python package CI verifies the project-declared wheel against reviewed provenance and the lock file before installation. Artifact publication retains the original package/source provenance; the workflow source SHA is recorded separately. A new checksum cannot replace the reviewed digest.

Python SDK 0.2.14 adds `validate_card_interaction` and app-authenticated `AppCardInteractions.create/read`. These persist structured requests under separately approved `interactions.request`; no response acceptance, workflow mutation or provider action follows request creation.

The host now consumes reviewed Python SDK 0.2.15 for explicit card lifecycle and
disabled-by-default card interaction declarations. Native registry approvals and
updates reject card-producing definitions without lifecycle policies, including
legacy schema submissions. Existing approved definitions remain readable;
creation-time type enforcement, feature settings and lifecycle dispatch remain
separate implementation gates. No external archive action runs at registration.

Python SDK 0.2.16 adds `AppCardInteractions.cancel` against the host's strict
revision-fenced cancellation route. The reviewed wheel and source distribution
are pinned by source commit and SHA256. Cancellation retains the original request
and does not deliver a human response or stop/resume an external runtime.

Python SDK 0.2.17 adds bounded `choice_or_text` question requests. It preserves
stable choices alongside non-secret custom input; custom text cannot implicitly
approve or select a workflow outcome. The actual reviewed wheel is exercised
against host request storage. Response acceptance and UI remain separate gates.

Python SDK 0.2.18 provides the shared `validate_card_interaction_values` function.
The host regression consumes the immutable wheel and validates answers against
an actually stored request. No human response acceptance endpoint, durable response
record, delivery or continuation is claimed by value validation alone.

Python SDK 0.2.19 supplies `AppCardInteractions.acknowledge` for explicit reception
of signed interaction responses. The host regression authenticates exact signed
bytes through the same wheel's `verify_webhook`, then uses the SDK method against
the native HTTP acknowledgment route. Reception, delivery and execution remain
separate outcomes; the SDK does not automatically resume or poll.


Python SDK 0.2.23 provides `AppManager.prepare_consent` and `set_consent` for
workflow-independent disabled drafts and explicit grants/withdrawal. The host
pins the independently built wheel and provenance digest. Actual SDK transport
calls exercise native user-authenticated consent preparation and grants on
SQLite/HTTP and PostgreSQL; preparation alone cannot publish signals or enable
workflow transitions. HITL wire contracts are unchanged. This is not registry
publication or deployed acceptance.

Python SDK 0.2.28 adds explicit administrator App Store composition management and
board-scoped atomic group withdrawal. Grouping grants no authority; compatible
host discovery/migration and current revisions are required.

Python SDK 0.2.29 validates native signal publication receipts. Missing or
malformed signal_uid/duplicate fields raise MutationOutcomeUnknown, preserving
the caller-owned envelope for reconciliation without automatic retry.

Python SDK 0.2.32 adds read-only `AppExecution.read_review` for request-scoped Review receipt reconciliation. The host retains original POST inputs separately from present card/binding/generation state. This method never automatically repeats a transition.
