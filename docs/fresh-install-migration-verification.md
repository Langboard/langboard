# Fresh installation migration verification

The reviewed development line now upgrades empty SQLite and PostgreSQL databases to
`dc60b3745ae2` with 120 tables. Verification used the actual Alembic CLI; no
`create_all`, revision stamping, production database, or migration bypass was used.

## Corrections

- Preserve PostgreSQL enum conversion and content-change sequences only on PostgreSQL.
- Compile timestamp defaults with SQLAlchemy instead of PostgreSQL-only text.
- Allow the two historical activity-index migrations to converge on the same index.
- Keep the canary merge revision ID, but retain only its three terminal parents.
  Removed parents are already ancestors of those terminals; their migrations remain reachable.
- Inspect checklist columns and indexes through SQLAlchemy rather than PostgreSQL catalogs.
- Use Alembic batch operations for organization and global-label constraints.
- Keep PostgreSQL JSONB while providing SQLite JSON and portable JSON defaults.

No new runtime dependency, schema revision ID, or application abstraction was introduced.
These historical corrections repair fresh installation and previously unapplied upgrades.
They do not modify already stamped databases automatically or prove downgrade safety
for every historical revision.

## Evidence

- Empty SQLite: full `alembic upgrade head`, revision `dc60b3745ae2`, 120 tables.
- Empty PostgreSQL 18.6: same full upgrade, revision, and table count; disposable
  local server stopped and temporary database removed afterward.
- Four targeted tests passed: merge-graph ancestry, full SQLite installation and
  repeated `upgrade head` preserving all seed rows, and enum-storage compatibility.
- Ruff and `git diff --check` passed.

Reproduce the tracked regression tests from the repository root:

```sh
PROJECT_NAME=langboard PROJECT_SHORT_NAME=lb python -m pytest -q \
  src/api/tests/card_workspace/test_fresh_install_migrations.py \
  src/api/tests/card_workspace/test_checkitem_status_migration.py
```

PostgreSQL verification requires a disposable PostgreSQL instance and explicit
`MAIN_DATABASE_URL` and `READONLY_DATABASE_URL`. Never target production for a
fresh-install check.

This evidence covers the reviewed source snapshot. Runtime deployment, authenticated
UI behavior, the complete CARVE gate, and future stack changes require separate evidence.
