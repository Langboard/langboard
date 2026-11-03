"""Idempotent owner of built-in workflow defaults; migrations only own schema."""

import json
from pathlib import Path
from sqlalchemy import func, select
from ...core.db import DbSession, SqlBuilder
from ..models import WorkflowStageDefinition


DEFAULTS_PATH = Path(__file__).parents[2] / "core/resources/locales/workflow_stages.json"


def bootstrap_workflow_stages():
    defaults = json.loads(DEFAULTS_PATH.read_text(encoding="utf-8"))
    with DbSession.atomic() as db:
        db.exec(select(func.pg_advisory_xact_lock(7104389251163)))
        existing = {row.key for row in db.exec(SqlBuilder.select.table(WorkflowStageDefinition)).all()}
        inserted = 0
        from .factory.WorkflowRuleService import replace_rules
        for definition in defaults:
            if definition["key"] not in existing:
                definition = dict(definition)
                rules = definition.pop("rules")
                stage = WorkflowStageDefinition(**definition)
                db.insert(stage)
                replace_rules(db, stage, rules)
                inserted += 1
        return {"inserted": inserted}
