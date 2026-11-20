"""Real SQLite DDL/data conversion; historical receipts remain byte-for-byte retained."""
import importlib.util
import json
from pathlib import Path
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


def migration():
    path = Path(__file__).parents[2] / "langboard/migrations/versions/20261024140000-acde123f4567.py"
    spec = importlib.util.spec_from_file_location("common_workflow_conversion", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def populated(connection, *, unknown=False):
    connection.execute(text("CREATE TABLE workflow_stage_definition(id BIGINT PRIMARY KEY, entry_effects JSON, entry_bots JSON, policy_bots JSON, continuous_effects JSON)"))
    settings = {"auto_compact": True, "auto_archive": True, "interval_seconds": 420, "archive_after_seconds": 777,
                "compact_after_characters": 24000, "compact_after_comments": 20, "compact_after_turns": 5,
                "bots": [{"bot_uid": "123456789ab", "condition": "bot_cron_scheduled", "schedule_uid": "123456789ac", "interval_seconds": 120}]}
    if unknown:
        settings["unexpected"] = True
    connection.execute(text("INSERT INTO workflow_stage_definition VALUES(1,:effects,:entry,:policy,:continuous)"), {
        "effects": json.dumps(["complete_checkitems", "stop_running_timers", "notify_author_on_entry", "confirm_descendant_move"]),
        "entry": json.dumps([{"bot_uid": "123456789ab", "condition": "card_moved"}]),
        "policy": json.dumps([{"bot_uid": "123456789ab"}]), "continuous": json.dumps(settings)})
    connection.execute(text("CREATE TABLE workflow_effect_run(id BIGINT PRIMARY KEY,status TEXT, result TEXT,source TEXT)"))
    connection.execute(text("INSERT INTO workflow_effect_run VALUES(99,'accepted','retained-original-receipt','manual')"))
    connection.execute(text("CREATE TABLE workflow_compaction_schedule(id BIGINT PRIMARY KEY,lease_token TEXT)"))


def test_populated_conversion_covers_every_legacy_behavior_and_drops_runtime_fields():
    module = migration()
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        populated(connection)
        before = connection.execute(text("SELECT * FROM workflow_effect_run")).all()
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
        columns = {column["name"] for column in inspect(connection).get_columns("workflow_stage_definition")}
        assert not columns.intersection({"entry_effects", "entry_bots", "policy_bots", "continuous_effects"})
        assert connection.execute(text("SELECT confirm_descendant_move FROM workflow_stage_definition")).scalar() == 1
        assert connection.execute(text("SELECT COUNT(*) FROM workflow_rule")).scalar() == len(module.CARD_EVENTS) + 4
        assert connection.execute(text("SELECT COUNT(*) FROM workflow_rule_action")).scalar() == len(module.CARD_EVENTS) + 7
        kinds = connection.execute(text("SELECT kind FROM workflow_rule_action")).scalars().all()
        assert {"checkitems.complete", "timers.stop", "author.notify", "comments.compact", "card.archive", "bot.invoke"}.issubset(kinds)
        assert connection.execute(text("SELECT interval_seconds FROM workflow_rule WHERE name='Compact then archive'")).scalar() == 420
        archive = connection.execute(text("SELECT config,requires_previous_success FROM workflow_rule_action WHERE kind='card.archive'")).one()
        assert json.loads(archive[0]) == {"archive_after_seconds": 777} and archive[1] == 1
        assert connection.execute(text("SELECT * FROM workflow_effect_run")).all() == before
        assert connection.execute(text("SELECT COUNT(*) FROM workflow_rule_run")).scalar() == 0
        assert connection.execute(text("SELECT COUNT(*) FROM workflow_action_run")).scalar() == 0
        with Operations.context(MigrationContext.configure(connection)), pytest.raises(RuntimeError, match="irreversible"):
            module.downgrade()


@pytest.mark.parametrize("case", ["unknown", "running", "lease"])
def test_cutover_refuses_unknown_settings_or_inflight_work_before_mutation(case):
    module = migration()
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        populated(connection, unknown=case == "unknown")
        if case == "running":
            connection.execute(text("UPDATE workflow_effect_run SET status='running'"))
        if case == "lease":
            connection.execute(text("INSERT INTO workflow_compaction_schedule VALUES(1,'active-token')"))
        before = connection.execute(text("SELECT * FROM workflow_stage_definition")).all()
        with Operations.context(MigrationContext.configure(connection)), pytest.raises(RuntimeError):
            module.upgrade()
        assert connection.execute(text("SELECT * FROM workflow_stage_definition")).all() == before
        assert "workflow_rule" not in inspect(connection).get_table_names()


def test_disabled_continuous_configuration_retains_threshold_and_grace_values():
    module = migration()
    confirm, definitions = module.convert_stage({"entry_effects": [], "entry_bots": [], "policy_bots": [],
        "continuous_effects": {"auto_compact": False, "auto_archive": False, "compact_after_characters": 0,
                               "compact_after_comments": 32, "archive_after_seconds": 567, "interval_seconds": 900}})
    assert not confirm and len(definitions) == 2
    assert all(not rule["is_enabled"] for rule in definitions)
    assert definitions[0]["conditions"] == [{"kind": "uncompacted_comments", "operator": "gte", "value": 32}]
    assert definitions[1]["actions"][0]["config"] == {"archive_after_seconds": 567}


@pytest.mark.parametrize("source", ["bot_entry", "bot_policy", "bot_interval"])
@pytest.mark.parametrize("status", ["accepted", "uncertain"])
def test_cutover_refuses_unresolved_retired_stage_bot_outcomes_without_rewriting_receipts(source, status):
    module = migration()
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        populated(connection)
        connection.execute(text("UPDATE workflow_effect_run SET source=:source,status=:status"), {"source": source, "status": status})
        before = connection.execute(text("SELECT * FROM workflow_effect_run")).all()
        with Operations.context(MigrationContext.configure(connection)), pytest.raises(RuntimeError, match="retired stage bot"):
            module.upgrade()
        assert connection.execute(text("SELECT * FROM workflow_effect_run")).all() == before
        assert "workflow_rule" not in inspect(connection).get_table_names()


def test_cutover_preserves_native_onetime_schedule_receipts_in_every_unresolved_state():
    module = migration()
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        populated(connection)
        connection.execute(text("CREATE TABLE bot_schedule_run(id BIGINT PRIMARY KEY,state TEXT,occurrence_key TEXT)"))
        for index, state in enumerate(("pending", "dispatching", "accepted", "uncertain")):
            connection.execute(text("INSERT INTO bot_schedule_run VALUES(:id,:state,:key)"), {"id": index, "state": state, "key": f"native-original-{index}"})
        before = connection.execute(text("SELECT * FROM bot_schedule_run ORDER BY id")).all()
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
        assert connection.execute(text("SELECT * FROM bot_schedule_run ORDER BY id")).all() == before
