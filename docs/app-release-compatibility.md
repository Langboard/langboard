# App platform compatibility and release gates

Current review update: 2026-10-12. The 2026-10-11 tables below are historical
snapshots. Package availability, source tests and a
deployed release are separate evidence. No production compatibility is claimed
from this table. Update it when an artifact, declaration or migration changes.

## Current review candidate

| Component | Current source or artifact | Evidence and limitation |
| --- | --- | --- |
| Host review PR #681 | `f02278225890d4075b2756f73a6c912fb769b857` before the archive-model review correction | Explicit human new-run API and native/panel controls; local SQLite/PostgreSQL execution controls: 8 passed, physical browser fixture: 5 passed. Not deployed acceptance |
| Python SDK | `0.2.33`, wheel SHA-256 `b021a7a849b3a712851069eac887dca3c937f669eea6095edf1ba1c5d4f3bd12` | Host vendor provenance; standalone SDK source tests: 92 passed |
| Panel SDK | `0.2.6`, tarball SHA-256 `d4d30fa7322dd9a4f3817a8fb15b111d6ab4b348ff53829e671bbac8229aa652` | Host vendor provenance; standalone panel tests: 22 passed |
| Independent app consumers | GitHub `5f4a48c`, GlitchTip `6fbfae2`, Dokploy `5bf47fe`; panel `0.2.3`, server `0.1.3` | Their existing versions are retained. A newer host SDK does not prove deployed cross-version acceptance |
| Official static packages | Public `v0.1.1` ZIPs for all three apps | Anonymous downloads returned HTTP 200 on 2026-10-12 with matching recorded SHA-256. Recorded install API tests: 6 passed across SQLite/PostgreSQL, including installed panel and both theme icons; no server execution code in the ZIPs |
| SDK review CI | Push `15bc8fbcc6b3c050158e8003bfaa889ba3d91188`, run `38154305746` | Uses the reviewed branch rather than a synthetic PR merge ref. Runner assignment was still queued when inspected; no remote pass claimed |

The historical migration-head and full-suite entries below must not be read as
the installed canary revision or a rerun of every current contract. Exact live
database, image and authenticated provider/browser evidence remain release gates.

### Archive outcome concurrency review

The shared `_current` helper already acquired `FOR UPDATE` by default, so the
reported unlocked archive-read race was not present in the reviewed source.
The outcome read/report path now explicitly uses the delivery path's outbox ->
archive lock order, then refreshes the locked archive receipt with
`populate_existing`. This avoids inversion with a delivery transaction that
already owns the event and ensures revision comparison uses the committed
receipt after waiting. Two real PostgreSQL transactions exercise different
reports at the same expected revision, including an outbox-first delivery lock.
Exactly one report commits; the other conflicts, the persisted report remains
unchanged, and replaying the accepted report is idempotent. This is local
database evidence, not provider or deployed acceptance.

### Shared editor ref review

`EditorWrapper` always creates its internal ref before choosing the optional
caller ref. Adding or removing a caller ref therefore preserves React hook
order and the current editor. The provider prop type distributes `Omit` over
its discriminated union, preserving required form fields without an `any` cast
or a file-wide lint exemption. Physical browser tests at 1280 and 390 pixels
type a draft and repeatedly switch between internal and caller refs, checking
the text remains and no page error occurs. The UI TypeScript project and the
changed-file ESLint checks pass. This does not prove collaborative Yjs server
updates preserve unsaved drafts; that separate review remains open.

### App model review boundary

Native app models declare compound uniqueness through existing `unique_groups`
field metadata. `domain/models/AppSchema.py` owns the named database checks and
query indexes and attaches them after model registration. The existing Alembic
migrations retain their original names and definitions; this refactor does not
change the deployed schema. Execution request/outbox, interaction request,
resource identity and signal occurrence constraints retain their original column
order. Package version changes are unnecessary for this internal model cleanup.

## Reviewed host and SDK combination

