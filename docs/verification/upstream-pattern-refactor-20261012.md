# Canary upstream pattern refactor — 2026-10-12

Status: in progress. Goal remains active; this is not a deployment receipt.

## Additional goal scope — shared build and validation contract

On 2026-10-13, the user authorized proceeding with the shared FractalOps build and validation agreement as additional work under this goal. Preserve every existing canary/upstream requirement and the prohibition on merging Langboard main.

FractalOps owns the versioned declaration, central executor, request lifecycle, capacity admission, cancellation, deduplication, and receipts. Assembly owns shared BuildKit/ARC execution capacity and isolated service-job policy. Langboard owns its Dockerfile validation targets, fixtures, and complete regression requirements. Existing v1 image requests must remain compatible. New repositories must participate through declarations without repository-specific executor code.

The agreed execution paths are BuildKit targets for service-independent checks and centrally managed digest-pinned validation images/jobs for PostgreSQL integration checks. Capacity shortage is `waiting_capacity`, not an executed test failure. Results distinguish executed checks from reused cache; failed or missing validation results block image promotion. Private retained editor data must never enter public source, build context, or shared cache.

The socket Dockerfile now prepares an optional `validation-results` export target with `/result/result.json` and a diagnostic log. It covers Phoenix format, warnings-as-errors compilation, native tests, unused dependencies, Credo, Dialyzer, and the distributed editor probe. Export completion alone does not imply success: the executor must validate `exitCode`. The final default `runtime` stage remains the production release image. This target has not yet been executed through the central service and does not replace the Python/UI/PostgreSQL, retained Yjs–Yex, or authenticated canary gates.

The central v2 executor remains an implementation dependency. Do not activate a v2 consumer declaration until its schema, target export, failure handling, and policy support are verified. Acceptance also requires a second language/project and a new repository using the same declaration contract, including intentional failure, cancellation, repeated requests, cache reuse, capacity waiting, report retrieval, and cleanup.

## Retained editor state inspection — 2026-10-13

Read-only inspection of the running legacy socket found 345 retained `.ydoc` files totaling 154,770 bytes under `/local/editor-sync`. The inspected container mount list did not include `/local`. Replacing that container without exporting its retained state would therefore risk losing editor state. The Phoenix Compose service already declares a persistent editor volume; the live source must be migrated into that volume, not inferred to be present there.

A private tar copy was obtained without modifying the running service. All 345 archived files were extracted locally and matched their recorded byte counts and SHA-256 hashes. All 345 also decoded successfully using the UI's installed Yjs package. The archive and file identities remain outside tracked source because they contain user document state.

This live copy is not a consistent cutover snapshot. It does not verify pending in-memory state, a simultaneous database snapshot, document-name mapping, Yex roundtrip, or deployed Phoenix recovery. Those gates remain required before retiring the Node container.

CI run `38328834320` on `eee97712d90f85d77547981c3a7955a7a3e20f1c` failed fetching Hex package metadata before compilation. The subsequent source commit `e5b750c12ad8d5246c1dd2b5983e4d9b699ac0f9` computes dependency cache identities inside the actual job workspace and configures bounded Hex request concurrency and timeout. These changes have not passed the full gate: run `38329612174` failed during container initialization because its job Pod remained Pending. No test steps executed in that run.

The preceding run `38265455679` passed Python and TypeScript dependency installation, UI typecheck, and 100 Python tests with four skips, then failed compiling `crc32cer` because `cmake` was absent. The CI package list now includes the build tool already required by the upstream socket Dockerfile. No GitHub-hosted runner fallback was introduced.

## Test scheduling gate — 2026-10-13

The company image-build service and the self-hosted validation runner have distinct execution contracts. Live inspection found eight healthy BuildKit workers matching the current private GitOps source; the older local infrastructure checkout must not be used to infer replica drift. The validation runner and its workflow container each request CPU and memory independently. At inspection, the only worker with sufficient reserved-memory headroom had 79m CPU unallocated, below their combined 100m CPU requirement. The other eligible workers rejected placement for insufficient memory.

Increasing BuildKit replicas does not address this validation scheduling failure. No resource limits, other workloads, node taints, or physical infrastructure were changed. No new virtual machine was created. The terminal failed run is retained; repeating the same job without a scheduling-capacity change is not a recovery strategy. Private cluster identities and diagnostic receipts remain outside this repository's tracked source.

## Ownership

The integration stays on `review/canary-phoenix-integration-20261012`. Main is not a merge target. The original union is retained at `2e9b4b243044903fe3edfd85bbbed5a52164ebb1`; the unfinished foundation merge remains `55dcc0c40bdb40ee602f4ad793d45caf81396aee`. Pre-edit conflict files and index stages are retained in `.verification/pre-refactor-20261012/`.

## Resolved UI contracts

