# Work Plan PostgreSQL lock diagnostics

The optional diagnostic measures the existing native Work Plan preview owner.
It does not remove production locks or substitute SQLite timing for PostgreSQL
behavior. Its current source is a prepared measurement path; skipped collection
or local source checks are not performance acceptance.

The matrix uses 5, 20 and 50 workers, small and large footprints, and same-board,
shared-relationship-type and distinct-type layouts. Connections are prewarmed
and the diagnostic pool is explicitly sized to workers with zero overflow. A
separate observer connection samples PostgreSQL lock waits. Each run creates a
unique test schema and removes only that schema through the existing fixture.
Use an explicitly disposable database, never an operational connection.

```sh
PROJECT_NAME=langboard \
PYTHONPATH=src/api:src/shared/py:. \
LANGBOARD_WORK_PLAN_LOCK_BENCHMARK=1 \
LANGBOARD_WORK_PLAN_LOCK_RESULT_DIR=/result/artifacts \
uv run --no-sync pytest -q \
  --junitxml=/result/artifacts/work-plan-lock-cost.xml \
  src/api/tests/card_workspace/test_work_plan_lock_cost_postgres.py
```

Supply `LANGBOARD_FILE_TEST_DATABASE_URL` using the approved disposable fixture
environment. Do not place a connection secret in documentation or reports.
Opt-in without the URL fails instead of producing a passing diagnostic.

## Central integration

The `work-plan-diagnostics` source profile uses the existing Docker validation
dependency layer and an `integration-work-plan-lock-cost` target. It requests
the native PostgreSQL sidecar, maps `postgres.sqlalchemy-url` to the fixture
environment variable and has a 1200-second integration limit. It has no production
publication purpose. The `work-plan-lock-cost` test command does not run during
normal API validation.

An additional validation profile requires the central repository/profile
allowlist and an admitted source revision. Source configuration alone does not
grant execution. This document records no successful central diagnostic run.

## Read the report accurately

Reports retain failed attempts and p50/p95/p99 over observed attempts. Pool
acquisition includes bookkeeping and is not pure pool wait. Locking SQL time
includes database execution and is not pure lock wait. PostgreSQL lock samples
are estimates with recorded actual sampling intervals. Transaction time stops
at DBAPI commit/rollback completion; post-commit callbacks are excluded.

The competing write workload is explicitly synthetic SQL contention, not native
edit API latency. Low sample counts limit p99 interpretation. The diagnostic
checks stable preview revisions, stale apply rejection, private-card denial and
absence of writes from preview/rejected apply. Successful apply/replay atomicity
still requires its existing native regressions. A lock reduction needs actual
before/after PostgreSQL evidence and preserved authorization, revision and
transaction semantics.
