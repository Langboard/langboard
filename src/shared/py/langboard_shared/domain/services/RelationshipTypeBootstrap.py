"""Provide explicit relationship meanings without reclassifying existing edges."""

import json
from pathlib import Path
from sqlalchemy import func, select
from ...core.db import DbSession, SqlBuilder
from ..models import GlobalCardRelationshipType


DEFAULTS_PATH = Path(__file__).parents[2] / "core/resources/locales/relationship_types.json"


def bootstrap_relationship_types():
    defaults = json.loads(DEFAULTS_PATH.read_text(encoding="utf-8"))
    with DbSession.atomic() as db:
        db.exec(select(func.pg_advisory_xact_lock(7104389251164)))
        existing = {
            row.machine_semantic
            for row in db.exec(SqlBuilder.select.table(GlobalCardRelationshipType)).all()
            if row.is_system_default
        }
        inserted = 0
        for definition in defaults:
            if definition["machine_semantic"] not in existing:
                db.insert(GlobalCardRelationshipType(**definition, is_system_default=True))
                inserted += 1
        return {"inserted": inserted}
