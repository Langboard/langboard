# Dashboard related-activity projection

## Timer endpoint projection, 2026-10-17

Board active-worker avatars now reuse this repository through the existing
repository factory. Their former `max(id)` query disagreed with My Work's
`(created_at, id)` ordering when an older event was inserted with a newer ID.
A real-database regression failed on both SQLite and PostgreSQL before the
change. Both now retain the newest timestamp, omit running anchors for paused
items and missing history, and execute two statements (workers plus timer
endpoints); an empty worker result executes only one. Existing archived-card
exclusion is unchanged. The combined timer and archive suite passed 16 cases.
This source correction has not yet been verified on deployed canary.

The shared timer repository now reads first/last events in one statement. A
partitioned row number ordered by `(created_at, id)` and a partition count identify
the two endpoints. Only those rows are hydrated; a single event supplies both
endpoints. Empty input executes no SQL. This replaces two endpoint-ID queries and
a third model query without changing caller payloads, adding a cache, or loading
the full history in Python. Both endpoints use the same statement snapshot.

Before this change, a bounded read-only probe of the deployed API found four
checkitems on an existing readable card: three without timer history and one
stopped event. The deployed repository used three timer SELECTs (29.982 ms);
four legacy last-event reads used four SELECTs (18.033 ms). Card projection used
four SELECTs including the item query (24.985 ms), and latest event IDs/timer
fields matched the legacy reads. These are individual observations under the
existing runtime load, not comparable latency percentiles or proof of an overall
HTTP improvement. The native MCP bundle independently returned the same four
stopped checkitems. No timer or customer content was changed.

On disposable PostgreSQL with ten timer events per item, seven warm alternating
executions per implementation produced these median repository durations:

| Requested items | Previous three-query batch | Single-query batch |
| --- | --- | --- |
| 4 | 2.848 ms | 1.218 ms |
| 100 | 6.771 ms | 4.232 ms |
| 1,000 | 47.828 ms | 46.722 ms |

Endpoint IDs matched at every size. The large-batch difference is small; these
local fixture measurements do not establish canary or full-request latency.
The temporary schema was removed after measurement. Regression coverage executes
the three 1,000-item service projections on SQLite and PostgreSQL, plus real
repository checks for equal-timestamp ties, timestamp priority over ID, empty and
missing histories, single-event identity, exclusion of unrequested items, and
hydration of endpoints only. Combined timer/workflow tests passed 21 cases with
no skips when the workflow fixture also used disposable PostgreSQL.

The deployed API still has the previous three-query implementation. Deployment,
authenticated REST/My Work acceptance with an active timer, and a corresponding
runtime before/after measurement remain required before archiving the timer card.

The project list derives related-project recency from the current user's
assignments to active, non-deleted cards. Each card's latest activity is read
through the existing `(card_id, created_at)` activity index, then grouped by
project. The projection remains one SQL statement. It does not load editor
content or cache authorization or results.

A card with no activity still makes its project related to the user; its activity
time remains null. Projects without related cards retain a null related timestamp
through the existing outer join. Archived and deleted cards do not establish
relatedness or advance related activity. Project ordering, current membership and
role checks, starred state, and the separate overall project activity remain in
the existing project-list path.

## Regression coverage

`src/api/tests/dashboard/test_related_activity_projection.py` executes the
repository expressions on SQLite and an isolated PostgreSQL schema. It checks
multiple card histories, different users, missing activity, missing assignments,
archived cards, deleted cards, null outer-join results, and the single-statement
boundary. The existing priority tests retain project ordering and view-count
coverage. Both files are included in the API and PostgreSQL validation profiles.

Set `LANGBOARD_FILE_TEST_DATABASE_URL` only to a disposable test database and run:

```sh
PYTHONPATH=src/api:src/shared/py:. PROJECT_NAME=langboard \
  python -m pytest -q src/api/tests/dashboard
```

## Local measurement, 2026-10-17