- Adopt the upstream validated incoming socket envelope and event-keyed stream error registry. Remove the duplicate, destructive error callback registry.
- Preserve subscription authorization and make callback type erasure explicit only at the heterogeneous registry boundary.
- Adopt HTTP notification commands and generation-fenced refresh. Preserve unread-only filtering and close-before-navigation.
- Adopt chat upload file tokens, send-failure handling, and user/project/tab-scoped recovery. Preserve lazy activation and the rail panel.
- Use Phoenix editor rich-patch transport from the Yjs integration. Remove obsolete BoardCard socket patch listeners. Preserve collaboration initialization gating and upstream cursor mismatch recovery.
- Preserve card flip drafts, resource-derived titles, unavailable-card UI, container-width comment layout, and optional metadata hydration. Add upstream comment-updated subscriptions.
- Use the upstream Tailwind preparation script; keep a compatibility import for existing automation and retain previous-release assets.
- Keep the current SDK 0.2.37 artifact and document retrieval extras in both Python lockfiles.

## Verification

- Current shared TypeScript core Rollup build: passed.
- Shared realtime contract check: passed.
- UI typecheck with current rebuilt core and installed dependencies matching Plate 53.3.15: passed, zero diagnostics. The verification configuration resolves Sentry from an existing installed package and does not change another checkout. This is not clean-install evidence.
- Event-keyed stream error regression: 1 passed.
- Board chat recovery store regressions: 3 passed.
- Release asset retention regressions: 2 passed.
- Modified UI syntax transforms: passed.
- BoardCardApi and MCP test Python syntax: passed; lockfiles parse as TOML.
- Unmerged paths: 56 initially, 20 remaining after UI, delivery and verified execution/ownership boundary resolutions. Remaining backend files have prior candidate contents but are intentionally not marked resolved without contract tests.

## Confirmed remaining failures

The delivery/signing owners now use the existing canary SDK and persistent outbox contract: `(request_id, event_type)`, `attempt_count`, `lease_until`, and `delivery_history`. The queue task delegates to the existing signed HTTP worker, preserving exact bytes, public DNS checks, post-DNS revocation checks, and bounded delivery leases. Administrator inspection/redelivery and paged recovery remain available; recovery selects only supported events with a previously frozen destination. Destination disable/re-enable advances the CAS revision and cannot retarget accepted events.

The resource selection model no longer declares unused foundation identity/ownership fields absent from the established migration. App resource bindings remain unselected by default; fixtures explicitly select their resources instead of relying on the old implicit-selection default. No applied migration was rewritten.

Current focused verification: the earlier SQLite delivery/destination/resource suites had 49 unique passing tests. The previously skipped PostgreSQL cases now run against a separate disposable PostgreSQL 17.6 container: delivery, HTTP, and after-commit dispatch together pass all 34 cases with zero skips, including 11 PostgreSQL cases. This proves the tested claim exclusion and current revocation/commit boundaries, not the complete migration history or every policy writer.

Remaining app services still mix foundation/canary contracts, including `verified_card_app_configuration` imports without a current implementation. Policy mutation locking needs verification across every writer before declaring concurrent revocation safety. The two migration heads still expose 24 duplicate table creations; PostgreSQL replay remains required.

## Phoenix card audience integration

The event projector now preserves validated `card_uids` as internal dispatch metadata. Socket fanout carries that evidence to the existing Python subscription authorization API with the recipient's bearer token. Python uses one current card visibility helper shared with the legacy notification dispatch endpoint. BoardCard subscriptions and editor write admission also require current card readability. A denied card skips the event without revoking an otherwise valid board subscription. Deleted-card signals are restricted to `uid`, `project_column_uid`, and `source_type`; dispatch metadata is removed before client serialization. Protected events without card evidence fail closed.

Allowed fanout uses one API request. Only denial requires a second request to distinguish revoked board membership from hidden card content. The API explicitly acknowledges that card authorization was applied; a socket facing an old API that silently ignores the new fields fails closed instead of accepting board membership as card visibility proof.

Verification of current sources:
- Python socket authorization and card audience: 93 passed, zero skips. Includes actual SQLite and PostgreSQL card rows for private/internal/shared visibility, soft deletion, membership removal, editor denial, and bounded input.
- Native Phoenix projector, API client and socket handler suites: 92 passed, zero failures using Elixir 1.19.5/OTP 28 in a disposable test container with the repository's locked dependencies. Includes audience metadata, denied card/retained board, scrubbed removal, missing evidence, and old API rejection.
- `mix format` applied to changed Elixir sources/tests; whitespace checks passed.

These are source integration tests, not deployed browser acceptance. Legacy Node conflicts remain until all remaining responsibilities (notifications, service credentials, AI and editor ownership) are accounted for. Main has not been merged and the live canary has not been changed by this refactor.

## Board

Tracked at https://langboard.yamon.io/board/2RRwwB5JNm0/gsU03P80pEe, Doing. Backup checklist item is complete; integrated UI/runtime, app contracts, migration replay, and canary acceptance remain open.

## Execution API consolidation

