"""Persist profile receipts in the same transaction as each compaction attempt."""

from datetime import timezone
from ...core.db import SqlBuilder
from ...core.types import SafeDateTime
from ..models import Card, InternalBot, ProjectColumn, WorkflowEffectRun, WorkflowStageDefinition


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _key(record, attempt):
    return f"comment_compaction:{record.get_uid()}:{attempt}"


def history_lease_expired(run, now):
    return run.status == "running" and run.lease_expires_at is not None and _utc(run.lease_expires_at) <= _utc(now)


def _finish(db, record, attempt, status, error_code, now):
    run = db.exec(SqlBuilder.select.table(WorkflowEffectRun).where(
        WorkflowEffectRun.execution_key == _key(record, attempt),
    ).with_for_update()).first()
    if run is None or run.status != "running":
        return
    run.status, run.error_code, run.finished_at = status, error_code, now
    run.lease_expires_at = None
    run.duration_ms = max(0, int((_utc(now) - _utc(run.started_at)).total_seconds() * 1000))
    run.result = {"compacted": int(status == "succeeded")}
    db.update(run)


def start_compaction_history(db, record, now):
    if record.attempt > 1:
        _finish(db, record, record.attempt - 1, "uncertain", "lease_expired", now)
    card = db.exec(SqlBuilder.select.table(Card).where(Card.id == record.card_id)).first()
    if card is None or card.deleted_at or card.is_linked_resource:
        return
    column = db.exec(SqlBuilder.select.table(ProjectColumn).where(
        ProjectColumn.id == card.project_column_id, ProjectColumn.project_id == card.project_id,
        ProjectColumn.deleted_at.is_(None), ProjectColumn.is_archive.is_(False),
    )).first()
    if column is None or not column.workflow_stage:
        return
    stage = db.exec(SqlBuilder.select.table(WorkflowStageDefinition).where(
        WorkflowStageDefinition.key == column.workflow_stage,
    )).first()
    profile = db.exec(SqlBuilder.select.table(InternalBot).where(InternalBot.id == record.internal_bot_id)).first()
    if stage is None or profile is None:
        return
    db.insert(WorkflowEffectRun(
        stage_id=stage.id, stage_key=stage.key, stage_revision=stage.edit_revision(),
        project_id=card.project_id, card_id=card.id,
        actor_id=record.user_id if record.user_id is not None else profile.id,
        actor_type="user" if record.user_id is not None else "profile",
        profile_id=profile.id, profile_name=profile.display_name, execution_key=_key(record, record.attempt),
        source="manual" if record.user_id is not None else "continuous", status="running", effects=["compact_comments"],
        lease_expires_at=record.lease_expires_at,
        started_at=now, finished_at=now, duration_ms=0, result={},
    ))


def finish_compaction_history(db, record, attempt, status, error_code=None, *, now=None):
    if record.attempt != attempt:
        return
    _finish(db, record, attempt, status, error_code, now or SafeDateTime.now())
