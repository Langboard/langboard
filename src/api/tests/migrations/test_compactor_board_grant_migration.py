"""Explicit grants start empty and cannot be discarded by a populated downgrade."""

import runpy
from io import StringIO
from pathlib import Path
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from langboard_shared.domain.models.CompactorBoardGrant import CompactorBoardGrant
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError


REVISION = Path(__file__).parents[2] / "langboard/migrations/versions/20261021130000-a3e607cf912b.py"


def test_sqlite_grant_migration_matches_model_and_preserves_records():
    revision = runpy.run_path(str(REVISION))
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as db:
            with Operations.context(MigrationContext.configure(db)):
                revision["upgrade"]()
            assert {column["name"] for column in inspect(db).get_columns("compactor_board_grant")} == set(
                CompactorBoardGrant.__table__.columns.keys())
            assert db.execute(text("SELECT COUNT(*) FROM compactor_board_grant")).scalar_one() == 0
            insert = "INSERT INTO compactor_board_grant (id,created_at,updated_at,project_id,internal_bot_id,granted_by_user_id,can_read,can_compact,can_archive) VALUES (:id,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,1,2,3,:read,:compact,0)"
            with pytest.raises(IntegrityError), db.begin_nested():
                db.execute(text(insert), {"id": 1, "read": False, "compact": True})
            db.execute(text(insert), {"id": 1, "read": True, "compact": True})
            with pytest.raises(IntegrityError), db.begin_nested():
                db.execute(text(insert), {"id": 2, "read": True, "compact": True})
            with Operations.context(MigrationContext.configure(db)), pytest.raises(RuntimeError, match="authority"):
                revision["downgrade"]()
            assert db.execute(text("SELECT COUNT(*) FROM compactor_board_grant")).scalar_one() == 1
    finally:
        engine.dispose()


def test_postgres_ddl_requires_explicit_read_and_unique_profile_board():
    output = StringIO()
    revision = runpy.run_path(str(REVISION))
    context = MigrationContext.configure(dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output})
    with Operations.context(context):
        revision["upgrade"]()
    assert "CONSTRAINT compactor_grant_requires_read CHECK" in output.getvalue()
    assert "CREATE UNIQUE INDEX ix_compactor_board_grant_identity" in output.getvalue()