The reconstructed request-read/acknowledgment owner duplicated the deployed canary execution authority API and depended on a separate CardExecutionIntent lineage. It has been removed. Request reads and acknowledgment writes remain in AppExecutionAuthorityApi; acknowledgment readback was absorbed into the same current-authority service. Existing runtime admission/check/recovery/start/report routes retain their original owner. The reconstructed lease API now retains only its distinct optional Active-handoff routes, whose service integration is still incomplete.

Acknowledgment admission additionally matches the frozen event type, project, event/request/card IDs and generation; expiry and immutable receipt semantics remain. Runtime lease/start/review boundaries now take the shared policy transaction lock before their existing row locks. Canonical models and immutable Review fingerprints/receipts are retained; no historic table was replaced.

Ownership reads were added to the existing audited ownership service. The optional observed app revision is checked when supplied, and the HTTP route maps native ownership conflicts to 409 instead of surfacing an internal error.

Verification:
- Request/acknowledgment, lease, start and Review suites: 51 passed with disposable PostgreSQL enabled, zero skips. Includes route uniqueness, native packaged SDK calls, corrupt event rejection, received readback/current revocation, concurrent acknowledgment and immutable Review rollback/replay.
- Ownership suite: 9 passed, including observed app revision and private-card read denial.
- Governance suite: 15 passed.
- Review fixture now uses checkfirst for a table already created through the real PostgreSQL FK closure; production migration behavior was not altered.

Route import audit still exposed the reconstructed human execution intent's missing configuration helper. Human intent and explicit Active handoff must be integrated with the canonical request/generation owner before full API boot or deployment is claimed. Resource configuration route arguments also still need consolidation. The graph foundation tests must be adapted to the resulting canonical contract; changing their route import is not test acceptance.

## Card resource settings boundary

The settings route now calls the existing card resource service with its canonical positional arguments and optional keyword revision fences. The route app key must match the selected connection. Reads expose current app, binding, ownership, and connection identity revisions; writes reject stale supplied revisions before creating selection or audit rows. Selection CAS and permanent audit history are unchanged. Revoked consent still permits explicit unlinking. No duplicate snapshot fields or new persistence lineage were introduced.

Focused evidence: `.verification/card-resource-route-regression.txt`, 17 passing SQLite tests, no skips. The new route-to-service test covers app mismatch (403), stale identity/app/binding/ownership revisions (409), absence of partial rows, save/readback, and unlink after consent withdrawal. This is direct route-function integration, not authenticated browser or PostgreSQL migration acceptance. Human execution intent, Active handoff, provider verification convergence, and full migration replay remain outstanding.

## Retire the replaced TypeScript socket runtime

Removed the 18 residual files under `src/socket/src`, including ten modify/delete conflicts. Upstream already removed the rest of that runtime, its package manifest, TypeScript configuration and build entry points. `mix.exs` builds `lib`; current Compose routes only to Phoenix. Keeping these orphan fragments would preserve neither a runnable service nor a supported compatibility path. Worktree originals are retained privately in `.verification/retired-typescript-socket-20261012.tar.gz`; prior merge stages remain in the original backup.

Canary behavior now belongs to the current owners: Python durable notification persistence/delivery and recipient revocation, Python Graph service authentication and session ownership, Phoenix subscriptions/card audience and editor document persistence. Phoenix persists accepted document updates before acknowledging; the removed Hocus shutdown-flush workaround is not a second persistence owner.

Focused Python/owner checks: 89 passed, four PowerShell checks skipped because PowerShell is unavailable. The first run exposed a test-only authentication stub missing the current `allow_oidc` keyword; updated it and asserted notification requests do not enable OIDC. Full native Phoenix run: 393 tests, two failures solely from scripts missing in the disposable test container copy. Repository scripts exist; copied that scripts directory and reran the failing manifest suite. These results do not establish a production cutover or browser acceptance. Nine unresolved merge paths remain, all app API/service convergence.

Final socket retirement evidence:

- `.verification/phoenix-full-native-regression-final.txt`: 393 native Phoenix tests, zero failures after copying the existing repository scripts into the disposable container.
- `.verification/phoenix-native-source-identity.json`: all 112 Elixir source/test files checked against the worktree, zero hash drift.
- `.verification/phoenix-retained-chat-editor-regression.txt`: 109 Python tests passed for board chat run/session ownership, internal Graph requests, editor runs and editor routing.
- `.verification/phoenix-manifest-native-regression.txt`: the two initially failing command paths reran in their ten-test suite, zero failures. Initial failed output remains retained.

This verifies source/runtime replacement behavior within the disposable test environments; deployment, retained production Yjs data, authenticated browser flows, and the unresolved app migration/authority contracts remain separate gates.

## Owning-app card reads and interactions

Converged card reads on the existing native `last_change_seq` and execution generation table. The alternate timestamp-derived revision and reconstructed counter are not used. Path validation, empty bearer rejection and shared policy locking are retained. Selected resources require both the operation capability and `resources.read` in the approved definition and board consent.

Interaction creation, cancellation, human answers, frozen workflow plans, delivery attempts and reaction receipts remain in the existing canonical service. Added current-authority request-key recovery and acknowledgment reads to that same service. Reception reads do not enqueue work or resume execution. Removed the unused reconstructed `authority_snapshot` model column because it was absent from the applied schema; no applied migration was edited. Current owning-app and human authority are revalidated independently of retained request payloads.

