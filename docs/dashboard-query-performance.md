# Dashboard related-activity projection

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
