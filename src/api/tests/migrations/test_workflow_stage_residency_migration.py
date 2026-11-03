"""Existing card data remains intact and its unknown entry time stays unknown."""

import os
import runpy
from pathlib import Path
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text


@pytest.mark.parametrize("database_url", ["sqlite://", os.getenv("LANGBOARD_FILE_TEST_DATABASE_URL")],
                         ids=["sqlite", "postgresql"])
def test_residency_migration_preserves_legacy_rows_and_observed_evidence(database_url):
    if database_url is None:
        pytest.skip("Direct PostgreSQL test connection not configured")
    path = Path(__file__).parents[2] / "langboard/migrations/versions/20261022030000-e75c3bd62f91.py"
    revision = runpy.run_path(str(path))
    assert revision["down_revision"] == "d64b2ac51e80"
    engine = create_engine(database_url)
    try:
        with engine.begin() as db:
            db.execute(text("CREATE TEMPORARY TABLE card(id BIGINT PRIMARY KEY, title TEXT, created_at TIMESTAMP)"))
            db.execute(text("INSERT INTO card VALUES(1,'preserved','2000-01-01')"))
            with Operations.context(MigrationContext.configure(db)):
                revision["upgrade"]()
            assert db.execute(text("SELECT title,workflow_stage_entered_key,workflow_stage_entered_at FROM card")).one() == (
                "preserved", None, None)
            db.execute(text("UPDATE card SET workflow_stage_entered_key='closed',workflow_stage_entered_at=CURRENT_TIMESTAMP"))
            with Operations.context(MigrationContext.configure(db)), pytest.raises(RuntimeError, match="evidence"):
                revision["downgrade"]()
            assert db.execute(text("SELECT title,workflow_stage_entered_key FROM card")).one() == ("preserved", "closed")
    finally:
        engine.dispose()
