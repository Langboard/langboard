"""Normalized rule validation, storage and durable event admission.

All triggers reserve the same rule/action identities. Source transactions own
admission; the existing bounded cron drains durable queued work after commit.
"""
import hashlib
import inspect
import json
import re
from contextvars import ContextVar
from datetime import timezone
from sqlalchemy import delete
from ....core.db import DbSession, SqlBuilder
from ....core.types import SafeDateTime
from ....helpers import InfraHelper
from ...constants.DomainEventType import DomainEventType
from ...models import (
    Card,
    ProjectColumn,
    WorkflowActionRun,
    WorkflowRule,
    WorkflowRuleAction,
    WorkflowRuleCondition,
    WorkflowRuleRun,
    WorkflowStageDefinition,
)


CAUSE_RUN = ContextVar("workflow_cause_run", default=None)
NUMERIC_CONDITIONS = {"stage_residency_seconds", "uncompacted_characters", "uncompacted_comments", "uncompacted_turns"}
BOOLEAN_CONDITIONS = {"actor_is_not_author", "has_unfinished_items", "timers_stopped", "approvals_clear", "dependencies_clear"}
COMPACTION_CONDITIONS = {"uncompacted_characters", "uncompacted_comments", "uncompacted_turns"}
NATIVE_ACTIONS = {"checkitems.complete", "timers.stop", "author.notify"}
ACTION_KINDS = NATIVE_ACTIONS | {"bot.invoke", "comments.compact", "card.archive"}
SUPPORTED_CARD_EVENTS = {event.value for event in DomainEventType if event.value.startswith("card_") and event != DomainEventType.CardDeleted}
UID = re.compile(r"^[A-Za-z0-9]{11}$")


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def rows(db, model, **filters):
    query = SqlBuilder.select.table(model)
    for key, value in filters.items():
        query = query.where(getattr(model, key) == value)
    return db.exec(query).all()


def _keys(value, allowed, required=()):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        raise ValueError("Unsupported or missing workflow fields")


