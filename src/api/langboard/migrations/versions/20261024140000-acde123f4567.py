"""Convert workflow configuration once; retire the four legacy runtime fields.

Operators must quiesce old API, workers and cron before upgrade. The migration
refuses in-flight work and unrepresentable settings. Historical effect/schedule
receipts are retained and never scheduled by this migration.
"""
import json
from datetime import datetime, timezone
import sqlalchemy as sa
from alembic import op
from langboard_shared.core.db import SnowflakeIDType
from langboard_shared.core.types import SnowflakeID


revision = "acde123f4567"
down_revision = "9bcd012e3456"
branch_labels = None
depends_on = None

# Frozen identities: migrations must not change when the runtime enum grows.
CARD_EVENTS = (
    "card_created", "card_updated", "card_moved", "card_labels_updated", "card_relationships_updated", "card_deleted",
    "card_attachment_uploaded", "card_attachment_name_changed", "card_attachment_deleted",
    "card_comment_added", "card_comment_updated", "card_comment_deleted", "card_comment_reacted", "card_comment_unreacted",
    "card_checklist_created", "card_checklist_title_changed", "card_checklist_checked", "card_checklist_unchecked", "card_checklist_deleted",
    "card_checkitem_created", "card_checkitem_title_changed", "card_checkitem_timer_started", "card_checkitem_timer_paused",
    "card_checkitem_timer_stopped", "card_checkitem_checked", "card_checkitem_unchecked", "card_checkitem_cardified", "card_checkitem_deleted",
)


def _json(value):
    return json.loads(value) if isinstance(value, str) else value