| Component | Reviewed identity | Evidence and limitation |
| --- | --- | --- |
| Host API integration source | `878541385a6779591224acd7f75344267a042d54` | Public app creation to independent HITL exercised on PostgreSQL; not a deployed image |
| API migration head | `d3c6fd51a7e9`, parent `c2b5ec40f6d8` | One Alembic head; archive sync defaults disabled, and archive/response history is durable. Active sync and stored history protect downgrade boundaries |
| Python SDK | `0.2.27`, source `05440591f1b3d03b9d4037f0b38d67a336012bc6` | Reviewed wheel, both host Python projects pinned; `scripts/sdk/verify_python_artifact.py` passes. Includes archive outcomes/reads and renewed signal consent on resource type changes |
| Host panel SDK | `@langboard/app-panel@0.2.3`, source `f4477be` | Vendored tarball and UI lockfile. Message protocol remains v1 |
| Example Monitor | package `0.1.1`, source `5d4d312b2c8e709cfc455d0cfb65eafa47a8a832` | SDK `0.2.23`; baseline declaration schema 2, app version `1.0.0`, signals only. HITL/card creation require a separately approved definition and consent |

Exact reviewed artifact digests:

| Artifact | SHA-256 |
| --- | --- |
| Python SDK `0.2.23` wheel | `eab73a2ca2b1ec47946000cafddfe5ada484d86db44de89823c89cb4ecbb423e` |
| Python SDK `0.2.26` wheel | `57e31b39c2bea7c09fddd47617e23726e9b472984d0ad856ee24d159f7e3be69` |
| Python SDK `0.2.27` wheel | `afee6ebe8a8ef4426960c0d3cb5bca6f9be977cde10664385c2981d8dd06bd07` |
| Panel SDK `0.2.3` tarball | `b347a9ebb87be8a48b38daca38e03a099b284094cebc21e867e6cb8f7481b383` |
| Panel SDK `0.2.0` tarball used by existing apps | `6e232e4e055760a6dd30d1e07a86de491af60e36471d6da4705512d6aeb3661b` |
| Example Monitor `0.1.1` wheel | `7ce265d8009f346263d3f157a8406d981da71b45b5dcd3dd6d1e4d2ac21b6cf9` |

Host provenance is in `src/sdk/vendor/provenance.json`; independent Monitor
provenance is in `src/api/tests/apps/vendor`. Package imports validate SHA-256
and module location. Python package semver, panel protocol version, declaration
schema, app version and database revision must not be treated as one version.

## Existing independent apps

All three packages are `0.1.0`; their default declaration is app version
`0.1.1`, schema 1, with `panels.render` and `signals.read`. Explicit
`--workflow-cards` selects app version `0.2.0`, schema 2, card creation/read and
mandatory lifecycle policies in CLI and HTTP declarations. GitHub interactions
are a separate optional profile. The published-signal profile is app version
`0.2.1`, with a finite `signal_policy` and `signals.publish`; it remains opt-in.
All three server helpers consume `@langboard/app-server@0.1.2` and panel SDK
`@langboard/app-panel@0.2.3`. Package updates do not establish deployment
acceptance or preserve the old app-to-host combination gate.

| App | Reviewed source | Native external resource | Fresh source test result |
| --- | --- | --- | --- |
| GitHub Panel | `020b1d368dffb1fe2de171e23dadacb3d0a3a844` | `repository` | 23 passed |
| GlitchTip Panel | `dc7e5f8` | `project` | 24 passed |
| Dokploy Panel | `cca095ff054c9d8883707ec391ad3ccedc4343a6` | `application`, `compose` | 15 passed |

Server SDK `0.1.2` at `09f703d` validates the native publication receipt:
`signal_uid` must be a native UID and `duplicate` must be a boolean. HTTP success
without that receipt is an unknown outcome, never delivery acceptance. All apps
retain saved observations and perform no automatic publication retry. Dokploy
stops subsequent batch publications after an ambiguous receipt. GlitchTip's
explicit recovery command reuses its saved envelope without rereading the
provider. These cases exercise controlled transports and temporary files, not
live provider delivery. Server SDK tests: 4 passed.

These local tests cover static service/manifest, host widget rendering, signal
requests, source-scoped cancellation, provider normalization and optional adapter
behavior. They do not prove the previous `0.2.0` app to `0.2.2` host combination
or the current `0.2.3` packages in an authenticated deployed browser.
Keep that cross-version gate open; do not force a package upgrade solely to hide
the untested combination.

## Native integration evidence

`test_signal_inbox.py`, `test_app_card_interactions.py` and the independent
Monitor case in `test_app_card_creation.py` pass together: 7 cases, 9.93 seconds.
The card creation suite passes 26 cases, with 25 SQLite combinations skipped
because native sequence/readiness uses PostgreSQL. The actual PostgreSQL cases
run against a disposable database. No live provider mutation is performed.