def validate_rules(value):
    if not isinstance(value, list) or len(value) > 1000:
        raise ValueError("Workflow rules must be a list of at most 1000 entries")
    result, rule_ids, action_ids = [], set(), set()
    for raw in value:
        _keys(raw, {"uid", "name", "is_enabled", "revision", "trigger", "condition_match", "conditions", "actions"}, {"name", "trigger", "condition_match", "conditions", "actions"})
        rule = dict(raw)
        for obj, seen in ((rule, rule_ids),):
            if "uid" in obj:
                if not isinstance(obj["uid"], str) or not UID.fullmatch(obj["uid"]) or obj["uid"] in seen:
                    raise ValueError("Invalid or duplicate workflow UID")
                seen.add(obj["uid"])
        if not isinstance(rule["name"], str) or not 1 <= len(rule["name"].strip()) <= 100 or type(rule.get("is_enabled", True)) is not bool:
            raise ValueError("Invalid workflow rule name or enabled flag")
        rule["name"] = rule["name"].strip()
        rule.setdefault("is_enabled", True)
        trigger = rule["trigger"]
        _keys(trigger, {"kind", "event_type", "interval_seconds"}, {"kind"})
        if trigger["kind"] == "event":
            if set(trigger) != {"kind", "event_type"}:
                raise ValueError("An event trigger requires a supported card event")
            if trigger["event_type"] not in SUPPORTED_CARD_EVENTS:
                raise ValueError(f"Unsupported workflow event: {trigger['event_type']}")
        elif trigger["kind"] == "interval":
            if set(trigger) != {"kind", "interval_seconds"} or type(trigger["interval_seconds"]) is not int or not 60 <= trigger["interval_seconds"] <= 86400:
                raise ValueError("Invalid workflow interval")
        elif trigger != {"kind": "stage_entered"}:
            raise ValueError("Invalid workflow trigger")
        if rule["condition_match"] not in {"all", "any"} or not isinstance(rule["conditions"], list) or len(rule["conditions"]) > 20:
            raise ValueError("Invalid workflow condition match")
        kinds = set()
        for condition in rule["conditions"]:
            _keys(condition, {"kind", "operator", "value"}, {"kind", "operator", "value"})
            kind, operator, item = condition["kind"], condition["operator"], condition["value"]
            if kind in kinds:
                raise ValueError("Duplicate workflow condition")
            kinds.add(kind)
            if kind in NUMERIC_CONDITIONS:
                upper = 31536000 if kind == "stage_residency_seconds" else 500000 if kind == "uncompacted_characters" else 100000
                if operator != "gte" or type(item) is not int or not 1 <= item <= upper:
                    raise ValueError("Invalid numeric workflow condition")
            elif kind not in BOOLEAN_CONDITIONS or operator != "eq" or type(item) is not bool:
                raise ValueError("Invalid boolean workflow condition")
        if rule["condition_match"] == "any" and not rule["conditions"]:
            raise ValueError("An OR rule needs conditions")
        if not isinstance(rule["actions"], list) or not 1 <= len(rule["actions"]) <= 20:
            raise ValueError("Workflow rules require actions")
        compact_seen = False
        for index, action in enumerate(rule["actions"]):
            _keys(action, {"uid", "kind", "executor", "config", "requires_previous_success"}, {"kind", "executor", "config", "requires_previous_success"})
            if "uid" in action:
                if not isinstance(action["uid"], str) or not UID.fullmatch(action["uid"]) or action["uid"] in action_ids:
                    raise ValueError("Invalid or duplicate action UID")
                action_ids.add(action["uid"])
            kind = action["kind"]
            if kind not in ACTION_KINDS or type(action["requires_previous_success"]) is not bool or (index == 0 and action["requires_previous_success"]):
                raise ValueError("Invalid workflow action or predecessor")
            if compact_seen and not action["requires_previous_success"]:
                raise ValueError("An action after compaction requires confirmed predecessor success")
            executor = action["executor"]
            _keys(executor, {"kind", "uid"}, {"kind"})
            config = action["config"]
            if kind == "bot.invoke":
                _keys(config, {"bot_uid", "schedule_uid"}, {"bot_uid"})
                if executor != {"kind": "bot", "uid": config["bot_uid"]} or not isinstance(config["bot_uid"], str) or not UID.fullmatch(config["bot_uid"]):
                    raise ValueError("Bot action requires its explicit matching executor")
                if trigger["kind"] == "interval":
                    if "schedule_uid" not in config or not isinstance(config["schedule_uid"], str) or not UID.fullmatch(config["schedule_uid"]):
                        raise ValueError("An interval bot requires an existing card schedule")
                elif "schedule_uid" in config:
                    raise ValueError("A schedule is valid only on an interval rule")
            elif kind in {"comments.compact", "card.archive"}:
                if executor != {"kind": "compactor"}:
                    raise ValueError("Compaction/archive requires the dedicated compactor")
                _keys(config, {"archive_after_seconds"} if kind == "card.archive" else set())
                if kind == "card.archive" and (type(config.get("archive_after_seconds")) is not int or not 0 <= config["archive_after_seconds"] <= 31536000):
                    raise ValueError("Archive requires an explicit grace period")
                if kind == "comments.compact":
                    if not kinds.intersection(COMPACTION_CONDITIONS) or kinds - COMPACTION_CONDITIONS or rule["condition_match"] != "any":
                        raise ValueError("Compaction requires enabled OR volume conditions")
                    compact_seen = True
            else:
                if executor != {"kind": "event_actor"} or trigger["kind"] == "interval":
                    raise ValueError("Native actions require the event actor")
                _keys(config, set())
        result.append(rule)
    return result


def serialize_rule(db, rule):
    trigger = {"kind": rule.trigger_kind}
    if rule.trigger_kind == "event":
        trigger["event_type"] = rule.event_type
    if rule.trigger_kind == "interval":
        trigger["interval_seconds"] = rule.interval_seconds
    return {"uid": rule.get_uid(), "name": rule.name, "is_enabled": rule.is_enabled, "revision": rule.revision,
            "trigger": trigger, "condition_match": rule.condition_match,
            "conditions": [{"kind": c.kind, "operator": c.operator, "value": c.value}
                           for c in sorted(rows(db, WorkflowRuleCondition, rule_id=rule.id), key=lambda c: c.order)],
            "actions": [{"uid": a.get_uid(), "kind": a.kind,
                         "executor": {"kind": a.executor_kind, **({"uid": InfraHelper.convert_uid(a.executor_id)} if a.executor_id is not None else {})},
                         "config": a.config, "requires_previous_success": a.requires_previous_success}
                        for a in sorted(rows(db, WorkflowRuleAction, rule_id=rule.id), key=lambda a: a.order)]}