The native comment service now passes the canonical domain service into reaction acceptance. Pending comment interactions use one shared bounded helper rather than two query implementations. The regression fixture now intercepts the real Broker enqueue contract rather than the retired direct task call.

Evidence: `.verification/app-card-convergence-postgres-final.txt`, seven passing SQLite/PostgreSQL integration cases, no skips. The four long interaction cases cover baseline and packaged example app profiles across both databases: create/dedupe/recover, answer/expiry/cancel, signed event retry and recovery, reception, ordinary and approval comment reactions, and mapped workflow transitions. Two card read cases exercise HTTP auth, native revision, bounded paging, current read/resource grants and disconnect. The seventh verifies native Review movement and replay on PostgreSQL. This remains source and disposable database proof, not a deployed browser acceptance result.

## Registry, connection authentication and governance convergence

Preserved verified provider-account connections and host-owned inbound identities as distinct paths. Inbound management filters by the constructor's identity fields, rejects provider connections, and disconnect atomically revokes retained host credentials and clears MCP connection consent. Credential issuance/revocation and inbound mutation use the shared policy lock. Existing provider resource management remains present rather than disappearing with the alternate management implementation.

Registry mutations preserve composition, board consent, provider manifests and existing signal adapters. Adopted the current definition -> connection -> consent lock order on trust changes and deterministic connection ordering. Retained workflow permission withdrawal. Declaration-only updates retain installed ZIP package metadata. Registry HTTP paths reject invalid identifiers/extra approval fields; optional bounded key-cursor reads preserve the existing `apps` response collection for SDK callers. Governance organization routes use bounded native identifiers.

Evidence: `.verification/registry-connection-postgres-final.txt`, 63 tests passed with PostgreSQL enabled, no skips. Coverage includes authenticated SDK register/update/disable, trust and scope consent revocation, manual/official ZIP failure/update/asset access, current identity and secret hash history, governance, cursor pagination/admin withdrawal and package retention. Inbound disconnect verifies permanent credential history, withdrawn MCP consent and exclusion of provider identities. Fixture changes use isolated auth caches, explicit selected resources, and checkfirst for already-created organization tables; no production auth bypass was added.

The latest settings route import audit (`.verification/app-route-import-audit-latest.json`) still finds `CardExecutionIntentApi` importing a reconstructed CardAppResources helper that does not exist in the canonical owner. Active handoff uses the same mixed lineage and remains unproven. Textual conflict resolution does not imply boot, migration, deployment, or feature completion.

## Human execution intent convergence

Human run intent no longer imports nonexistent reconstructed resource helpers or
creates an event referencing an intent-only request identity. It evaluates the
existing native human card authority and current connection execution grant,
compares the observed authority snapshot, and advances the migration-owned
counter with a database compare-and-set. PostgreSQL uses the same card advisory
lock as the readiness producer before acquiring native card locks. The resulting
request and delivery event are persisted by `accept_authorized_execution_request`.
The intent retains its initiating user and idempotency key as audit evidence using
the canonical request ID. It does not grant a lease or attest a runtime start.

Replay checks current authority and the accepted generation, while stale or
competing requests fail with a conflict. Existing requests must use the separate
stopped-runtime/new-run flow. Retained receipt reads remain available after app
revocation under current human card and connection ownership boundaries.

The compatibility counter mapping now represents the already-applied two-column
`card_execution_generation` table, outside application-row metadata. It no longer
adds an application ID or timestamps, nor recreates this migration-owned table
through `BaseDbModel.metadata.create_all`. This preserves the existing PostgreSQL
schema and its primary key/FK. Explicitly selected resource fixtures were updated
to reflect the current opt-in selection default; authorization was not weakened.

The bounded settings app route import audit now returns no failures. This does
not prove full application boot: optional Active handoff still requires canonical
request/lease convergence and continuity checks after movement. The competing
migration histories also remain unresolved; no deployment is claimed.

Verification receipts:
- `.verification/human-intent-native-counter-regression-final.txt`: 181 passed,
  49 SQLite skips for PostgreSQL-only native card creation cases. PostgreSQL was
  enabled and executed; the counter/create_all collision is no longer reproduced.
- `.verification/human-intent-initial-and-existing-generation.txt`: 4 passed,
  no skips, covering SQLite and PostgreSQL at observed generation 0 and 3, canonical
  request/event identities, idempotent replay, stale request denial and retained
  audit reads after revocation.
- `.verification/app-route-import-audit-latest.json`: empty failure list for
  bounded settings app imports, including human execution intent.

## Active handoff and runtime/HITL continuity convergence

`AppExecutionActive` now uses the canonical accepted request, credential-owned
lease and retained start attestation. It uses `last_change_seq` for card CAS and
native `CardService.change_order` for a declared, mapped built-in Active stage.
It no longer imports nonexistent lease/resource helpers, reads human intent as
an execution request, or calls an unsupported lease-check argument.

