"""Native policy selections survive upgrade and block destructive downgrade."""

import importlib.util
from pathlib import Path
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text


def test_policy_upgrade_preserves_existing_data_and_downgrade_refuses_loss():
    path = Path(__file__).parents[2] / "langboard/migrations/versions/20261024130000-9bcd012e3456.py"
    spec = importlib.util.spec_from_file_location("policy_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.down_revision == "8abc901d2345"
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE workflow_stage_definition (id INTEGER PRIMARY KEY, entry_bots JSON, continuous_effects JSON)"))
        connection.exec_driver_sql("INSERT INTO workflow_stage_definition VALUES (1, '[]', '{\"auto_compact\":true}')")
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        assert connection.execute(text("SELECT policy_bots, continuous_effects FROM workflow_stage_definition")).one() == ('[]', '{"auto_compact":true}')
        connection.execute(text("UPDATE workflow_stage_definition SET policy_bots='[{\"bot_uid\":\"123456789ab\"}]'"))
        with Operations.context(MigrationContext.configure(connection)), pytest.raises(RuntimeError, match="Cannot discard"):
            migration.downgrade()
        connection.execute(text("UPDATE workflow_stage_definition SET policy_bots='[]'"))
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
        assert connection.execute(text("SELECT continuous_effects FROM workflow_stage_definition")).scalar() == '{"auto_compact":true}'
    engine.dispose()