def stage_rules(stage_id):
    with DbSession.use(readonly=False) as db:
        return [serialize_rule(db, rule) for rule in sorted(rows(db, WorkflowRule, stage_id=stage_id, retired=False), key=lambda r: r.order)]


def replace_rules(db, stage, definitions, *, configured_by_id=None):
    """Retain stable UIDs; retire removed rules so historical reservations never replay."""
    existing = {rule.get_uid(): rule for rule in rows(db, WorkflowRule, stage_id=stage.id, retired=False)}
    retained = set()
    for index, definition in enumerate(validate_rules(definitions)):
        uid = definition.get("uid")
        if uid and uid not in existing:
            raise ValueError("Workflow rule UID belongs to another stage or is retired")
        old = existing.get(uid)
        if old and {k: v for k, v in serialize_rule(db, old).items() if k != "revision"} == {k: v for k, v in definition.items() if k != "revision"}:
            retained.add(old.id)
            continue
        rule = old or WorkflowRule(stage_id=stage.id, name=definition["name"], trigger_kind=definition["trigger"]["kind"])
        if old:
            rule.revision += 1
        if configured_by_id is not None:
            rule.configured_by_id = configured_by_id
        rule.name, rule.is_enabled, rule.order = definition["name"], definition["is_enabled"], index
        rule.trigger_kind, rule.event_type, rule.interval_seconds = definition["trigger"]["kind"], definition["trigger"].get("event_type"), definition["trigger"].get("interval_seconds")
        rule.condition_match = definition["condition_match"]
        db.update(rule) if old else db.insert(rule)
        retained.add(rule.id)
        actions = {a.get_uid(): a for a in rows(db, WorkflowRuleAction, rule_id=rule.id)}
        for action in definition["actions"]:
            if action.get("uid") and action["uid"] not in actions:
                raise ValueError("Workflow action UID belongs to another rule")
        db.exec(delete(WorkflowRuleCondition).where(WorkflowRuleCondition.rule_id == rule.id))
        retained_actions = {item["uid"] for item in definition["actions"] if item.get("uid")}
        for offset, old_action in enumerate(actions.values()):
            if old_action.get_uid() not in retained_actions:
                db.exec(delete(WorkflowRuleAction).where(WorkflowRuleAction.id == old_action.id))
            else:
                # Free positive order slots before reordering retained identities.
                old_action.order = -offset - 1
                db.update(old_action)
        for order, condition in enumerate(definition["conditions"]):
            db.insert(WorkflowRuleCondition(rule_id=rule.id, order=order, **condition))
        for order, action in enumerate(definition["actions"]):
            values = dict(rule_id=rule.id, order=order, kind=action["kind"], executor_kind=action["executor"]["kind"],
                          executor_id=InfraHelper.convert_id(action["executor"]["uid"]) if action["executor"].get("uid") else None,
                          config=action["config"], requires_previous_success=action["requires_previous_success"])
            existing_action = actions.get(action.get("uid"))
            if existing_action is not None:
                for field, value in values.items():
                    setattr(existing_action, field, value)
                db.update(existing_action)
            else:
                db.insert(WorkflowRuleAction(**values))
    for rule in existing.values():
        if rule.id not in retained:
            rule.retired, rule.is_enabled = True, False
            db.update(rule)


