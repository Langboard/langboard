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
    engine = create_engine("sqlite://")
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE card(id BIGINT PRIMARY KEY, title TEXT)"))
            connection.execute(text("INSERT INTO card VALUES(1,'Original')"))
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
            assert connection.execute(text("SELECT title FROM card WHERE id=1")).scalar_one() == "Original"
        now = SafeDateTime.now()
        with DbSession.atomic() as db:
            row = WorkflowEffectRun(
                stage_id=1, stage_key="closed", stage_revision="a" * 64, project_id=1, card_id=1,
                actor_id=2, actor_type="user", source="entry", effects=["stop_running_timers"],
                started_at=now, finished_at=now, duration_ms=0, result={"completed": 0, "stopped": 2},
            )
            db.insert(row)
        with engine.begin() as connection:
            # Evidence has no cascading ownership: even deleting its source leaves the receipt.
            connection.execute(text("DELETE FROM card WHERE id=1"))
            with Operations.context(MigrationContext.configure(connection)), pytest.raises(RuntimeError, match="Cannot discard"):
                migration.downgrade()
        with DbSession.use(readonly=False) as db:
            saved_rows = db.exec(SqlBuilder.select.table(WorkflowEffectRun)).all()
            assert len(saved_rows) == 1
            saved = saved_rows[0]
            assert saved.status == "succeeded" and saved.result == {"completed": 0, "stopped": 2}
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM workflow_effect_run"))
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
            assert "workflow_effect_run" not in inspect(connection).get_table_names()
    finally:
        engine.dispose()
