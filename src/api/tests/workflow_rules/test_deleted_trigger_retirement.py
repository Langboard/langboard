"""Retirement uses the real conversion schema and preserves stored history."""

import importlib.util
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def migration():
    return load_module(
        Path(__file__).parents[2] / "langboard/migrations/versions/20261025030000-acde789f0123.py",
        "deleted_trigger_retirement",
    )


def snapshot(connection, table):
    return connection.execute(sa.text(f'SELECT * FROM "{table}" ORDER BY id')).mappings().all()


@pytest.fixture(params=["sqlite://", "postgresql-test"], ids=["sqlite", "postgresql"])
def database(request):
    url = request.param
    if url == "postgresql-test":
        url = os.environ.get("LANGBOARD_FILE_TEST_DATABASE_URL")
        if not url:
            pytest.skip("Set LANGBOARD_FILE_TEST_DATABASE_URL to a disposable PostgreSQL database")
    engine = sa.create_engine(url)
    schema = None
    try:
        if engine.dialect.name == "postgresql":
            schema = "deleted_trigger_retirement_" + uuid4().hex
            with engine.begin() as connection:
                connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
        with engine.begin() as connection:
            if schema:
                connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
                connection.exec_driver_sql("SET LOCAL statement_timeout = '10s'")
                connection.exec_driver_sql("SET LOCAL lock_timeout = '3s'")
            yield connection
    finally:
        try:
            if schema:
                with engine.begin() as connection:
                    connection.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        finally:
            engine.dispose()


@pytest.fixture
def populated(database):
    connection = database
    conversion = load_module(Path(__file__).with_name("test_conversion.py"), "retirement_conversion_fixture")
    conversion.populated(connection)
    with Operations.context(MigrationContext.configure(connection)):
        conversion.migration().upgrade()
    metadata = sa.MetaData()
    metadata.reflect(connection)
    rules = metadata.tables["workflow_rule"]
    deleted = connection.execute(rules.select().where(rules.c.event_type == "card_deleted")).mappings().one()
    # Preserve previously disabled and previously retired definitions too.
    for identity, enabled, retired in ((901, False, False), (902, False, True)):
        connection.execute(rules.insert().values(**{
            **dict(deleted), "id": identity, "is_enabled": enabled, "retired": retired,
        }))
    now = datetime.now(timezone.utc)
    connection.execute(metadata.tables["workflow_rule_condition"].insert().values(
        id=903, created_at=now, updated_at=now, rule_id=deleted["id"], order=0,
        kind="uncompacted_comments", operator="gte", value=5,
    ))
    action_id = connection.execute(sa.text(
        "SELECT id FROM workflow_rule_action WHERE rule_id=:id"
    ), {"id": deleted["id"]}).scalar_one()
    connection.execute(metadata.tables["workflow_rule_run"].insert().values(
        id=904, created_at=now, updated_at=now, rule_id=deleted["id"], configured_by_id=None,
        rule_revision=deleted["revision"], stage_id=1, stage_revision="original-stage-revision",
        event_id="historical-deletion", event_type="card_deleted", occurred_at=now, project_id=2,
        card_id=3, residency="original-residency", actor_id=4, actor_type="user", cause_run_id=None,
        rule_snapshot={"trigger": {"kind": "event", "event_type": "card_deleted"}},
        event_context={"retained": "original-context"}, condition_results=[{"matched": True}],
        status="succeeded", next_attempt_at=now, finished_at=now, error_code=None,
    ))
    connection.execute(metadata.tables["workflow_action_run"].insert().values(
        id=905, created_at=now, updated_at=now, rule_run_id=904, action_id=action_id,
        kind="bot.invoke", executor_id=4, executor_type="bot", idempotency_key="original-identity",
        status="accepted", attempt=1, attempts=[{"status": "accepted"}], result={"receipt": "original"},
        error_code=None, started_at=now, finished_at=None, lease_expires_at=None, legacy_effect_run_id=99,
    ))
    yield connection


def test_retires_enabled_and_disabled_deletion_rules_without_changing_definitions_or_receipts(populated):
    connection = populated
    module = migration()
    assert module.down_revision == "facd678e9012"
    before = snapshot(connection, "workflow_rule")
    retained_tables = (
        "workflow_stage_definition", "workflow_rule_condition", "workflow_rule_action",
        "workflow_rule_run", "workflow_action_run", "workflow_effect_run", "workflow_compaction_schedule",
    )
    retained = {table: snapshot(connection, table) for table in retained_tables}
    with Operations.context(MigrationContext.configure(connection)):
        module.upgrade()
    after = snapshot(connection, "workflow_rule")
    expected = [
        {**row, "retired": 1, "is_enabled": 0}
        if row["trigger_kind"] == "event" and row["event_type"] == "card_deleted" and not row["retired"]
        else dict(row)
        for row in before
    ]
    assert after == expected
    assert not connection.execute(sa.text(
        "SELECT 1 FROM workflow_rule WHERE retired = false AND event_type = 'card_deleted'"
    )).first()
    assert connection.execute(sa.text(
        "SELECT 1 FROM workflow_rule WHERE retired = false AND is_enabled = true AND event_type = 'card_updated'"
    )).first()
    assert {table: snapshot(connection, table) for table in retained_tables} == retained
    with Operations.context(MigrationContext.configure(connection)):
        module.upgrade()
    assert snapshot(connection, "workflow_rule") == after
    assert {table: snapshot(connection, table) for table in retained_tables} == retained


def test_downgrade_refuses_to_guess_previous_activation_state_without_mutation(populated):
    connection = populated
    module = migration()
    with Operations.context(MigrationContext.configure(connection)):
        module.upgrade()
    tables = sa.inspect(connection).get_table_names()
    before = {table: snapshot(connection, table) for table in tables}
    with Operations.context(MigrationContext.configure(connection)), pytest.raises(RuntimeError, match="irreversible"):
        module.downgrade()
    assert {table: snapshot(connection, table) for table in tables} == before