def admit(db, actor, card, event_type, *, event_id, occurred_at=None, context=None, rule_id=None):
    """Called inside the source mutation. No external I/O and no permission grants."""
    if card.is_linked_resource or card.deleted_at or card.archived_at or card.visibility not in {"INTERNAL", "SHARED"} or not card.workflow_stage_entered_at:
        return []
    expected_residency = utc(card.workflow_stage_entered_at)
    card = db.exec(SqlBuilder.select.table(Card).where(Card.id == card.id)
        .with_for_update().execution_options(populate_existing=True)).first()
    if (card is None or card.workflow_stage_entered_at is None or utc(card.workflow_stage_entered_at) != expected_residency
            or card.deleted_at or card.archived_at or card.is_linked_resource or card.visibility not in {"INTERNAL", "SHARED"}):
        return []
    column = db.exec(SqlBuilder.select.table(ProjectColumn).where(ProjectColumn.id == card.project_column_id)).first()
    if not column or column.deleted_at or column.is_archive or column.workflow_stage != card.workflow_stage_entered_key:
        return []
    stage = db.exec(SqlBuilder.select.table(WorkflowStageDefinition).where(WorkflowStageDefinition.key == column.workflow_stage)).first()
    if stage is None:
        return []
    cause = CAUSE_RUN.get()
    residency = utc(card.workflow_stage_entered_at).isoformat()
    admitted = []
    for rule in rows(db, WorkflowRule, stage_id=stage.id, retired=False, is_enabled=True):
        if rule_id is not None and rule.id != rule_id:
            continue
        if not (rule.trigger_kind == event_type or rule.trigger_kind == "event" and rule.event_type == event_type):
            continue
        if not stage.is_active and not (event_type == "stage_entered" and all(
                action.kind in NATIVE_ACTIONS for action in rows(db, WorkflowRuleAction, rule_id=rule.id))):
            continue
        if cause is not None:
            parent = db.exec(SqlBuilder.select.table(WorkflowRuleRun).where(WorkflowRuleRun.id == cause)).first()
            if parent and parent.rule_id == rule.id:
                continue
        # Bot/profile authored changes retain the native cascade guard.
        from ...models import User
        if actor is not None and not isinstance(actor, User):
            continue
        previous = rows(db, WorkflowRuleRun, rule_id=rule.id, event_id=event_id, residency=residency)
        if previous:
            admitted.extend(run.id for run in previous)
            continue
        run = WorkflowRuleRun(rule_id=rule.id, rule_revision=rule.revision, configured_by_id=rule.configured_by_id, stage_id=stage.id,
            stage_revision=stage.edit_revision(), event_id=event_id, event_type=event_type,
            occurred_at=occurred_at or SafeDateTime.now(), project_id=card.project_id, card_id=card.id,
            residency=residency, actor_id=actor.id if actor else None, actor_type="user" if actor else "system",
            next_attempt_at=occurred_at or SafeDateTime.now(), cause_run_id=cause, rule_snapshot={"stage_key": stage.key, **serialize_rule(db, rule)}, event_context=context or {})
        db.insert(run)
        for action in rows(db, WorkflowRuleAction, rule_id=rule.id):
            key = hashlib.sha256(f"{event_id}:{rule.id}:{action.id}:{residency}".encode()).hexdigest()
            db.insert(WorkflowActionRun(rule_run_id=run.id, action_id=action.id, kind=action.kind,
                executor_id=action.executor_id if action.executor_kind == "bot" else run.actor_id if action.executor_kind == "event_actor" else None,
                executor_type={"event_actor": "user", "bot": "bot", "compactor": "profile"}[action.executor_kind], idempotency_key=key))
        admitted.append(run.id)
    return admitted