On disposable PostgreSQL 17.6 with 1,000 assigned cards, 20 projects, and 100,000
activity events, ten warm executions of the original aggregate join and the
current repository-generated SQL returned identical timestamps. Median client
query duration was 12.40 ms versus 2.31 ms. EXPLAIN ANALYZE showed the original
activity sequential scan reading 100,000 rows; the replacement used an index-only
scan returning one row per card, with 1,000 loops. Existing index columns were
used; no new index, migration, or dependency was introduced.

Sparse-history checks also returned identical results: 10 cards/10 events measured
0.49 ms versus 0.41 ms, while 1,000 cards/1,000 events measured 0.82 ms versus
1.20 ms. The indexed approach trades a small per-card probe cost for avoiding
scans of accumulated history; it is not faster for every data distribution.

These are synthetic local query measurements, not dashboard latency, cold-cache
results, percentile acceptance, or production deployment proof. Costs still grow
with the number of assigned cards. Actual canary measurements on small and large
boards remain necessary, as do the broader loading and server-resource changes.


## Project detail reads and default bots

Ordinary board/MCP detail serialization no longer repairs default bot assignments.
It retains member classification, invitations, role actions, and labels, without
querying the assigned-bot or global-default inventories. New project creation
still installs its defaults. Bot settings still repair missing defaults.

When a specific bot is actually requested, an existing assignment returns
immediately. If absent, the existing default-repair routine runs and the requested
assignment is looked up again. This preserves older projects that predate a new
default and leaves custom bot choices and prompts intact. If no global default
exists, the bot remains unavailable. This uses the existing service boundary and
adds no background reconciler, cache, or service.

The bootstrap regression and board-chat availability tests are part of the API
validation profile. Focused chat, upload, editor, settings, membership and bootstrap
coverage passed 120 tests after the change; three new expectations failed on the
original implementation. This is source proof only. The board HTTP route's
separate last-view update still writes visit recency/frequency and is not removed
by this change. Actual canary response timing and bot operation need verification
after release.

## Monotonic board read cursors without fabricated visits

The card snapshot reader previously loaded a membership cursor and later wrote
the snapshot's maximum visible change sequence without a database condition. An
older snapshot could therefore overwrite a newer cursor. Updating that model
also invoked the `last_viewed_at` column's automatic update and changed membership
`updated_at`, even though this operation was only a read-cursor advance.

The existing membership repository now performs one conditional primary update,
scoped to user and project, with `board_seen_seq < observed_seq`. The cursor only
advances. It explicitly retains `last_viewed_at` and `updated_at` to suppress their
automatic updates. An equal/older request affects zero rows, including when a
stale replica supplied the membership snapshot. Starred state, visit frequency and
the per-card unread baseline are preserved. A deleted membership is never recreated.
No locking protocol, migration, cache or request contract is added.

The six new SQLite/PostgreSQL stale-snapshot cases failed on the original source:
a newer cursor regressed from 100 to 50, or cursor writes manufactured a visit.
After the fix, the database reports affected rows of one for 0→50 and zero for
50→50 or 100→50. A real PostgreSQL parallel 50/100 update finishes at 100. Native
card unread flags and actual project-visit recency/count behavior are retained.
The focused cursor, dashboard activity, card read visibility and shared board
progress regressions passed 22 tests in 4.08 seconds. Ruff, shell syntax and diff
checks pass. The cursor suite is included in the existing API and disposable
PostgreSQL profiles.

Records: `.verification/board-seen-before.log`,
`.verification/board-seen-after.log`, `.verification/board-seen-final.log` and
`.verification/board-seen-concurrency-final.log`. An intermediate fixture used
preassigned foreign-row IDs that the native insert replaces; the fixture now
reads its generated IDs, and that failed run is preserved. These are disposable
database checks, not operating data repair or a canary latency measurement.

The board GET still intentionally advances its existing notification cursor, and
project bootstrap still records a real visit. This change does not claim all GET
routes are side-effect free or remove the separate read/write separation task.


## Independent detail metadata requests

Individual metadata requests use a query key containing the resource kind, board
UID and resource UID. Simultaneous card/wiki/detail views therefore cannot share
an unrelated in-flight request. Requests for the same resource still deduplicate.
The query AbortSignal is forwarded to Axios so an abandoned or cancelled query
cannot continue into a stale model write after an aborted transport.