Admission and new execution requests remain Ready-only. Runtime checks allow the
exact retained Active handoff for this request/lease/start/generation, revalidate
native visibility, dependencies, pending approval gates, scope/ownership/resource
revisions and workflow mapping, and compare the complete post-move authority
snapshot. The accepted request authority is immutable. Subsequent card changes,
movement, scope/mapping changes, credential revocation or generation changes
request stop while retaining start/handoff/history. Receipt reads do not renew a
lease, dispatch a process, or repeat a move.

Accepted request reads and execution-bound human interaction answers share this
current-authority path after Active. Public request reads return the current
Active snapshot; internal acceptance comparison retains the original authority.
Active and Review reuse one `current_execution_request_scope` in the execution
request service instead of cross-importing a Review-specific private helper.

Verification:
- `.verification/active-canonical-hitl-postgres-final.txt`: 71 passed, no skips,
  PostgreSQL enabled. Includes Active concurrency/rollback and eight revocation
  cases, start/lease/Review/stop/resume, request acknowledgment and structured
  human interactions (baseline and independently packaged example app).
- Active cases demonstrate one native move/receipt under three concurrent
  requests, heartbeat after movement, current request snapshot read, an
  execution-bound question and a human answer without implicit resumption.
- Focused HTTP receipt/admission validation is recorded separately below.

This source convergence does not prove migration/deployment or provider runtime
acceptance. Applied migration lineage and full application startup remain gates;
legacy reconstruction-specific graph acceptance tests still require alignment
with canonical request IDs and services. Main has not been merged.
- `.verification/active-http-postgres-final.txt`: 8 passed, no skips. Native
  PostgreSQL Active cases plus HTTP missing identity, extra actor rejection,
  idempotent replay, private-body receipt read and no-store headers passed.

## Human response route and applied migration lineage

The legacy single-interaction HTTP surface now delegates to the bounded native
human ACL read and existing response service. It supplies `DomainService`, current
collaboration channel and canonical argument order, and maps native interaction
conflicts to 409. No parallel human response persistence or approval path was
added. The response key bound matches the existing native API. The four SQLite /
PostgreSQL baseline / separately packaged monitor interaction cases passed with
legacy HTTP read, missing/app-token denial, actor injection denial, stale revision
conflict and idempotent response recovery (`human-route-canonical-regression.txt`).
All 96 route modules now import without failure.

Applied migration history `c97659de63f4` replayed successfully on a fresh disposable
PostgreSQL schema. The 19 unapplied app restoration revisions on the incoming
branch were archived privately and retired from this candidate; they recreated
24 existing tables with incompatible request/outbox/counter schemas. All applied
revision IDs and the nine actual upstream notification/runtime migrations remain.
`d0876afe1402` joins `c97659de63f4` and upstream `0cc6abc4b020` without stamping or
recreating their schema. `e1987bdf2503` adds only the two missing configuration
columns and two new intent/Active audit tables. Existing selections, generations
and consent are not reset or fabricated. Populated audit rollback is refused.

Evidence:
- `canary-applied-history-replay.txt`: actual old head replay passed.
- `upstream-canary-merge-replay.txt`: upstream nine-revision path + join passed.
- `canonical-forward-replay.txt`: additive forward revision passed.
- `merged-model-schema-gap.json`: no missing model tables/columns. This is a
  column/table inventory, not proof of every constraint or live operation.
- `phoenix-canary-migration-postgres-final.txt`: reusable PostgreSQL test passed,
  no skips. Fresh union replay, populated old-head replay, all retained row values,
  two event types sharing a request, delivery failures/history, ownership/resource
  audit, accepted request/ack/lease/start/Review, package and MCP consent preserved;
  empty forward roundtrip and populated downgrade refusal also passed.

The first isolated multi-schema replay exposed pg_trgm visibility because the
extension was installed in another test schema. Extension placement/search_path
were corrected only in the disposable DB. A reserved-word fixture SELECT was
quoted. Neither failure required rewriting applied migrations or production DB.

Application construction loads MCP tools, middleware and routes successfully
(`application-construction-audit.txt`). Full lifespan and live canary are unproven.
OpenAPI construction exposed duplicate GET `/apps/v1/boards/{project_uid}/resources`
registrations (AppConnectionResourcesApi/AppResourceReadApi); convergence is the
next required source correction. Reconstruction-specific graph acceptance tests
still need canonical-service/ID alignment. Main has not been merged.


## Resource and workflow route convergence

The duplicate resource GET was retired in favor of AppConnectionResourcesApi and
its native credential/current-board policy service. The canonical route retains
bounded paging, current selected resources, declared types, legacy manifest
compatibility, organization authority and shared-board personal-connection fences.
It now rejects empty bearer credentials and malformed board IDs explicitly.
The retired restoration reader was archived locally before removal.

