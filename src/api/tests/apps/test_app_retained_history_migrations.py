# ruff: noqa: F811
"""Native migrations preserve connection consent and execution review receipts."""

import importlib
import json
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.domain.services.factory.WorkflowStageService_app_test import board  # noqa: F401
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError


@pytest.mark.parametrize("revision,table,owner,payload,error", [
    ("20261011221000-b86548cd52e3", "app_execution_review", "request_id",
     {"fingerprint": "fixture", "receipt": '{"state":"retained"}'}, "Cannot discard retained execution Review history"),
    ("20261011230000-c97659de63f4", "app_mcp_consent", "connection_id",
     {"identity_hash": "fixture", "scope_tools": '["cards.read"]', "revision": 1}, "Cannot discard retained MCP consent"),
])
def test_retained_history_upgrade_unique_owner_and_guarded_downgrade(board, monkeypatch, revision, table, owner, payload, error):
    migration = importlib.import_module(f"langboard.migrations.versions.{revision}")
    engine = DbEngine.get_main_engine()
    with engine.begin() as db:
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(db)))
        migration.upgrade()
        migration.downgrade()
        assert not inspect(db).has_table(table)
        migration.upgrade()
    values = {"id": 101, "created_at": "2026-10-01 00:00:00", "updated_at": "2026-10-01 00:00:00", owner: 201, **payload}
    # Table and column names come only from the fixed parametrization above.
    insert = text(f"INSERT INTO {table} ({', '.join(values)}) VALUES ({', '.join(':' + key for key in values)})")
    with engine.begin() as db:
        db.execute(insert, values)
    with pytest.raises(IntegrityError):
        with engine.begin() as db:
            db.execute(insert, {**values, "id": 102})
    with pytest.raises(RuntimeError, match=error):
        with engine.begin() as db:
            monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(db)))
            migration.downgrade()
    with engine.connect() as db:
        row = db.execute(text(f"SELECT * FROM {table}")).mappings().one()
        assert row[owner] == 201
        assert row["identity_hash" if table == "app_mcp_consent" else "fingerprint"] == "fixture"
        value = row["scope_tools" if table == "app_mcp_consent" else "receipt"]
        if isinstance(value, str):
            value = json.loads(value)
        assert value == (["cards.read"] if table == "app_mcp_consent" else {"state": "retained"})
