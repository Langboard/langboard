"""Replay actual DDL; retained occurrence evidence blocks destructive downgrade."""

import runpy
from io import StringIO
from pathlib import Path
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from langboard_shared.domain.models.BotScheduleRun import BotScheduleRun
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError


REVISION = Path(__file__).parents[2] / "langboard/migrations/versions/20261021120000-64c8c0a4baf1.py"


def test_actual_sqlite_migration_matches_model_and_preserves_retained_evidence():
    revision = runpy.run_path(str(REVISION))
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as db:
            with Operations.context(MigrationContext.configure(db)):
                revision["upgrade"]()
            assert {column["name"] for column in inspect(db).get_columns("bot_schedule_run")} == set(
                BotScheduleRun.__table__.columns.keys()
            )
            assert {index["name"] for index in inspect(db).get_indexes("bot_schedule_run")} == {
                index.name for index in BotScheduleRun.__table__.indexes
            }
            db.execute(
                text(
                    "INSERT INTO bot_schedule_run (id,created_at,updated_at,schedule_id,scope_table,scope_id,occurrence_key,state,attempt,max_attempts,next_attempt_at,expires_at) VALUES (1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,2,'card_bot_schedule',3,'occurrence','pending',0,6,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
                )
            )
            for invalid in ("state='unknown'", "attempt=7", "state='dispatching'"):
                with pytest.raises(IntegrityError), db.begin_nested():
                    db.execute(text("UPDATE bot_schedule_run SET " + invalid))
            with Operations.context(MigrationContext.configure(db)), pytest.raises(RuntimeError, match="evidence"):
                revision["downgrade"]()
            assert db.execute(text("SELECT state,attempt FROM bot_schedule_run")).one() == ("pending", 0)
            db.execute(text("DELETE FROM bot_schedule_run"))
            with Operations.context(MigrationContext.configure(db)):
                revision["downgrade"]()
            assert "bot_schedule_run" not in inspect(db).get_table_names()
    finally:
        engine.dispose()


def test_postgres_offline_ddl_keeps_unique_identity_and_lease_checks():
    output = StringIO()
    revision = runpy.run_path(str(REVISION))
    context = MigrationContext.configure(dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output})
    with Operations.context(context):
        revision["upgrade"]()
    ddl = output.getvalue()
    assert "CREATE UNIQUE INDEX ix_bot_schedule_run_occurrence" in ddl
    assert "CONSTRAINT bot_schedule_run_attempts CHECK" in ddl
    assert "CONSTRAINT bot_schedule_run_lease CHECK" in ddl