Restoration also registered duplicate workflow registry GET and semantic PUT
routes (stage_key versus stage_uid). Both are removed. WorkflowStageSettingsApi
remains the single role-gated administrator registry editor; authorized board
users retain BoardColumnApi workflow options. Column descriptions, translations,
CAS and composed guidance remain available through BoardWorkflowSettingsApi.
No new replacement editor or parallel authorization policy was introduced.

Evidence:
- resource-workflow-convergence-final.txt: 44 resource/registry/authenticated
  workflow tests passed, including PostgreSQL, no skips.
- application-route-uniqueness-final.txt: application and OpenAPI construction
  passed; 534 normalized method/path pairs, zero collisions even when parameter
  names differ.
- application-lifespan-final.txt: native application lifespan entered and exited
  cleanly; default, agent and raw MCP transports started using the local
  development vault. This does not establish production OAuth or deployed health.
- column-and-migration-convergence-final.txt: three tests passed, no skips:
  current column authority/CAS, after-commit publication/rollback, actual fresh
  and populated Alembic replay. Canonical generation PK, intent uniqueness,
  Active lease uniqueness, positive-generation check names and ORM reads against
  migrated tables were checked.

The combined test run exposed database-scoped pg_trgm creation inside a disposable
schema. The fixture now installs it outside those schemas before replay. A second
check exposed Alembic naming-convention expansion of explicit check names; the
unapplied forward revision now uses native op.f(), preserving the intended names.
Applied migrations were not modified. The older reconstruction-specific graph
suite still requires broader canonical contract alignment. Merge commit, hosted
CI, canary deployment and authenticated browser acceptance remain outstanding.


## Inbound credential isolation and identity-bound resource convergence

The reconstruction contract audit exposed a real shared-boundary defect: provider
connections could issue host inbound credentials. AppConnectionAuthentication
now uses its existing native-inbound discriminator for both issuance and token
validation. Credential receipt listing and revocation remain available after
connection disablement. No parallel authentication implementation was added.

AppInboundConnectionApi contained two imported functions accidentally passed as
positional arguments to list_inbound_connections; these are removed. Connection
listing now reads one app definition per bounded page instead of per row.
Registry mutation receipts now consistently carry Cache-Control: no-store.
The legacy UI envelopes (apps/app) and default service list response are retained;
restoration tests now explicitly request pagination when they expect items.

New inbound resource approvals record the current connection identity hash.
Replays keep access revision stable while identity changes require a new approval
revision. Existing unbound legacy rows remain usable; they are not rewritten or
stamped as verified. Identity-bound rows from a prior connection snapshot are
excluded at resource discovery, native card scope, card creation, card selection,
execution grant and signed signal ingress. A newly issued token cannot restore an
old identity-bound resource approval. Existing ownership, shared-board and
organization automation gates remain in force.

Evidence:
- inbound-native-credential-resource-regression.txt: 69 passed, 25 intentional
  SQLite skips for PostgreSQL-specific tests.
- resource-account-change-postgres-final.txt: four passed, zero skips; actual
  SQLite/PostgreSQL resource discovery and execution reject previous identity
  approvals after current credential reissuance.
- identity-scope-active-regression-final.txt: 70 passed, zero skips; resource,
  grant, Active, interaction and card resource selection suites.
- dokploy-resource-selection-regression.txt: four passed, zero skips. The failed
  fixture omitted is_selected for its second resource; explicit selection now
  preserves the native consent gate.
- graph-core-canonical-contract-final.txt: 52 passed before the relationship
  fixture failed for missing native execution tables/sequence. Its fixture is
  being aligned separately. Reconstruction resource/intent tests still use old
  signatures and need canonical contract updates. Full suite is not green.

The Phoenix source integration includes upstream PR #682 (45171c109), and the
staged socket tree has zero legacy TypeScript/package files. Phoenix still serves
WebSocket transport; removing Node socket implementation does not remove that
protocol. The 393-test Phoenix receipt proves the exercised source, not deployed
canary image identity. Integration merge commit and deployment remain unproven.

## Dependency authority and reconstructed acceptance convergence

Execution authority now reuses `card_dependency_state` and includes its revision
in the immutable authority version. A prerequisite change therefore cannot leave
an old accepted authority looking current. Completion follows upstream
`DependencyConditions`: source/material cards do not satisfy prerequisites;
`counts_as_completed` remains authoritative even when the stage or relationship
type is inactive. No prerequisite names are added to the authority snapshot.

Evidence from disposable PostgreSQL:

- `native-dependency-policy-postgres-final.txt`: 19 passed, no skips.
- `dependency-authority-lifecycle-bounded.txt`: 84 passed, two explicit-new-run
  cases deselected for investigation.
- `new-run-final.txt`: both explicit-new-run cases passed after adding the SDK's
  required panel icon and explicitly approving the fixture resource for the
  changed app trust identity. Runtime authorization was not relaxed.
- `native-notification-delivery-final.txt`: 53 passed for the previously retained
  notification test changes.