def admit_native_task(function, args, kwargs):
    """Broker ingress records only source references, before the source commit."""
    module = getattr(function, "__module__", "")
    name = getattr(function, "__name__", "")
    if ".tasks.bots.Card" not in module:
        return []
    event_type = "card_relationships_updated" if name == "card_relationship_updated" else name
    if event_type not in {event.value for event in DomainEventType if event.value.startswith("card_")}:
        return []
    from ...models import Bot, Project, User
    values = [*args, *kwargs.values()]
    card = next((value for value in values if isinstance(value, Card)), None)
    actor = next((value for value in values if isinstance(value, (User, Bot))), None)
    if card is None:
        return []
    refs = {value.__class__.__name__: str(value.get_uid()) for value in values if hasattr(value, "get_uid")}
    try:
        parameters = inspect.signature(function).bind(*args, **kwargs).arguments
    except TypeError:
        parameters = {}
    metadata = {}
    if isinstance(parameters.get("reaction"), str):
        metadata["reaction_type"] = parameters["reaction"]
    for name, key in (("new_card", "cardified_card_uid"), ("old_column", "old_project_column_uid")):
        if hasattr(parameters.get(name), "get_uid"):
            metadata[key] = parameters[name].get_uid()
    # Actor and board revisions are context, not mutations of the event source.
    # Refreshing either between source retries must retain the reservation.
    stamps = ":".join(
        f"{value.__class__.__name__}:{value.get_uid()}:"
        f"{'' if isinstance(value, (User, Bot, Project)) else getattr(value, 'updated_at', '')}"
        for value in values if hasattr(value, "get_uid")
    )
    identity = hashlib.sha256(f"{event_type}:{card.id}:{card.last_change_seq}:{stamps}:{json.dumps(metadata, sort_keys=True)}".encode()).hexdigest()
    with DbSession.atomic() as db:
        # Card row serializes admission against stage edits/moves and repeat delivery.
        fresh = db.exec(SqlBuilder.select.table(Card).where(Card.id == card.id).with_for_update()).first()
        if (fresh is None or fresh.workflow_stage_entered_at is None or card.workflow_stage_entered_at is None
                or utc(fresh.workflow_stage_entered_at) != utc(card.workflow_stage_entered_at)
                or fresh.workflow_stage_entered_key != card.workflow_stage_entered_key):
            return []
        return admit(db, actor, fresh, event_type, event_id=identity,
                     context={"producer": module, "refs": refs, "metadata": metadata})


def selected_bots(db, card, event_type):
    """A configured workflow owns its selected bot's native event admission."""
    if not card.workflow_stage_entered_key:
        return set()
    stage = db.exec(SqlBuilder.select.table(WorkflowStageDefinition).where(
        WorkflowStageDefinition.key == card.workflow_stage_entered_key, WorkflowStageDefinition.is_active.is_(True))).first()
    if stage is None:
        return set()
    result = set()
    for rule in rows(db, WorkflowRule, stage_id=stage.id, retired=False, is_enabled=True):
        if (rule.trigger_kind == "event" and rule.event_type == event_type
                or rule.trigger_kind == "stage_entered" and event_type == DomainEventType.CardMoved.value):
            result.update(action.config["bot_uid"] for action in rows(db, WorkflowRuleAction, rule_id=rule.id) if action.kind == "bot.invoke")
    return result


def compaction_threshold_for_rule(db, rule_id):
    result = {"compact_after_characters": 0, "compact_after_comments": 0, "compact_after_turns": 0}
    keys = {"uncompacted_characters": "compact_after_characters", "uncompacted_comments": "compact_after_comments", "uncompacted_turns": "compact_after_turns"}
    for condition in rows(db, WorkflowRuleCondition, rule_id=rule_id):
        if condition.kind in keys:
            result[keys[condition.kind]] = condition.value
    if not any(result.values()):
        raise ValueError("Compaction needs an enabled threshold")
    return result


def action_policy(db, stage, rule_id, action_kind):
    """Native services require an explicit current normalized action reference."""
    rule = db.exec(SqlBuilder.select.table(WorkflowRule).where(WorkflowRule.id == rule_id,
        WorkflowRule.stage_id == stage.id, WorkflowRule.is_enabled.is_(True), WorkflowRule.retired.is_(False))
        .execution_options(populate_existing=True).with_for_update()).first()
    if rule is None:
        return None
    action = next((a for a in rows(db, WorkflowRuleAction, rule_id=rule.id) if a.kind == action_kind), None)
    return (rule, action) if action is not None else None


def originating_run_is_current(db, rule, stage, card):
    """Fence delayed effects against their dispatcher origin under current effect locks."""
    run_id = CAUSE_RUN.get()
    if run_id is None:
        return True
    run = db.exec(SqlBuilder.select.table(WorkflowRuleRun).where(WorkflowRuleRun.id == run_id)
                  .execution_options(populate_existing=True)).first()
    return bool(run is not None and run.project_id == card.project_id and run.card_id == card.id
                and run.stage_id == stage.id and run.rule_id == rule.id and run.rule_revision == rule.revision
                and run.stage_revision == stage.edit_revision() and card.workflow_stage_entered_key == stage.key
                and card.workflow_stage_entered_at is not None
                and utc(card.workflow_stage_entered_at).isoformat() == run.residency)
