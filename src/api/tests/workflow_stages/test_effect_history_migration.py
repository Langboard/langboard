"""Execution evidence is additive, queryable through its ORM, and cannot be discarded."""

import importlib.util
from pathlib import Path
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import WorkflowEffectRun
from sqlalchemy import create_engine, inspect, text


def test_history_migration_preserves_work_and_refuses_evidence_loss(monkeypatch):
    path = Path(__file__).parents[2] / "langboard/migrations/versions/20261021040000-e7b942cd815f.py"
    spec = importlib.util.spec_from_file_location("workflow_effect_history_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.down_revision == "d6e821bc704a"
    followup_path = path.with_name("20261021050000-f9c053de926a.py")
    followup_spec = importlib.util.spec_from_file_location("workflow_profile_history_migration", followup_path)
    followup = importlib.util.module_from_spec(followup_spec)
    followup_spec.loader.exec_module(followup)
    assert followup.down_revision == migration.revision

    engine = create_engine("sqlite://")
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE card(id BIGINT PRIMARY KEY, title TEXT)"))
            connection.execute(text("INSERT INTO card VALUES(1,'Original')"))
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                followup.upgrade()
            assert connection.execute(text("SELECT title FROM card WHERE id=1")).scalar_one() == "Original"
        now = SafeDateTime.now()
        with DbSession.atomic() as db:
            row = WorkflowEffectRun(
                stage_id=1, stage_key="closed", stage_revision="a" * 64, project_id=1, card_id=1,
                actor_id=2, actor_type="user", source="entry", effects=["stop_running_timers"],
                started_at=now, finished_at=now, duration_ms=0, result={"completed": 0, "stopped": 2},
                execution_key="compaction:fixture:1", profile_id=3, profile_name="컴팩터", error_code=None,
            )
            db.insert(row)
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)), pytest.raises(RuntimeError, match="Cannot discard"):
                followup.downgrade()
            # Evidence has no cascading ownership: even deleting its source leaves the receipt.
            connection.execute(text("DELETE FROM card WHERE id=1"))
            with Operations.context(MigrationContext.configure(connection)), pytest.raises(RuntimeError, match="Cannot discard"):
                migration.downgrade()
        with DbSession.use(readonly=False) as db:
            saved_rows = db.exec(SqlBuilder.select.table(WorkflowEffectRun)).all()
            assert len(saved_rows) == 1
            saved = saved_rows[0]
            assert saved.status == "succeeded" and saved.result == {"completed": 0, "stopped": 2}
            assert (saved.profile_id, saved.profile_name, saved.execution_key) == (3, "컴팩터", "compaction:fixture:1")
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM workflow_effect_run"))
            with Operations.context(MigrationContext.configure(connection)):
                followup.downgrade()
                migration.downgrade()
            assert "workflow_effect_run" not in inspect(connection).get_table_names()
    finally:
        engine.dispose()


def test_profile_history_upgrade_preserves_legacy_receipts_and_unique_attempts():
    import sqlalchemy as sa
    from sqlalchemy.exc import IntegrityError

    root = Path(__file__).parents[2] / "langboard/migrations/versions"
    migrations = []
    for filename in ("20261021040000-e7b942cd815f.py", "20261021050000-f9c053de926a.py"):
        spec = importlib.util.spec_from_file_location(filename, root / filename)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        migrations.append(module)
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            # Bind all operations to this exact transaction.
            operations = Operations(MigrationContext.configure(connection))
            for migration in migrations:
                migration.op = operations
            migrations[0].upgrade()
            table = sa.Table("workflow_effect_run", sa.MetaData(), autoload_with=connection)
            now = SafeDateTime.now()
            values = dict(id=1, created_at=now, updated_at=now, stage_id=1, stage_key="active", stage_revision="a" * 64,
                          project_id=10, card_id=20, actor_id=2, actor_type="user", source="entry", status="succeeded",
                          effects=[], started_at=now, finished_at=now, duration_ms=0, result={"old": 1})
            connection.execute(table.insert().values(**values))
            before = dict(connection.execute(table.select()).mappings().one())
            migrations[1].upgrade()
            current = sa.Table("workflow_effect_run", sa.MetaData(), autoload_with=connection)
            after = dict(connection.execute(current.select()).mappings().one())
            assert {key: after[key] for key in before} == before
            assert all(after[key] is None for key in ("execution_key", "profile_id", "profile_name", "error_code", "lease_expires_at"))
            connection.execute(current.update().values(execution_key="compaction:one:1", profile_id=3, profile_name="컴팩터"))
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(current.insert().values(**{**values, "id": 2, "execution_key": "compaction:one:1"}))
            with pytest.raises(RuntimeError, match="Cannot discard"):
                migrations[1].downgrade()
            assert connection.execute(sa.select(current.c.profile_name)).scalar_one() == "컴팩터"
    finally:
        engine.dispose()