Signal, Inbox and app-owned cards remain independent. Inbox-created cards do not
acquire app ownership. Native app creation stores ownership and selected-resource
audit records in the card transaction. HITL response, webhook delivery, reception
ACK, explicit workflow transition and runtime attestation remain separate facts.

Card signal evidence selects the event type from the same stored row as its
representative signal UID. Same-occurrence outcomes still aggregate to a conflict
when they disagree; lexical event-type ordering must not fabricate provenance.
SQLite/PostgreSQL acceptance: 6 cases covering occurrence ordering, same-time
provenance and constant batch query count. This is database contract evidence,
not live provider or workflow-transition acceptance.

## Deployment and rollback gates

`Check App Contracts` is a separate native API contract workflow. It uses the
configured runner and a disposable PostgreSQL service, verifies the reviewed
SDK against both Python project lockfiles before installation, and preserves
JUnit results. It includes signed delivery, bounded retries, trust changes and
revocation, alongside card and HITL contracts. The same selected cases passed
locally after `uv sync --frozen`, without a wheel import-path override:
117 passed, 25 skipped, including installed SDK 0.2.25 public HTTP archive consent, delivery, outcomes and migration
guards. SDK artifact verification tests: 7 passed.
That result does not establish a successful remote workflow run. Draft PRs
remain skipped; a ready same-repository PR or explicit dispatch is required.

Before release approval, record immutable API, worker, socket and UI image
digests with their source revisions; record the installed database head and the
consuming app versions. An older running source revision is not evidence for
this candidate. Different API/worker images may be expected, but their contract
and source compatibility must be verified explicitly.

Required remaining evidence:

- Approved build/release job for the exact preserved candidate, including its
  result and image provenance. Draft or Skipped checks are not passing CI.
- Existing database upgrade with retained ownership, response and transition
  history. Destructive downgrade is not a rollback strategy; retain a compatible
  schema and verify the old application before choosing rollback images.
- Authenticated FHD/mobile host panels with existing and current app packages,
  current consent, trust-target changes and connection/resource revocation.
- Independently deployed receiver, real provider observations and explicit
  action readback, including duplicates, timeout/unknown outcomes and recovery.
- Image rollback with readable history and denied revoked authority. No test
  may merge a review PR or silently mutate external resources.

Runner topology, private build endpoints, credentials and organization-specific
deployment configuration belong in the operator's delivery system, not this
public repository. The host uses generic runner configuration. A missing runner
or product release workflow remains an explicit delivery blocker.

The installed SDK 0.2.26 archive API subset passed 13 PostgreSQL cases, including receipt reads after unknown and applied reports and rejection after consent withdrawal. The full 117/25 gate above records the preceding 0.2.25 run; it has not been rerun for 0.2.26.

Installed SDK 0.2.27 panel signal scope subset: 14 authenticated HTTP cases on SQLite/PostgreSQL, including resource-type filtering, scoped cursor rejection and renewed consent on resource scope expansion. SDK standalone: 82 tests. Artifact verifier: 7 tests. Earlier full-gate results remain historical until rerun.

Panel SDK 0.2.3 shares source-scoped pager lifetime management. Standalone panel SDK: 20 tests. GlitchTip: 18 tests; Dokploy: 14 tests, both consuming the reviewed 0.2.3 tarball. These are local contract checks, not deployed browser or provider readback.

Host SDK artifact was installed in an isolated offline Yarn project and matched reviewed inbox source bytes. Full host offline install was blocked by an existing axios@^1.20.0 cache miss; no full UI build success is claimed for this change.

Independent provider snapshots: `tests/apps/vendor/provider-signal-contracts.json`
records current external repository commits, their generated publishing declarations
and normalizer evidence. Six native HTTP/SQL cases passed across SQLite and
PostgreSQL: signed acceptance, immutable stored fields, inbox/time-basis readback,
duplicate replay, undeclared outcomes and revoked credentials. Snapshots are
compatibility fixtures, not execution of external services in the host test process.
Refreshing a fixture requires running the referenced app's actual declaration and
normalizer functions and retaining its source commit. This local gate is included
in the existing contract workflow; deployed account/connection/browser acceptance
remains separate.

Latest SDK 0.2.27 complete local contract selection initially produced 123 passed, 25 skipped and one failure: the archive test asserted the retired installed version 0.2.26. The test now matches installed SDK against reviewed provenance, preserving version validation. Its PostgreSQL HTTP case passed after repair. The full repaired selection passed: 124 passed, 25 skipped, zero failures/errors; remote Draft checks remain skipped. Skipped SQLite combinations require native PostgreSQL sequence/readiness behavior and were exercised by the corresponding PostgreSQL cases.

