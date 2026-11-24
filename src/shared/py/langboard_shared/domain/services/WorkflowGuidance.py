"""Compose explicit stage and column guidance without interpreting display names."""

from ...core.db import DbSession, SqlBuilder
from ..models import WorkflowStageDefinition


def compose_workflow_guidance(stage_description, column_description):
    parts = []
    if stage_description.strip():
        parts.append(f"Workflow stage:\n{stage_description}")
    if column_description.strip():
        parts.append(f"Column:\n{column_description}")
    return "\n\n".join(parts)


def column_workflow_contexts(columns, *, stages=None):
    """Resolve all referenced definitions in one query, regardless of column count."""
    keys = {column.workflow_stage for column in columns if column.workflow_stage}
    if stages is None:
        stages = {}
    else:
        keys = set()
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
            "workflow_stage_status": "active" if stage and stage.is_active else "inactive" if stage else "missing" if column.workflow_stage else "unclassified",
            "workflow_counts_as_completed": stage.counts_as_completed if stage else None,
            "workflow_stage_description": stage_description,
            "column_description": column.description,
            "workflow_guidance": compose_workflow_guidance(stage_description, column.description),
        }
    return result