def convert_stage(row):
    """Pure, exhaustive conversion used by migration tests; never used at runtime."""
    effects, entry, policy, continuous = (_json(row[key]) for key in ("entry_effects", "entry_bots", "policy_bots", "continuous_effects"))
    allowed = {"complete_checkitems": "checkitems.complete", "stop_running_timers": "timers.stop", "notify_author_on_entry": "author.notify"}
    if not isinstance(effects, list) or len(effects) != len(set(effects)) or set(effects) - set(allowed) - {"confirm_descendant_move"}:
        raise RuntimeError("Unrepresentable workflow entry effects")
    if not isinstance(entry, list) or not isinstance(policy, list) or not isinstance(continuous, dict):
        raise RuntimeError("Malformed workflow settings")
    defaults = {"auto_compact": False, "auto_archive": False, "interval_seconds": 300, "archive_after_seconds": 86400,
                "compact_after_characters": 12000, "compact_after_comments": 0, "compact_after_turns": 0}
    if set(continuous) - set(defaults) - {"bots"}:
        raise RuntimeError("Unrepresentable continuous workflow fields")
    settings = {**defaults, **continuous}
    for key in ("auto_compact", "auto_archive"):
        if type(settings[key]) is not bool:
            raise RuntimeError("Malformed workflow switch")
    for key, lower, upper in (("interval_seconds", 60, 86400), ("archive_after_seconds", 0, 31536000),
                              ("compact_after_characters", 0, 500000), ("compact_after_comments", 0, 100000), ("compact_after_turns", 0, 100000)):
        if type(settings[key]) is not int or not lower <= settings[key] <= upper:
            raise RuntimeError("Malformed workflow interval or threshold")
    rules = []

    def action(kind, config=None, executor=None, previous=False):
        return {"kind": kind, "executor": executor or {"kind": "event_actor"}, "config": config or {}, "requires_previous_success": previous}

    def rule(name, trigger, actions, conditions=None, enabled=True, match="all"):
        rules.append({"name": name, "is_enabled": enabled, "trigger": trigger,
                      "condition_match": match, "conditions": conditions or [], "actions": actions})

    native = [action(allowed[effect]) for effect in effects if effect in allowed]
    if native:
        rule("Stage entry actions", {"kind": "stage_entered"}, native)

    def bot_actions(bindings, mode):
        if not isinstance(bindings, list):
            raise RuntimeError("Malformed workflow bot bindings")
        seen = set()
        for binding in bindings:
            expected = {"bot_uid"} if mode == "policy" else {"bot_uid", "condition"} if mode == "entry" else {"bot_uid", "condition", "interval_seconds", "schedule_uid"}
            if not isinstance(binding, dict) or set(binding) - expected or "bot_uid" not in binding:
                raise RuntimeError("Unrepresentable workflow bot binding")
            uid = binding["bot_uid"]
            try:
                SnowflakeID.from_short_code(uid)
            except (ValueError, TypeError):
                raise RuntimeError("Invalid workflow bot UID") from None
            if uid in seen:
                raise RuntimeError("Duplicate workflow bot binding")
            seen.add(uid)
            config = {"bot_uid": uid}
            if mode == "entry":
                if binding.get("condition") != "card_moved":
                    raise RuntimeError("Unrepresentable entry bot condition")
                triggers = [{"kind": "stage_entered"}]
            elif mode == "policy":
                triggers = [{"kind": "event", "event_type": event} for event in CARD_EVENTS]
            else:
                if binding.get("condition") != "bot_cron_scheduled" or not isinstance(binding.get("schedule_uid"), str):
                    raise RuntimeError("Unrepresentable interval bot condition/schedule")
                try:
                    SnowflakeID.from_short_code(binding["schedule_uid"])
                except (ValueError, TypeError):
                    raise RuntimeError("Invalid workflow schedule UID") from None
                interval = binding.get("interval_seconds", 300)
                if type(interval) is not int or not 60 <= interval <= 86400:
                    raise RuntimeError("Invalid workflow bot interval")
                config["schedule_uid"] = binding["schedule_uid"]
                triggers = [{"kind": "interval", "interval_seconds": interval}]
            for trigger in triggers:
                rule(f"Bot {uid}: {trigger.get('event_type', mode)}", trigger,
                     [action("bot.invoke", config, {"kind": "bot", "uid": uid})])
    bot_actions(entry, "entry")
    bot_actions(policy, "policy")
    bot_actions(settings.get("bots", []), "interval")
    # Preserve disabled policies and customized thresholds/grace periods as disabled
    # rules. A later explicit save controls their activation.
    if continuous:
        conditions = [{"kind": kind, "operator": "gte", "value": settings[key]} for key, kind in (
            ("compact_after_characters", "uncompacted_characters"), ("compact_after_comments", "uncompacted_comments"),
            ("compact_after_turns", "uncompacted_turns")) if settings[key] > 0]
        if not conditions:
            raise RuntimeError("Cannot convert an all-disabled compaction threshold policy")
        compact = action("comments.compact", executor={"kind": "compactor"})
        archive = action("card.archive", {"archive_after_seconds": settings["archive_after_seconds"]}, {"kind": "compactor"}, previous=True)
        if settings["auto_compact"] and settings["auto_archive"]:
            rule("Compact then archive", {"kind": "interval", "interval_seconds": settings["interval_seconds"]},
                 [compact, archive], conditions, match="any")
        else:
            rule("Comment compaction", {"kind": "interval", "interval_seconds": settings["interval_seconds"]},
                 [compact], conditions, enabled=settings["auto_compact"], match="any")
            archive["requires_previous_success"] = False
            rule("Archive verified work", {"kind": "interval", "interval_seconds": settings["interval_seconds"]},
                 [archive], enabled=settings["auto_archive"])
    return "confirm_descendant_move" in effects, rules


def _base():
    return [sa.Column("id", SnowflakeIDType(), primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False)]


def _id(name, nullable=False):
    return sa.Column(name, SnowflakeIDType(), nullable=nullable)


def _str(name, length=64, nullable=False):
    return sa.Column(name, sa.String(length), nullable=nullable)


