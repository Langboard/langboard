# Snowflake allocation and storage verification

The generator keeps timestamp, node and sequence bits separate, advances a
logical millisecond on sequence exhaustion, and resets inherited state/locks after
fork. Node hashing includes process identity to reduce collisions. Hashing does
not guarantee a globally unique worker allocation: the database primary key and
the existing bounded insert retry remain authoritative across processes/replicas.

On an ID primary-key collision, the allocator advances past the occupied sequence
range. Other unique-constraint errors propagate. Exhausted allocation leaves the
existing row unchanged and resets the unsaved model ID; it must not silently
overwrite a row or commit part of a batch. Existing bigint IDs and 11-character
short-code round trips are retained.

## Reproducible integration gate

Use a dedicated PostgreSQL test database through `LANGBOARD_TEST_DATABASE_URL`:

```sh
PROJECT_NAME=langboard PYTHONPATH=src/api:src/shared/py:. \
  python -m pytest -q -s \
  src/api/tests/core/test_snowflake_storage_safety.py \
  src/api/tests/project_labels/test_builtin_global_labels.py
```

The existing tests create and remove isolated schemas. Never point a destructive
test gate at an operational database. The existing card-workspace workflow already
selects both files; this verification adds no job, runner or dependency.

The 2026-10-19 run on source `278318c9a` passed all 27 tests with no skips across
SQLite and PostgreSQL 16. Its independent spawn processes intentionally use the
same node and clock: four workers insert 8 or 64 rows each into the same database,
then assert all 32 or 256 persisted IDs are distinct. Other checks cover allocator
threads/overflow/rollback, a real fork with the parent lock held, constraint error
propagation, allocation exhaustion and occupied sequence ranges. Actual Alembic
seed writes verify transaction rollback, retry, idempotency and preservation of
existing/customized global labels in both databases.

The local hot-path sample generated 100,000 distinct IDs in 0.1901 seconds
(525,981 IDs/s). This is one allocator-only sample, not concurrent database write
throughput or an API latency comparison. The temporary database was limited to
1 CPU/256 MiB; the test schemas were absent after cleanup and the disposable
container was removed. A pytest `record_property`/JUnit xunit2 compatibility
warning was emitted; it did not skip any test or invalidate the stdout timing.

## Remaining acceptance

This is current-source integration evidence. It does not establish deployment of
this allocator to API, Graph or Celery replicas, successful required CI, a canary
new-card round trip, or original-author acceptance. Preserve those separate gates
and do not archive the parent work item on this result alone.
