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
