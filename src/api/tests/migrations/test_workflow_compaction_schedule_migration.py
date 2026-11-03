"""The durable clock is additive and its populated downgrade is non-destructive."""

import runpy
from io import StringIO
from pathlib import Path
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from langboard_shared.domain.models.WorkflowCompactionSchedule import WorkflowCompactionSchedule
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError


REVISION = Path(__file__).parents[2] / "langboard/migrations/versions/20261022020000-d64b2ac51e80.py"


def test_schedule_migration_retains_unique_clock_and_populated_downgrade():
    revision = runpy.run_path(str(REVISION))
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as db:
            with Operations.context(MigrationContext.configure(db)):
                revision["upgrade"]()
            assert {column["name"] for column in inspect(db).get_columns("workflow_compaction_schedule")} == set(
                WorkflowCompactionSchedule.__table__.columns.keys())
            insert = "INSERT INTO workflow_compaction_schedule (id,created_at,updated_at,card_id,profile_id,next_check_at) VALUES (:id,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,1,2,CURRENT_TIMESTAMP)"
            db.execute(text(insert), {"id": 1})
            with pytest.raises(IntegrityError), db.begin_nested():
                db.execute(text(insert), {"id": 2})
            with Operations.context(MigrationContext.configure(db)), pytest.raises(RuntimeError, match="schedule"):
                revision["downgrade"]()
            assert db.execute(text("SELECT COUNT(*) FROM workflow_compaction_schedule")).scalar_one() == 1
    finally:
        engine.dispose()


def test_postgres_schedule_ddl_preserves_existing_tables():
    output = StringIO()
    revision = runpy.run_path(str(REVISION))
    context = MigrationContext.configure(dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output})
    with Operations.context(context):
        revision["upgrade"]()
    assert revision["down_revision"] == "b4a719de028c"
    assert "CREATE UNIQUE INDEX ix_workflow_compaction_schedule_identity" in output.getvalue()
    assert "DROP TABLE" not in output.getvalue()
