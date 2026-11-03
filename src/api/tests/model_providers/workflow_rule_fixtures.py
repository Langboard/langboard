"""Normalized workflow fixtures shared by native command regression tests."""
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.domain.models import (
    WorkflowRule, WorkflowRuleAction, WorkflowRuleCondition, WorkflowRuleRun, WorkflowActionRun,
)
from langboard_shared.domain.services.factory.WorkflowRuleService import replace_rules, stage_rules


def create_rule_tables():
    for model in (WorkflowRule, WorkflowRuleAction, WorkflowRuleCondition, WorkflowRuleRun, WorkflowActionRun):
        model.__table__.create(DbEngine.get_main_engine(), checkfirst=True)


def rule_id(stage):
    """Return the fixture's sole current rule without changing the fixture tuple."""
    with DbSession.use(readonly=False) as db:
        return db.exec(SqlBuilder.select.table(WorkflowRule).where(
            WorkflowRule.stage_id == stage.id, WorkflowRule.retired.is_(False))).first().id


def configure_rule(db, stage, *, characters=1000, comments=0, turns=0, compact=True, archive=False,
                   archive_after_seconds=3600, enabled=True, interval=300):
    existing = stage_rules(stage.id)
    actions = []
    if compact:
        actions.append({"kind": "comments.compact", "executor": {"kind": "compactor"},
                        "config": {}, "requires_previous_success": False})
    if archive:
        actions.append({"kind": "card.archive", "executor": {"kind": "compactor"},
                        "config": {"archive_after_seconds": archive_after_seconds},
                        "requires_previous_success": compact})
    conditions = [{"kind": kind, "operator": "gte", "value": value} for kind, value in (
        ("uncompacted_characters", characters), ("uncompacted_comments", comments),
        ("uncompacted_turns", turns)) if compact and value]
    definition = {"name": "Native compactor regression", "is_enabled": enabled,
                  "trigger": {"kind": "interval", "interval_seconds": interval},
                  "condition_match": "any" if compact else "all", "conditions": conditions, "actions": actions}
    if existing:
        definition["uid"] = existing[0]["uid"]
        for action in actions:
            previous = next((item for item in existing[0]["actions"] if item["kind"] == action["kind"]), None)
            if previous:
                action["uid"] = previous["uid"]
    replace_rules(db, stage, [definition])
    return db.exec(SqlBuilder.select.table(WorkflowRule).where(
        WorkflowRule.stage_id == stage.id, WorkflowRule.retired.is_(False))).first()