`useGetMetadata.test.mjs` executes the production hook with the real TanStack
QueryClient and an isolated transport/model boundary. It covers different cards,
card versus wiki, different boards, same-resource deduplication, and cancellation
that leaves another card's request intact. The metadata and existing settings
loading regressions are included in the UI validation profile. This verifies the
query lifecycle; it is not a claim of authenticated canary multi-window acceptance.

## Cancel abandoned primary board requests

Project bootstrap, card snapshot and project settings queries now pass TanStack
Query's native `AbortSignal` to Axios, matching the existing metadata path. When
navigation removes the final observer, the pending HTTP transport is cancelled.
A late adapter response cannot continue into model hydration or launch optional
dock/metadata enrichment. Native query keys, deduplication, authorization ordering,
explicit retry and the already nonblocking enrichment behavior are retained.
No manual request registry, navigation timer or new cancellation abstraction is
introduced. Browser cancellation does not prove the API has cancelled SQL work
that it already started.

The real React/TanStack/Axios browser fixture holds either the previous project's
bootstrap or card response, switches boards, observes transport abortion and
allows the late response to finish. It verifies the current board loads and no
previous-board enrichment starts. Both cases failed before the change. The six
project-load browser checks and four existing card-enrichment checks pass. The
settings test uses a real QueryClient and Axios adapter to verify cancellation
prevents late model writes and dock requests; it failed on the unchanged source.
Settings plus metadata Node checks pass eight tests. TypeScript, scoped ESLint
and diff checks pass.

One intermediate browser assertion incorrectly expected `Project.fromOne` to
populate the shared model collection. It was corrected to observe the actual
loaded project, transport abortion and request history; the failed run is retained.
Raw records are `.verification/board-cancel-before.log`,
`.verification/board-settings-cancel-before.log`,
`.verification/board-cancel-after.log`, `.verification/board-cancel-final.log`,
`.verification/board-cancel-cards.log` and
`.verification/board-cancel-node-final.log`. These are local fixture regressions,
not authenticated canary navigation or production latency measurements.


## Board comment aggregation follows the hot archive window

The board-list comment aggregate now uses the same archive cutoff as the card
projection, in addition to the existing project, deletion and actor-visibility
conditions. Cold archived cards cannot contribute to this aggregate. Exact-cutoff
cards remain included; the `(Card, comment_count)` contract and detail/archive
reads are unchanged. No cache, index, migration or background reconciler is added.

A bounded read-only PostgreSQL observation on 2026-10-17 found 342 non-deleted
cards (294 within the three-day window), and 2,337 comments (2,296 within that
window) in one authorized board. The standalone aggregate measured 6.845 ms for
the original and 2.895/3.071 ms for the narrowed query after warming. The initial
original-query run took 143.232 ms and is not comparable because cache state
changed. These diagnostic queries omitted actor predicates to isolate archive
filtering; production retains those predicates. This is neither an authenticated
API benchmark nor evidence of a deployed latency improvement. The excluded
41 comments cannot alone explain second-scale board response times.

The focused archive/visibility regressions verify active and exact-boundary
comment counts, cold-card exclusion, deferred AI content, and current-actor
visibility. Actual canary acceptance remains required after release.

## Skip signal policy work for unlinked card batches

The work-state projection previously read and parsed the entire approved app
registry and built provider-specific authority joins even when the requested cards
had no enabled signal bindings. It now first probes the indexed binding `card_id`
with the caller's card IDs, `is_enabled=true`, and `LIMIT 1` on the primary database.
An empty result returns no evidence without reading the registry or joining app,
connection, user, resource and policy tables. There is no cache or schema change.

A positive probe grants no authority. The original provider/resource predicates,
current connection/organization/credential checks and signal matching still run.
Revocation is never inferred from a cached prior result. Batches with signal links
pay one additional small query (five total in the existing 1-card and 31-card
fixture); unlinked batches use one query instead of two. A concurrently added
binding may first appear on the next request, as with other changes after a read.