def _dt(name, nullable=True):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade():
    connection = op.get_bind()
    if connection.dialect.name == "sqlite" and not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
    if connection.dialect.name == "postgresql":
        connection.exec_driver_sql("LOCK TABLE workflow_stage_definition IN ACCESS EXCLUSIVE MODE")
    names = sa.inspect(connection).get_table_names()
    now = datetime.now(timezone.utc)
    if "workflow_effect_run" in names and connection.execute(sa.text(
        "SELECT 1 FROM workflow_effect_run WHERE status = 'running' LIMIT 1")).first():
        raise RuntimeError("Quiesce and reconcile running workflow effects before cutover")
    if "workflow_effect_run" in names and connection.execute(sa.text(
        "SELECT 1 FROM workflow_effect_run WHERE source IN ('bot_entry','bot_policy','bot_interval') "
        "AND status IN ('accepted','uncertain') LIMIT 1")).first():
        raise RuntimeError("Reconcile accepted or uncertain retired stage bot executions before cutover")
    if "workflow_compaction_schedule" in names and connection.execute(sa.text(
        "SELECT 1 FROM workflow_compaction_schedule WHERE lease_token IS NOT NULL LIMIT 1")).first():
        raise RuntimeError("Quiesce and reconcile workflow compaction leases before cutover")
    sources = connection.execute(sa.text("SELECT id, entry_effects, entry_bots, policy_bots, continuous_effects FROM workflow_stage_definition")).mappings().all()
    converted = [(row, *convert_stage(row)) for row in sources]  # Validate every row before DDL/data changes.
    rule_table = op.create_table("workflow_rule", *_base(), _id("stage_id"), _id("configured_by_id", True), _str("name", 100),
        sa.Column("is_enabled", sa.Boolean(), nullable=False), sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("order", sa.Integer(), nullable=False), _str("trigger_kind", 20), _str("event_type", 80, True),
        sa.Column("interval_seconds", sa.Integer(), nullable=True), _str("condition_match", 3), sa.Column("retired", sa.Boolean(), nullable=False),
        sa.CheckConstraint("revision >= 1", name="ck_workflow_rule_revision"),
        sa.CheckConstraint("(trigger_kind = 'stage_entered' AND event_type IS NULL AND interval_seconds IS NULL) OR (trigger_kind = 'event' AND event_type IS NOT NULL AND interval_seconds IS NULL) OR (trigger_kind = 'interval' AND event_type IS NULL AND interval_seconds BETWEEN 60 AND 86400)", name="ck_workflow_rule_trigger"))
    condition_table = op.create_table("workflow_rule_condition", *_base(), _id("rule_id"), sa.Column("order", sa.Integer(), nullable=False),
        _str("kind"), _str("operator", 8), sa.Column("value", sa.JSON(), nullable=False),
        sa.UniqueConstraint("rule_id", "order", name="uq_workflow_rule_condition_order"))
    action_table = op.create_table("workflow_rule_action", *_base(), _id("rule_id"), sa.Column("order", sa.Integer(), nullable=False),
        _str("kind", 32), _str("executor_kind", 16), _id("executor_id", True), sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("requires_previous_success", sa.Boolean(), nullable=False), sa.UniqueConstraint("rule_id", "order", name="uq_workflow_rule_action_order"))
    op.create_table("workflow_rule_run", *_base(), _id("rule_id"), _id("configured_by_id", True), sa.Column("rule_revision", sa.Integer(), nullable=False),
        _id("stage_id"), _str("stage_revision"), _str("event_id", 128), _str("event_type", 80), _dt("occurred_at", False),
        _id("project_id"), _id("card_id"), _str("residency", 40), _id("actor_id", True), _str("actor_type", 8),
        _id("cause_run_id", True), sa.Column("rule_snapshot", sa.JSON(), nullable=False), sa.Column("event_context", sa.JSON(), nullable=False), sa.Column("condition_results", sa.JSON(), nullable=False),
        _str("status", 20), _dt("next_attempt_at", False), _dt("finished_at"), _str("error_code", 64, True),
        sa.UniqueConstraint("rule_id", "event_id", "residency", name="uq_workflow_rule_event"))
    op.create_table("workflow_action_run", *_base(), _id("rule_run_id"), _id("action_id"), _str("kind", 32),
        _id("executor_id", True), _str("executor_type", 16), _str("idempotency_key"), _str("status", 20),
        sa.Column("attempt", sa.Integer(), nullable=False), sa.Column("attempts", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False), _str("error_code", 64, True), _dt("started_at"), _dt("finished_at"),
        _dt("lease_expires_at"), _id("legacy_effect_run_id", True))
    for table, columns, unique, name in (
        ("workflow_rule", ["stage_id"], False, "ix_workflow_rule_stage_id"),
        ("workflow_rule_condition", ["rule_id"], False, "ix_workflow_rule_condition_rule_id"),
        ("workflow_rule_action", ["rule_id"], False, "ix_workflow_rule_action_rule_id"),
        ("workflow_rule_run", ["stage_id"], False, "ix_workflow_rule_run_stage_id"),
        ("workflow_rule_run", ["card_id"], False, "ix_workflow_rule_run_card_id"),
        ("workflow_rule_run", ["status", "next_attempt_at"], False, "ix_workflow_rule_run_due"),
        ("workflow_action_run", ["rule_run_id"], False, "ix_workflow_action_run_rule_run_id"),
        ("workflow_action_run", ["idempotency_key"], True, "ix_workflow_action_run_identity"),
    ):
        op.create_index(name, table, columns, unique=unique)
    op.add_column("workflow_stage_definition", sa.Column("confirm_descendant_move", sa.Boolean(), nullable=False, server_default=sa.false()))
    expected = {"workflow_rule": 0, "workflow_rule_condition": 0, "workflow_rule_action": 0}
    for source, confirm, definitions in converted:
        connection.execute(sa.text("UPDATE workflow_stage_definition SET confirm_descendant_move = :confirm WHERE id = :id"), {"confirm": confirm, "id": source["id"]})
        for order, definition in enumerate(definitions):
            identity = SnowflakeID()
            trigger = definition["trigger"]
            connection.execute(rule_table.insert().values(id=identity, created_at=now, updated_at=now, stage_id=source["id"],
                name=definition["name"], is_enabled=definition["is_enabled"], revision=1, order=order,
                trigger_kind=trigger["kind"], event_type=trigger.get("event_type"), interval_seconds=trigger.get("interval_seconds"),
                condition_match=definition["condition_match"], retired=False))
            expected["workflow_rule"] += 1
            for index, condition in enumerate(definition["conditions"]):
                connection.execute(condition_table.insert().values(id=SnowflakeID(), created_at=now, updated_at=now, rule_id=identity, order=index, **condition))
                expected["workflow_rule_condition"] += 1
            for index, action in enumerate(definition["actions"]):
                executor = action["executor"]
                connection.execute(action_table.insert().values(id=SnowflakeID(), created_at=now, updated_at=now, rule_id=identity, order=index,
                    kind=action["kind"], executor_kind=executor["kind"], executor_id=SnowflakeID.from_short_code(executor["uid"]) if executor.get("uid") else None,
                    config=action["config"], requires_previous_success=action["requires_previous_success"]))
                expected["workflow_rule_action"] += 1
    for table, count in expected.items():
        if connection.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar_one() != count:
            raise RuntimeError("Workflow conversion count verification failed")
    for name in ("entry_effects", "entry_bots", "policy_bots", "continuous_effects"):
        op.drop_column("workflow_stage_definition", name)


def downgrade():
    raise RuntimeError("Workflow rule retirement is irreversible; restore a verified pre-cutover backup with quiesced old workers")