The restored graph acceptance fixture now declares optional Active/Review stages,
creates the native change sequence, uses current resource and interaction method
signatures, registers the Review route, and binds an event destination before
requesting an answer. Its complete acceptance flow is still under investigation;
it must not be counted as passing. The independent consumer branch requires an
installed acceptance module and remains unverified. Merge commit, hosted CI,
canary image deployment and authenticated browser acceptance remain outstanding.

Latest restored intent run (`restored-intent-alignment-fifteenth.txt`) reaches
past native Active/HITL answer delivery validation, SCIM/private visibility,
cross-app isolation, read authority revocation and Review setup. It still fails
because a retained fixture parses the native integer `card_revision` as an ISO
timestamp. Correcting that fixture must retain both content-change and movement
revocation coverage. It remains a failed receipt, not completion evidence.

## Restored PostgreSQL acceptance now passes

`restored-auth-postgres-full-current.txt`: 55 passed, one optional independent
consumer branch skipped. The ordinary full execution flow covers concurrent
intent admission, outbox and Active rollback, immutable acceptance, current
resource/visibility checks, Active start, human answer validation, Review,
heartbeat movement/content revocation, credential revocation, expiry and stale
generation. Fixture revisions use the native integer `last_change_seq` rather
than parsing authority revisions as timestamps.

This run exposed a real credential revocation race: authentication did not lock
its credential row. Native authentication now takes the existing transaction's
row lock, so concurrent revocation commits before the waiting request reads and
rejects it. `credential-lock-lifecycle-regression.txt`: 81 passed after this
change. The retained graph test directly verifies the PostgreSQL lock wait and
post-commit denial. No token, consent or audit history is reset.

The current upstream main was fetched again and remains included in the merge
candidate. The deployed canary branch has newer customer-repair/CI commits than
the original union; those must also be retained before promotion. The source
merge can be recorded for review, but no runtime promotion is proved here.

## Recorded integration and current canary repair

The upstream integration is recorded as merge commit `b50dd2667`, preserving
both original union and Phoenix reconstruction parents. The latest deploy
branch (`9f5a9838e`) is being merged into that candidate to retain customer
creation and portable CI repairs. The only two merge conflicts were SDK lock
entries: both now match the retained 0.2.37 wheel's actual SHA-256.
`latest-canary-repair-regression.txt`: 11 passed for external/default visibility
and immutable image-request contracts.

Phoenix checks reuse the generic `CI_RUNNER` repository variable when set and
keep the public Ubuntu default otherwise. Push checks are restricted to main
and deploy branches; draft pull requests do not run the expensive check.
Production image building stays in the immutable image-request/central executor
workflow rather than duplicating a release-image build in the validation job.
No private runner identifier or executor endpoint is added to source. A draft
review is preparation, not CI or deployment acceptance.

## Concurrent authentication and installed consumer acceptance

Broader archive and signal concurrency checks exposed lock inversion after
adding an exclusive credential lock. Authentication now takes `FOR SHARE`,
which permits concurrent authenticated reads while blocking credential
revocation/update. Runtime-only credential checks use the same shared lock;
start/authorization no longer repeat credential acquisition and authentication.
Lease, card and event mutation locks remain exclusive. This avoids credential
lock upgrades after project/event locks without weakening the revocation fence.

- `shared-credential-lock-final.txt`: six passed, one unconfigured consumer
  branch skipped; includes actual PostgreSQL concurrent archive reports and
  credential revocation wait/denial in the full ordinary execution flow.
- `shared-lock-lifecycle-final.txt`: 81 passed after removing duplicate locks.
- `independent-consumer-final.txt`: both ordinary and independently built
  consumer wheel branches passed (two tests, no skips). The external receiver
  and Review consumer run their own implementation; only DNS/network transport
  and the signing secret are disposable fixture values. This establishes local
  SDK/HTTP/PostgreSQL consumer acceptance, not deployed provider execution.
- `current-binding-migration-second.txt`: 25 passed. The binding fixture now
  applies the actual forward migration, including native intent/Active audit
  tables. Identity-hash default removal uses Alembic batch alteration so the
  existing SQLite migration coverage remains supported. Actual populated
  PostgreSQL migration replay is included.
- `hosted-contract-final-paths.txt`: 20 passed for SDK resources and registered
  connection context after explicitly selecting fixture resources.

The workflow-equivalent 25-path suite reached 252 passed/26 skipped before the
old binding migration fixture failed. A complete final run is in progress and
is not yet recorded as successful. PR #688 is ready for review; its three
self-hosted checks remain queued. Runner permission inspection returned 403 for
this API token and the browser requires GitHub sign-in. The narrow new
workflow/ref access request is pending. No canary merge or deployment occurred.

Final workflow-equivalent receipt: `current-hosted-contract-parity-final.txt`
reports **296 passed, 26 skipped, zero failures** across all 25 paths declared by
Check App Contracts, with both disposable PostgreSQL test URLs configured.
Skipped branches are not passing evidence. The SDK artifact verifier also
passed against the reviewed 0.2.37 wheel and both lockfiles. This is local
workflow parity, not a hosted-CI conclusion. The latest hosted checks remain
queued. Copilot review failed with quota HTTP 402; this is not a source failure
or an independent review approval.