A disposable local PostgreSQL fixture with 300 cards and no signal links measured
30 warm trials before and after: median 5.890 ms to 1.579 ms, with first calls
20.818 ms and 2.330 ms respectively. SQLite fixture medians were 2.860 ms and
0.333 ms. These are standalone projection measurements with a small registry,
not production HTTP latency, p95, or an explanation of the observed 5–6-second
board request. Raw measurements and the disposable fixture are retained under
`.verification/signal-unbound-benchmark-pg-{before,after}.log` and
`.verification/test_signal_unbound_benchmark.py` in the working tree.

The regression verifies absent, disabled and unrelated-card links skip registry
reads and authority joins. Existing linked-card tests retain current revocation,
batch query bounds, occurrence ordering, conflict and provider mapping coverage.
Authenticated canary phase measurements still require release.

The expanded run also exposed two stale fixtures on the unchanged implementation:
resource discovery omitted the now-required explicit selection, and the generic
adapter test patched a removed module global. The fixtures now select their test
resources and register a validated SDK declaration in SQL. Product default-deny
behavior is unchanged; the independent adapter no longer relies on a fake global
registry. The board signal projection suite runs in the existing API and disposable
PostgreSQL validation profiles.

The final combined SQLite/PostgreSQL run passed 170 tests in 99.50 seconds across
card projection, generic declaration, independent provider HTTP contracts,
GlitchTip and Dokploy evidence. Fifteen existing short JWT fixture-key warnings
remain recorded; no production key was changed. The original 26 fixture failures,
unchanged-source reproduction, and final passing run remain in
`.verification/signal-unbound-regression.log`,
`.verification/signal-existing-fixture-before.log` and
`.verification/signal-unbound-regression-final.log` respectively. These tests do
not establish deployment or whole-board HTTP performance acceptance.


## Card author display fields

The board author query retains the existing User/Bot ORM return contract, author
precedence, soft-delete filters and recent-archive cutoff. SQLAlchemy `load_only`
now fetches only IDs, display names, avatars and deletion state, with `raiseload`
to reject accidental deferred account reads. It no longer fetches account email,
password fields, bot API credentials or bot configuration for author enrichment.
The display helper reads the avatar path directly instead of serializing an entire
account or bot for every card. Missing avatars and deleted-user avatar suppression
retain their existing behavior. No schema, cache or dependency is added.

Two SQLite/PostgreSQL regressions fail on the previous source because the SQL
selects private account fields. The updated tests verify exact display payloads,
one SELECT, no full account serialization and no lazy loads after session close.
Existing repository scope and shared board-progress tests remain in the focused
suite. An initial fixture omitted Bot/Card tables; a combined run also exposed an
unnecessary DomainService import-order dependency in the new test. The fixture now
creates those tables, and the stateless projection is exercised directly without
constructing unrelated services. Failed receipts are retained.

On disposable PostgreSQL, 300 cards split between one human and one bot with a
30,800-byte synthetic configuration produced identical display payloads before
and after. Thirty alternating warm samples per variant measured median query plus
author projection duration of 21.661 ms before and 3.515 ms after, with one SELECT
per run. The original methods were loaded from the preceding committed source;
no production data or credentials were used. This represents a configuration-heavy
local fixture, not a general speedup guarantee or complete board HTTP latency.
Current canary performance and authenticated UI acceptance remain unverified for
this change.


## Reuse checklist rows in the board snapshot

The board route used to query all authorized hot-window checklists while building
card completion state, then query the same user checklist rows again for its
response. The internal CardService entry point is now `get_board_snapshot` and
returns the assembled cards together with user checklist models from that same
read. The route serializes those rows in its existing `board_checklists` timing
stage. The HTTP response fields and five Server-Timing stage names are unchanged.
The standalone ChecklistService method remains for other callers. No alternate
cache, callback, response flag or compatibility wrapper is added.

The original all-checklist repository query still applies the same project,
current resolved visibility, soft-delete and archive-cutoff filters. Hidden system
checklists still determine card completion, but never enter the returned checklist
list. The initial route authorization and the service's existing authorization
resolution both remain; this change does not cache or bypass either check.

