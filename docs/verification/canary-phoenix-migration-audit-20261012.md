# Canary / Phoenix migration compatibility audit

Status: runtime cutover deployed; complete user acceptance remains pending. The
dated findings below preserve the original investigation. See the latest
deployment checkpoint at the end before using an older runtime observation.

The original canary union is retained at `2e9b4b243044903fe3edfd85bbbed5a52164ebb1`.
At the initial audit, the integration worktree had an unfinished merge and was
not deployable. The customer repair in PR #686 used a separate canary review
path; the larger integration was not a prerequisite for that repair.

## Evidence

The static Alembic DAG audit found two heads:

- `a5b6c7d8e9f0`: 57 reachable revisions, no repeated table creation within this head.
- `c97659de63f4`: 117 reachable revisions, no repeated table creation within this head.

Combining both reachable sets exposes 24 repeated table-creation declarations. A merge revision alone cannot reconcile these schemas. The audit did not execute SQL.

Evidence: `.verification/migration-duplicate-create-audit.txt` and `.verification/migration-dag-merge-audit.txt`.

## Required compatibility work

1. Preserve applied revision identifiers and data. Do not stamp an incompatible database or discard retained history to make upgrades pass.
2. Compare the deployed revision and schema with both lineages before choosing the adoption path.
3. Use current main model and service contracts as the target, with explicit migration of retained canary fields and constraints. Avoid parallel runtime implementations.
4. Replay upgrades from a fresh database and a populated canary fixture retaining requests, delivery attempts, receipts, ownership, relationships, consent and reviews.
5. Recheck authorization and idempotency after conversion. Table creation alone is insufficient.

## Concrete differences

- Outbox: canary uses `(request_id, event_type)` uniqueness and `attempt_count`, `lease_until`, `delivery_history`; Phoenix uses unique `request_id` and `attempt`, `claim_expires_at`, `delivery_receipts`. Renaming fields does not establish compatibility with existing multiple events per request.
- Review: canary stores `fingerprint` and `receipt`; Phoenix stores `snapshot` and `receipt`. Existing review evidence must not become a fabricated current authority snapshot.
- MCP consent: Phoenix adds a connection foreign key and positive-revision check. Retained consent and connection deletion need explicit compatibility verification.
- Acknowledgment: Phoenix makes request and event identities unique. Inspect existing rows before adding that constraint.

## Narrow model verification

The acknowledgment and MCP consent models now follow incoming main contracts. An isolated subprocess loaded the merged sources with the retained native ORM. Duplicate request/event SQLite inserts were rejected; consent foreign-key metadata and the 200-character runtime-reference validation bound were inspected.

The first verification incorrectly assumed `max_length` became SQL column length. It failed; `.verification/app-model-merge-check-first-failure.txt` preserves the output. The corrected check inspects validation metadata and does not claim a SQL length constraint.

Passing evidence: `.verification/app-model-merge-check.txt`. This is not PostgreSQL migration replay, foreign-key enforcement testing, merged-service acceptance or live canary validation.

## Updated 2026-10-12: applied lineage convergence

The initial two-head/24-duplicate finding is resolved in the integration candidate.
Nineteen unapplied reconstruction revisions were backed up and retired. Applied
canary revisions and the nine upstream notification/runtime revisions remain;
`d0876afe1402` joins their heads. Additive `e1987bdf2503` introduces only missing
resource-selection revision / identity hash columns and human-intent / Active
receipt tables. The old request/outbox/Review/counter/history schema is preserved.

Fresh PostgreSQL and populated `c97659de63f4` upgrades pass. Retained rows, two
outbox event types per request, delivery attempts/history, canonical generations,
ownership, selections/audit, package metadata and MCP consent remain equal. Empty
forward roundtrip passes; rollback with intent/Active evidence is refused. Evidence
is the reusable `tests/migrations/test_phoenix_canary_union_postgres.py` and private
`phoenix-canary-migration-postgres-final.txt` (1 passed, no skips).

This resolves migration DAG and populated replay, not the overall release. Full
native operation on that migrated schema, constraint inventory, reconstruction
acceptance alignment, application lifespan and deployed canary remain pending.


## Updated 2026-10-14: validation candidate follows the current canary

The profile-validation candidate now integrates the current canary source while
retaining the upstream Phoenix runtime and its integration entrypoint. The
integration preserves the visibility-transition API, UI and tests, the patched
OS-library version guard, the reduced UI build graph, and the locked dependency
layers. API validation no longer requires the cron runtime. Document-worker
validation still includes the real document-processing dependency set and CJK
fonts. Named profiles remain `socket`, `default`, `api`, `worker`, and `ui`;
fresh-cache invalidation is restricted to the test execution stages.

Focused request-envelope, image reproducibility, and document-image checks passed:
30 tests and 26 subtests. Static profile resolution uses each profile's build
context, and all declared targets and fresh stages exist. This does not establish
actual Phoenix container execution, PostgreSQL integration acceptance, or live
canary acceptance. The current narrow API/UI delivery remains separate from the
larger Phoenix candidate until those full gates pass.

## Updated 2026-10-14: deployed realtime runtime is still Node

A read-only inspection of the canary deployment found `langboard_socket`
running the `langboard-socket` image. `docker top` reported two Node processes,
including `node /app/dist/`; the container command was
`["pm2-runtime", "ecosystem.config.cjs"]`. This is direct evidence that the
legacy Node realtime runtime has not been retired in the deployed canary.
No container was stopped, rebuilt or replaced during this inspection.

The inspected candidate was `91efb8ffc9d357c93b46f58efefd6c88be1bb05a`.
Its `socket` image profile produces the Elixir release from
`src/socket/Dockerfile`, with `/app/bin/langboard_socket start` as its entrypoint
and port 5690. The upstream Compose service is `socket-phoenix`, with readiness
at `/health/ready`, API/Kafka/Redis/graph dependencies, and retained editor-sync
storage. The directory name `socket` does not identify a Node implementation;
WebSocket transport also remains part of the Phoenix implementation.