## Phoenix CI readiness and cutover contracts

The Phoenix workflow now explicitly handles `ready_for_review`, matching its
existing draft guard. Without this event a draft-to-ready transition did not
schedule the Phoenix gate. Synchronization continues to schedule the same gate;
no additional build job or runner configuration was introduced.

`phoenix-cutover-gate-current.txt` reports 112 passed and 4 skipped across owner
preflight, cutover, retained cutover evidence, OTEL soak validation, and editor
owner routing. The four skipped cases require unavailable PowerShell; Bash
cases ran. This proves these local contract checks, not live runtime cutover.

## Authorized runner access and Phoenix container gate

The user authorized the four integration-branch validation workflow refs.
Runner-group readback confirmed the original 15 entries retained, exactly four
added, and workflow restriction still enabled. Private settings receipts remain
outside tracked source. Device authentication completed successfully.

Phoenix run 38258496843 reached the runner but failed during job setup because
that runner requires a job container. The workflow now uses the same standard
Ubuntu 24.04 job container as the existing contract workflows, installs runtime
prerequisites, and reuses setup-uv instead of assuming pipx is preinstalled.

The API cross-runtime subset initially exposed two obsolete upstream fixtures:
an untyped actor and an authentication stub lacking the current allow_oidc
argument. Fixtures now preserve the user guard and after-commit reorder
publication boundary. `phoenix-api-contract-current-final3.txt`: 191 passed,
zero skipped. This does not prove the whole cross-runtime workflow or deployment.

The Phoenix runner job record confirmed the configured self-hosted group.
A duplicate manual dispatch (38259884108) was cancelled after the pull-request
synchronization automatically scheduled the same commit. Phoenix validation now
requires CI_RUNNER, skips unconfigured/fork contexts, and has no GitHub-hosted
fallback. Per-branch concurrency cancels superseded or duplicate runs across
pull-request and manual dispatch events. The standard Ubuntu image is the job
container on the configured runner, not a hosted runner selection.

AI cross-runtime contract follow-up: two more HTTP authentication fixtures now
accept and explicitly reject OIDC fallback for non-MCP routes, matching the
current middleware contract. `phoenix-ai-contract-current-final.txt` reports
231 passed with zero skips across Ollama commands/recovery, internal graph
requests, application startup, attachment upload, native run recovery, file
client, board chat and editor socket run tests. Production auth remains unchanged.

Phoenix Makefile static gate audit: all 109 Ruff check paths pass after replacing
two SQL boolean comparisons in notification fanout queries with SQLAlchemy
`is_(True)`. Notification delivery/command regression: 74 passed. All 34 Ruff
format gate paths pass after formatting four integration files with the existing
formatter. AST comparisons confirm those four files retain identical behavior.
The notification query change is separate and covered by the regression receipt.

UI runtime follow-up: socket stream errors (1), board chat store (3), and Ollama
model tracker (1) all passed using the workflow's Node 22 major and installed
locked dependencies. Initial Yarn invocation failed before tests because local
npm configuration required NODE_AUTH_TOKEN; a noncredential placeholder was
used for offline script execution only, without dependency installation.

The hosted-equivalent App Contracts run remained in its first captured test,
with a live pytest process, no written JUnit file, and no observed database lock
wait. No test completion is inferred from intermittent empty database sessions.
Enable pytest's built-in faulthandler_timeout=60 for the next execution to retain
thread stacks on a stalled test without adding dependencies or relaxing the gate.

Phoenix run 38261627844 failed at the custom-container execution boundary of
setup-beam before native compilation, then was cancelled by a newer PR event.
It is not a failing Phoenix test receipt. The CI job now directly reuses the
exact digest-pinned Elixir/OTP build image from the upstream socket Dockerfile,
initializes Hex/Rebar through Mix, and retains exact-version dependency/PLT cache
keys. This removes redundant BEAM installation and introduces no image build or
private runner configuration. YAML and Dockerfile image equality were verified;
the next actual CI run must establish execution compatibility.

Workflow Stages CI run 38260506393 passed registry/policy and native app
management, then failed four card identity tests because three legacy fixture
callables omitted the current bounded-query `limit` parameter. This matches the
local failure. Fixtures now accept the explicit keyword and slice returned
columns by the supplied bound. User/Bot identity and forged actor rejection
assertions remain unchanged. Local management/webhook/query/native adapter/MCP
and column service suite: 113 passed, no skips. The original CI failure remains
preserved; PostgreSQL transition CI was not reached in that run.

The pinned BEAM image successfully ran version reporting and Hex/Rebar setup.
Run 38263205909 then failed at the first nested working-directory script through
the runner's custom container hook. No Mix diagnostic reached the log and the
cache key lacked a lockfile hash, so this is still execution-boundary evidence,
not dependency resolution failure. Align script working directories with the
existing successful root-based jobs and explicitly enter src/socket for Mix.
Cache file hashes remain rooted at the checkout. The exact hook root cause and
actual dependency/test success still require the next run; no gate is removed.
