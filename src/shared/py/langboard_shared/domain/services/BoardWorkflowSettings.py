"""Native built-in workflow metadata and column configuration."""

import hashlib
import json
import re
from ...core.db import DbSession, SqlBuilder
from ...publishers import ProjectColumnPublisher
from ..models import ProjectColumn, WorkflowStageDefinition
from .AppGovernance import AppGovernanceConflict, AppGovernanceDenied, _actor, _authorized_board, _current
from .WorkflowGuidance import compose_workflow_guidance


def _translations(value):
    if not isinstance(value, dict) or len(value) > 32:
        raise ValueError("Invalid workflow translations")
    for language, fields in value.items():
        if not isinstance(language, str) or not re.fullmatch(r"[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", language):
            raise ValueError("Invalid workflow language")
        if (
            not isinstance(fields, dict)
            or set(fields) - {"name", "description"}
            or any(
                not isinstance(text, str) or len(text) > (100 if key == "name" else 4000)
                for key, text in fields.items()
            )
        ):
            raise ValueError("Invalid workflow translation fields")
    return value


def _revision(column):
    payload = [
        int(column.id),
        column.name,
        column.description,
        column.translations,
        column.workflow_stage,
        column.is_archive,
    ]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _column_response(column, stage):
    return {
        "column_uid": column.get_uid(),
        "revision": _revision(column),
        "description": column.description,
        "translations": column.translations,
        "workflow_stage": column.workflow_stage,
        "workflow_guidance": compose_workflow_guidance(stage.description if stage else "", column.description),
    }


def read_column_workflow(actor, project_id, column_id):
    with DbSession.atomic() as db:
        actor = _actor(db, actor, for_update=False)
        project = _authorized_board(db, actor, project_id, for_update=False)
        column = _current(db, ProjectColumn, column_id, for_update=False)
        if column is None or column.project_id != project.id or column.deleted_at:
            raise AppGovernanceDenied()
        stage = (
            db.exec(
                SqlBuilder.select.table(WorkflowStageDefinition).where(
                    WorkflowStageDefinition.key == column.workflow_stage
                )
            ).first()
            if column.workflow_stage
            else None
        )
        return _column_response(column, stage)


def save_column_workflow(actor, project_id, column_id, stage_key, description, translations, expected_revision):
    if stage_key is not None and (not isinstance(stage_key, str) or len(stage_key) > 32):
        raise ValueError("Invalid stage key")
    if not isinstance(description, str) or len(description) > 4000:
        raise ValueError("Invalid column description")
    _translations(translations)
    with DbSession.atomic() as db:
        actor = _actor(db, actor)
        project = _authorized_board(db, actor, project_id)
        column = _current(db, ProjectColumn, column_id)
        if column is None or column.project_id != project.id or column.deleted_at:
            raise AppGovernanceDenied()
        if _revision(column) != expected_revision:
            raise AppGovernanceConflict()
        stage = (
            db.exec(
                SqlBuilder.select.table(WorkflowStageDefinition)
                .where(WorkflowStageDefinition.key == stage_key)
                .with_for_update()
            ).first()
            if stage_key
            else None
        )
        if stage_key and (stage is None or not stage.is_active or not stage.is_builtin or column.is_archive):
            raise ValueError("Choose an active built-in stage for a nonarchive column")
        column.workflow_stage, column.description, column.translations = stage_key, description, translations
        db.update(column)
        db.after_commit(lambda: ProjectColumnPublisher.metadata_changed(project, column))
        return _column_response(column, stage)