Python SDK 0.2.29 (`e83c19b`) applies the same native signal receipt contract as
server SDK 0.1.2. Standalone Python regression tests: 87 passed. Malformed HTTP
success receipts raise MutationOutcomeUnknown without retrying. The host root
and shared Python projects pin the same reviewed wheel and digest.

### Collaborative editor snapshot protection

The shared editor restores the authoritative Markdown snapshot once per document
and edit session. Later server snapshots do not reset its live Yjs document.
Explicit recovery and cancellation callbacks retain the existing remote-editor
guard. A local Hocuspocus server and the production Plate/Yjs editor reproduce
the delayed-snapshot defect before the fix on 1280px and 390px viewports, then
verify unsaved typing survives and explicit reset still works after the fix.
Run `node_modules/.bin/playwright test -c playwright.collaborative-draft.config.ts`
from `src/ui`, with the UI and socket workspace dependencies installed. The
transport-only fixture replaces authentication; production authentication,
multiple-user collaboration and deployed acceptance remain separate gates.
### Official release failure handling

Official installation validates the bounded GitHub release response before
downloading an asset: valid JSON object, an asset list of objects, stable release
identity, positive integer asset ID/size, and a SHA256 digest string are required.
Malformed responses are reported as bad requests through the existing registry
error boundary and do not create an app definition. The package suite passes
30 cases, and the authenticated package HTTP suite passes four SQLite/PostgreSQL
cases, including malformed-response rejection and existing install/update/disable
behavior. These local checks do not replace deployed installation acceptance.

The native contract workflow includes package validation and authenticated
installation regressions. Its runner must support GitHub service containers
for the declared disposable PostgreSQL service, or the operator must supply an
approved equivalent database lifecycle contract before changing the workflow.
Runner labels alone do not provide Docker daemon/container-hook support. No
production database or shared application database is an acceptable substitute.

Card-bound model filters in the outline, description, attachment, checklist and
comment views depend on the current card model, following the model-hook
dependency convention. The outline browser fixture switches between cards with
different attachments on 1440px and 390px viewports and confirms the section is
added and removed with the current card. This exercises local model navigation,
not authenticated deployed navigation or same-UID model replacement.

Workflow mapping API and authenticated HTTP tests are included in the native
contract workflow. Their local SQLite/PostgreSQL selection passes 30 cases with
one SQLite row-lock case skipped; the PostgreSQL concurrent stage edit and
same-revision disable cases pass. Missing-stage creation, inactive-stage denial,
stale expected-stage conflict and membership/role revocation are exercised through
authenticated HTTP on both databases. Eight browser fixture cases cover missing
stage assignment/creation, discard, read-only denial and explicit replacement
confirmation on desktop/mobile. These remain local evidence; deployed workflow
mapping acceptance is still required.

## Localization regression gate

The native UI literal audit identified ten new candidates in the current union.
SCIM issuer, group ID, external group ID, and GitHub app connection identity now
use English, Korean, Japanese, and Chinese locale keys. Connection identifiers
remain interpolation values rather than translated user data.

The six remaining candidates are invariant product names (Langboard, GlitchTip,
Dokploy) and literal URI examples (`https://`, `secret://ref/`). Their exact file,
kind, text, and occurrence count are recorded in the existing baseline; the
scanner is not weakened and additional occurrences still fail the gate.

Local verification: no new UI literal candidates, four scanner tests passed,
29 namespaces and 2538 values per translated locale passed complete parity and
placeholder checks, changed-file ESLint and UI TypeScript passed. This is
source-level evidence and does not establish deployed localization acceptance
or finish the full UI string migration card.

## Card column history model transition

The timeline effect now depends on the card model, matching the model hook
contract. It clears the previous snapshot when its card/column subscription is
replaced; outstanding requests remain aborted and aborted responses cannot
write history. The existing committed activity listener and API env cast remain.

A browser fixture uses real ProjectCard models, BoardCardProvider and
CardColumnHistory, with the history HTTP response held during a card switch.
Before the fix both 1440px and 390px cases displayed the previous card history.
After the fix both pass, including ordered return transitions and keyboard-focus
actor details. This uses controlled HTTP responses, not deployed activity
persistence or actual multi-user socket delivery.
