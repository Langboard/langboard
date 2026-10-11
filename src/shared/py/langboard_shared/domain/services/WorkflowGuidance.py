"""Compose explicit stage and column guidance without interpreting display names."""

from ...core.db import DbSession, SqlBuilder
from ..models import WorkflowStageDefinition


def compose_workflow_guidance(stage_description, column_description):
    return "\n\n".join(text for text in (stage_description, column_description) if text)


def column_workflow_contexts(columns):
    """Resolve all referenced definitions in one query, regardless of column count."""
    keys = {column.workflow_stage for column in columns if column.workflow_stage}
    stages = {}
    if keys:
        with DbSession.use(readonly=True) as db:
            stages = {
                stage.key: stage
                for stage in db.exec(
                    SqlBuilder.select.table(WorkflowStageDefinition).where(WorkflowStageDefinition.key.in_(keys))
                ).all()
            }
    result = {}
    for column in columns:
        stage = stages.get(column.workflow_stage)
        stage_description = stage.description if stage else ""
        result[column.id] = {
            "workflow_stage": column.workflow_stage,
            "workflow_stage_status": "active" if stage and stage.is_active else "inactive" if stage else "unassigned",
            "workflow_stage_description": stage_description,
            "column_description": column.description,
            "workflow_guidance": compose_workflow_guidance(stage_description, column.description),
        }
    return result