An API/UI-only release cannot demonstrate this runtime migration. Acceptance
requires exact-source Phoenix validation and a signed socket image, a release
declaration covering the runtime and compatible API/UI routing, and live
authorization, notification, editor-sync and board-chat checks. Removal of the
old Node service follows successful cutover verification; its currently healthy
status is not Phoenix acceptance. The original canary database and retained
application history must remain intact throughout the cutover.

## Updated 2026-10-16: immutable Phoenix runtime and native subscriptions

The earlier observation of an active Node runtime is superseded. The official
canary application completed successfully with API source
`0e14576b60f476e0e16b3ace6e5ef184e496cf24`, UI source
`35aa2fc374a2b7e6250021096f1b4cfe17f0fd23`, Graph source
`322b7349975f37d0b0be3c857c3eab0739d8a0ca`, and retained Worker/Phoenix source
`f0e6cfe1dae05ee95281b153c014c8f073fc3d0f`. These are distinct verified
component sources; they must not be relabeled as one image build.

Actual container readback confirms healthy API, UI, Phoenix, Graph and gateway,
a running worker, and the retired Node socket container stopped. Runtime source
and dependency overlays are absent from the new product containers. The database
remains at `e1987bdf2503`; the final application recorded no live schema change.
The existing host checkout and retained data were preserved. Langboard main was
not changed.

Authenticated Chrome rendered the board and cards. Network evidence records
board, board-settings and user subscription acknowledgements, dashboard batches
of 64 and 28, card batches of 64/64/64/64/33, and live dashboard change events.
Early reconnects occurred; subsequent observation received the complete
acknowledgements and no further close. This is bounded native evidence rather
than a claim of indefinite connection stability.

The first native chat prompt failed without a model reply. Read-only
configuration comparison found the shared internal authentication present and
equal in API/Phoenix, but absent from Graph; Graph returned 401. The official
configuration application completed successfully in run `38787044616`, forwarding
the same existing secret to Graph without creating credentials or relaxing
authentication. The API, Phoenix and Graph then had matching existing service
configuration and healthy runtime states. This applied a configuration repair,
not a new Graph product image.

A single post-fix prompt through the normal authenticated Chrome chat panel
received `CANARY_OK`. Reloading the page and reopening the panel retained both
the prompt and model response. The first failed execution remains recorded;
no active lease was present before the post-fix request. This proves native
text-chat execution and persistence for that session, not tool execution,
attachment handling, every provider, or indefinite stream stability. The generic
Compose fix also forwards the same setting; its 49 Graph authentication
regressions pass locally. The source correction is included with the native
attachment feedback follow-up; product release verification remains separate.
Complete user acceptance is still open.

The first API activation exceeded the 240-second readiness deadline before
eventually becoming healthy. A later official application of the same signed
release succeeded. The failed execution is retained; the cold-start cause is
not established by the subscription change.

A later native macOS picker selected a synthetic 68-byte UTF-8 attachment. The
server returned 201; an independent MCP attachment read and a normal reload
confirmed persistence while retaining existing attachments. This was a staff
session, not external-user native upload acceptance. The existing tab did not
show the new file immediately. The follow-up hydrates the existing UID-based
attachment model from the confirmed HTTP response, matching the existing socket
handler. It adds no polling or parallel store. Exact UI product validation and
post-deployment immediate-list verification remain pending. The missing live
update event cause is not established by this bounded reproduction.

## Editor preservation follow-up (2026-10-16)

A read-only runtime audit found separate editor stores: the stopped legacy Node
container retains 418 documents in its writable layer, while the active Phoenix
bind mount contains 32 documents. All 376 documents from the October 14 backup
remain in the stopped source with identical SHA256 checksums. The files are
preserved; active-store restoration is not yet verified.

The 433 unique file hashes map to names derived from the current database,
including deleted records. This proves name correspondence, not active-record
status or a transactionally consistent backup. All 450 source/current copies
passed Yjs -> deployed Yex -> Yjs checks with equal state vectors and complete
updates. The active store is readable and writable by the deployed nonroot user.

Seventeen shared filenames have independent CRDT histories. Sixteen contain
label-selection text and one contains a card description. A mechanically valid
CRDT union is not an accepted recovery strategy: it can duplicate independently
seeded content or resurrect stale selections. Preserve current documents and
both original snapshots; compare retained drafts with current domain values
before activating old state. The audit-only recovery plan separates 401 missing
files and 17 conflicts and explicitly does not authorize or claim restoration.
A matching quiesced database/editor snapshot, resolved-content restore, rollback
proof, and authenticated browser acceptance remain required. The independent
read-only copies and checks are private verification artifacts, not product data
or repository fixtures.

The shared card-description conflict was checked against the current authenticated
card read. Phoenix text exactly matches the saved domain value; legacy text does
not. A mechanical merge increases visible text from 162 to 269 characters and
includes both versions. Keep the current document for this conflict. This result
does not decide the other missing files or prove that old drafts were all saved.

A subsequent bounded read retrieved the 64 saved card-description, comment, and
wiki values. Comparing through the current source's actual BaseEditorKit and
MarkdownKit found 45 of 59 legacy content documents equivalent to the saved
values. Fourteen differ, including the already-resolved current-store conflict;
13 missing-from-current legacy content documents still require semantic review.
Five of six current content documents compare equal. The sixth differs only by
a zero-width-space empty paragraph in serialized Markdown, not business text.
These comparisons do not authorize activation, replace a consistent snapshot,
or decide non-content selection/configuration state. No live data was changed.
