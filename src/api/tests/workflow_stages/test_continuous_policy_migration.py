"""Additive continuous-policy storage retains prior definitions and prevents data loss."""

import importlib.util
import json
from pathlib import Path
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


def test_continuous_policy_migration_preserves_definitions_and_refuses_configured_rollback():
    path = Path(__file__).parents[2] / "langboard/migrations/versions/20261021030000-d6e821bc704a.py"
    spec = importlib.util.spec_from_file_location("continuous_policy_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.down_revision == "c4a8f16d3092"
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE workflow_stage_definition(id BIGINT PRIMARY KEY, key TEXT UNIQUE, entry_effects JSON NOT NULL)"))
        connection.execute(text("INSERT INTO workflow_stage_definition VALUES(1,'closed','[\"stop_running_timers\"]'),(2,'active','[]')"))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        rows = connection.execute(text("SELECT id,key,entry_effects,continuous_effects FROM workflow_stage_definition ORDER BY id")).all()
        assert rows == [(1, "closed", '["stop_running_timers"]', "{}"), (2, "active", "[]", "{}")]
        policy = json.dumps({"auto_compact": True})
        connection.execute(text("UPDATE workflow_stage_definition SET continuous_effects=:policy WHERE id=1"), {"policy": policy})
        with Operations.context(MigrationContext.configure(connection)), pytest.raises(RuntimeError, match="Cannot discard"):
            migration.downgrade()
        assert connection.execute(text("SELECT continuous_effects FROM workflow_stage_definition WHERE id=1")).scalar_one() == policy
        connection.execute(text("UPDATE workflow_stage_definition SET continuous_effects='{}'"))
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
        assert "continuous_effects" not in {column["name"] for column in inspect(connection).get_columns("workflow_stage_definition")}
        assert connection.execute(text("SELECT entry_effects FROM workflow_stage_definition WHERE id=1")).scalar_one() == '["stop_running_timers"]'
    engine.dispose()
