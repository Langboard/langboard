"""Current direct prerequisites; names and unreadable card details are never projected."""

import hashlib
import json
from sqlalchemy import and_, select
from ..models import Card, CardRelationship, GlobalCardRelationshipType, Project, ProjectColumn, WorkflowStageDefinition


def card_dependency_state(db, project_id, card_id):
    prerequisite = Card.__table__.alias("prerequisite")
    column = ProjectColumn.__table__.alias("prerequisite_column")
    project = Project.__table__.alias("prerequisite_project")
    stage = WorkflowStageDefinition.__table__.alias("prerequisite_stage")
    satisfied = (
        and_(
            prerequisite.c.id.is_not(None),
            prerequisite.c.deleted_at.is_(None),
            project.c.id.is_not(None),
            project.c.deleted_at.is_(None),
            prerequisite.c.archived_at.is_(None),
            prerequisite.c.source_type.is_(None),
            column.c.id.is_not(None),
            column.c.deleted_at.is_(None),
            column.c.project_id == prerequisite.c.project_id,
            column.c.is_archive.is_(False),
            stage.c.counts_as_completed.is_(True),
        )
        .is_(True)
        .label("satisfied")
    )
    statement = (
        select(
            CardRelationship.id,
            CardRelationship.updated_at,
            GlobalCardRelationshipType.id,
            GlobalCardRelationshipType.updated_at,
            prerequisite.c.id,
            prerequisite.c.updated_at,
            project.c.updated_at,
            column.c.updated_at,
            stage.c.updated_at,
            satisfied,
        )
        .select_from(CardRelationship)
        .join(
            GlobalCardRelationshipType,
            CardRelationship.relationship_type_id == GlobalCardRelationshipType.id,
        )
        .outerjoin(prerequisite, prerequisite.c.id == CardRelationship.card_id_parent)
        .outerjoin(project, project.c.id == prerequisite.c.project_id)
        .outerjoin(
            column,
            column.c.id == prerequisite.c.project_column_id,
        )
        .outerjoin(stage, stage.c.key == column.c.workflow_stage)
        .where(
            CardRelationship.card_id_child == card_id,
            GlobalCardRelationshipType.machine_semantic == "blocks",
        )
        .order_by(CardRelationship.id)
    )
    rows = db.exec(statement).all()
    digest = hashlib.sha256(
        json.dumps([list(row) for row in rows], default=str, separators=(",", ":")).encode()
    ).hexdigest()
    blocked = sum(not row[-1] for row in rows)
    return {"state": "blocked" if blocked else "clear", "direct_blocker_count": blocked, "revision": digest}