SQLite and disposable PostgreSQL route regressions show two checklist SELECTs on
the original source and one after the change. They compare the serialized user
checklists with the existing standalone reader and verify shared cards, the
caller's own private card, the archive cutoff boundary, internal/other-private
exclusion, foreign boards, deleted cards/lists and hidden completion state. The
focused snapshot, archive, cursor and shared progress suite passed 16 tests in
6.43 seconds. Initial fixture failures (missing bot table/private-owner constraint
and comparison before JSON normalization) were corrected with failed receipts
retained. Both API validation profiles include the snapshot regression; the
PostgreSQL profile also explicitly includes author display-field coverage.

This removes one redundant database read and model hydration per board snapshot.
It does not establish a transactionally consistent snapshot across every response
section, nor does it prove deployed latency or canary acceptance.


## Join workflow policies with their referenced columns

Work-state projection previously read full ProjectColumn rows, then loaded full
WorkflowStageDefinition rows in another query. It now uses one native SQLAlchemy
outer join and load_only/raiseload for the column's ID, project, stage and archive
flag and the policy's key, completion, queue and overdue values. This avoids a
second round trip and reading descriptions, translations, names and entry-effect
configuration merely to calculate state. Other workflow registry and guidance
readers retain their full models and existing descriptions.

The join retains deleted-column exclusion and does not exclude an inactive
registry definition that is already bound. Missing columns or policies remain
unknown; a foreign project's column supplies neither semantics nor archive state.
Archived cards/columns, completion/overdue suppression, verification, dependency,
app signal and timer projections retain their existing interpretation. No cache,
schema change, dependency or new repository facade is added.

Two SQLite/PostgreSQL regressions fail on the preceding source with two policy
queries. The replacement executes one narrow query and preserves active, review,
closed, inactive-bound, unknown, unclassified, missing, deleted, foreign and archive
cases. Existing registry-edit tests verify immediate reinterpretation without
entry-effect execution. Workflow, native MCP guidance, shared progress, snapshot
and registered app signal coverage passed 50 tests in 13.13 seconds. Two existing
PostgreSQL row-lock tests skipped because their separate effect-test database was
not selected; nine existing short test-key warnings remain. The expanded run
also exposed a stale guidance fixture returning UID strings where native models
were required; its resolver now returns those models and its unused preliminary
lookup patch was removed. An initial new fixture reused unsaved IDs and untracked
updates; unique IDs and reloaded rows corrected it. Failed receipts are retained.

Disposable PostgreSQL, 300 cards/3 columns, 30 alternating warm samples per variant:
query plus work-state projection median was 4.660 -> 3.425 ms with small metadata,
and 5.622 -> 3.253 ms with large synthetic descriptions/translations. Results were
identical and policy SELECTs fell from two to one in both cases. These are local
projection measurements, not complete board HTTP latency or deployed acceptance.
Both API validation profiles include the two-database column projection test.

## Activity refresh must not starve slow dashboard reads

Authenticated canary inspection on 2026-10-17 reproduced an initial board load
failure that recovered on retry, and repeated dashboard load failures. A captured
dashboard XHR ended with `net::ERR_ABORTED` after 2.462 seconds, while its preflight
and a concurrent notification read returned 200. This proves cancellation, not an
authentication failure or a complete attribution of all slow requests. The
deployed UI revision de3d840 contains the same activity subscription handler that
unconditionally canceled dashboard queries before each event-driven refresh.

The existing shared subscription lease now allows a running snapshot to finish
before invalidating it with `cancelRefetch: false`. Events during that refresh
coalesce into one subsequent refresh. A single running read and the existing
250 ms burst window bound requests; there is no periodic poll. Merely disabling
cancellation would reuse a snapshot started before the event and lose freshness,
so the follow-up is retained. Reconnect still waits for topic acknowledgement,
and released or replaced owners cannot trigger a follow-up. Authentication and
permission handling remain owned by the existing query/session lifecycle.

The official [QueryClient cancellation contract](https://github.com/TanStack/query/blob/main/packages/query-core/src/types.ts)
and installed TanStack Query source were checked. A real QueryClient/QueryObserver
test fails on the original implementation because activity aborts the running
request. Initial and cached slow reads, sustained bursts, final fresh data, no
polling, and owner replacement pass after the change. The expanded workload and
API error suite passes 17 tests; the UI validation profile includes the lease
regression. This is source verification, not a deployed fix or a canary latency
improvement claim. Column-count acceptance remains incomplete because dashboard
loading prevented the actual comparison.

Separate read-only infrastructure evidence at 07:09:14 UTC shows the two-CPU
canary LXC using 1.94282 CPU seconds during 1.00028 seconds, with CPU pressure
`some avg10=94.07`. A separate infrastructure operator sampled Kafka processes for
18.588 seconds: broker processes used 0.46 CPU seconds, while six observed
healthcheck JVMs used at least 14.02 CPU seconds. These samples support a concrete
probe-frequency investigation; they do not prove that every request failure is
caused by Kafka. Preserve protocol checks, broker topology, volumes and ISR when
reviewing the five-second steady-state probe interval and startup/detection
budgets. No operating broker was restarted or changed for these measurements.

## Kafka healthcheck scheduling

The shared Kafka Compose healthcheck retains the official broker API-version
command, three brokers, their existing data volumes and the healthy dependency
gates. Each check starts a separate JVM. Its steady-state interval is now 30
seconds instead of five; timeout remains ten seconds. Four consecutive failures
replace 24, retaining 120 seconds of inter-check delay before failure detection.
Including the maximum execution time of each failed check gives a nominal
160-second budget instead of 360 seconds after a completed healthy check. These
are scheduling budgets, not wall-clock guarantees under host CPU starvation.
A recovering broker may take up to the new 30-second interval plus probe duration
to report healthy; Docker health status alone does not restart a running process.

The existing 20-second initialization grace remains. Native `start_interval: 5s`
preserves frequent checks during that grace, then the normal interval applies.
The [Docker healthcheck contract](https://docs.docker.com/reference/dockerfile/#healthcheck)
requires Engine 25.0+ for this field. The existing Compose 2.24.4+ requirement
exceeds the field's Compose 2.20.2 minimum. Local Engine 29.8.0/Compose 5.5.1 and
the separately reported canary Engine 29.3.0/Compose 5.1.0 support it.

Actual full-stack Compose rendering covers bundled and external Redis selections,
all three inherited probes, startup and failure budgets, existing volume/node
identities and API/Phoenix health gates. Both new cases failed on the old
five-second schedule, then passed after the change. The combined external-service
selection suite passed 25 tests with two PowerShell-unavailable skips. No Kafka
image was downloaded or started for these configuration tests. Startup, recovery,
CPU reduction and request latency still need deployed runtime acceptance.

Changing a Compose healthcheck requires container recreation. Before rollout,
record current volume identity, KRaft cluster identity, partition leaders/replicas/
ISR, and active Phoenix consumer offsets/lag. Existing topic replication may be
one even with three brokers; sequential replacement can temporarily interrupt
those partitions. Do not call this a zero-downtime change. Retain existing volumes,
cluster ID, topic layout, offsets and application versions. Recreate one broker
at a time through the existing deployment process, verify recovery before the
next, and compare offset bounds/progression rather than requiring immutable
offset numbers on an active board. Never use volume removal, offset reset, topic
recreation or automatic replication changes to apply this scheduling adjustment.
After rollout, measure healthcheck and broker CPU separately, host pressure,
authenticated dashboard/board latency and actual delivery/reconnect behavior.
Rollback restores the previous Compose schedule with the same data identities.

## Current visibility resolution, 2026-10-18

Each visibility resolution now reads the current project, user and membership
existence in one primary statement using the existing SQLAlchemy boundary. The
owner path previously used two statements; assigned and unassigned non-owners
used three. Internal-access classification still uses its existing policy.

The board route and snapshot retain their separate current-access checks. An
attempt to reuse the earlier context failed the existing regression that revokes
access before the snapshot read; that change was discarded. No request-spanning
auth cache or caller-supplied visibility context was introduced.

Real SQLite/PostgreSQL query-count regressions cover owners, assigned members
and outsiders. Existing account-revocation and second-check denial tests remain
part of focused acceptance. The API and API-PostgreSQL validation profiles include
the policy/query-count module. Source query reduction is not proof of a canary
HTTP latency improvement; that requires the new image and runtime measurements.

## Read-route phase timing, 2026-10-18

Board details now expose `project_details`, `project_bots`, `project_visit` and
`project_serialize` durations through the existing `Server-Timing` header.
Dashboard projects expose `dashboard_projects`, `dashboard_columns` and
`dashboard_serialize`. The existing middleware retains these metrics and appends
the total `app` duration. Metrics contain fixed names and milliseconds only.
No user, project, SQL or payload content is added to the header.

Dashboard authorization and query order remain unchanged: columns receive only
the IDs returned by the current user's project query. The unused combined service
wrapper was removed. Human last-view writes and bot behavior remain unchanged;
the timing makes the visit write visible for later measured review.

The focused route, bootstrap, activity-order and middleware suite passed 43 tests,
including missing-board and database-failure behavior, authorized column scope,
payload preservation, timing-header composition and CORS exposure. These tests
do not establish production latency improvement.

Before deploying these changes, ten authenticated Chrome reloads on the current
canary returned HTTP 200 for the board card endpoint in every sample. CDP
`requestTime` to `loadingFinished` durations ranged from 1,561 to 8,216 ms;
the median was 4,919 ms and nearest-rank p95 was 8,216 ms. Encoded responses were
439,766–440,092 bytes. Existing server `app` durations ranged from 1,400.703 to
8,041.930 ms. This small sequential sample describes observed variability, not a
capacity benchmark or a before/after comparison. The final browser DOM contained
the board columns without a load error. The current canary exposes only the total
`app` metric; the new phases still require deployment and request-ID correlation.
The sanitized local receipt retains timestamps, request IDs, durations and bytes
under `.verification/canary-board-server-timing-20261018-followup.json`.

## Column reads do not create archive storage

`ProjectColumnRepository.get_all_by_project` now returns existing columns from
its single bounded read. It no longer repairs archive columns during dashboard,
board, template-export or other list requests. A limited result omitting an
existing archive previously triggered one extra lookup per requested project and
appended rows beyond the requested limit. A missing archive also triggered an
order lookup and INSERT per project.

The existing explicit write boundaries still create or reuse archive storage:
project creation, template application, card archiving and column deletion.
No migration, background scan, new cache or synthetic archive ID is introduced.
Existing archive rows remain in unlimited results, and their physical card counts
are unchanged. Older boards missing an archive acquire one when a write needs it.

Six new real SQLite regressions failed on the original implementation. For 25
boards missing archives, the old list request issued 76 statements including
INSERTs; the changed read issues one SELECT and no writes. Limited existing-
archive cases also remain one SELECT and honor the requested limit. Focused
read-side-effect, physical-count, unfinished-work, column-deletion and current-
visibility coverage passed 17 tests. These source and query-count results do not
prove deployed latency improvement; authenticated canary acceptance still needs
the updated image.

## Scope board enrichment reads before hydration

The board snapshot now passes its independently resolved current visibility
context to the existing assignment and label batch queries. Each applies the
shared card-visibility predicate and live-card filter before hydrating rows.
Previously these two reads loaded all hot assignments/labels from the board,
including hidden cards, and only visible cards were ultimately serialized.
This reduces unnecessary intermediate data; it does not establish a prior
hidden-card disclosure or explain all observed server latency.

Project and archive cutoff predicates are retained. The current owner can still
see their private-card enrichment; other private cards, external users' internal
cards, deleted cards, foreign boards and cold archive rows are excluded. Other
internal callers that omit the optional context retain their existing behavior.
No authority cache, new policy implementation or additional query is introduced.

Real SQLite coverage compares scoped and legacy reads for internal and external
actors. Focused enrichment, snapshot/checklist, archive, current visibility and
seen-cursor coverage passed 14 tests with five PostgreSQL-only skips. The new
regression is included in the API profile. PostgreSQL and authenticated canary
acceptance are not claimed by this run.

A separate local relative-time check measured 400 formatter calls in 3.144 ms
median across ten warm Node runs. That does not support a formatter cache as a
fix for the multi-second board request, so no cache was added. Raw samples are
retained under `src/ui/.verification/relative-format-before-20261018.json`.
