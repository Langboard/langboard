"""Bounded cron checks over explicit grants; the native command owns source access."""

import asyncio
from datetime import timedelta, timezone
from uuid import uuid4
from sqlalchemy import and_, func, or_, select, update
from ...core.db import DbSession, SqlBuilder
from ...core.types import SafeDateTime
from ...domain.models import Card, Project, ProjectColumn, WorkflowStageDefinition
from ...domain.models.CompactorBoardGrant import CompactorBoardGrant
from ...domain.models.WorkflowCompactionSchedule import WorkflowCompactionSchedule
from ...domain.services import DomainService
from ...domain.services.AppGovernance import AppGovernanceDenied
from ...domain.services.CompactorBoardAuthority import CompactorAuthorityError
from ...domain.services.factory.CardCommentCompactionService import CommentCompactionError
from ...Env import Env


MAX_RUNNING = 2


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _due(profile_id, now, limit):
    state = WorkflowCompactionSchedule
    with DbSession.use(readonly=False) as db:
        return db.exec(SqlBuilder.select.tables(Card.project_id, Card.id)
            .join(Project, Project.id == Card.project_id)
            .join(ProjectColumn, and_(ProjectColumn.id == Card.project_column_id, ProjectColumn.project_id == Card.project_id))
            .join(WorkflowStageDefinition, WorkflowStageDefinition.key == ProjectColumn.workflow_stage)
            .join(CompactorBoardGrant, and_(CompactorBoardGrant.project_id == Card.project_id,
                                           CompactorBoardGrant.internal_bot_id == profile_id))
            .outerjoin(state, and_(state.card_id == Card.id, state.profile_id == profile_id))
            .where(Card.deleted_at.is_(None), Card.archived_at.is_(None), Card.visibility.in_(["INTERNAL", "SHARED"]),
                   Card.source_type.is_(None), Project.deleted_at.is_(None),
                   ProjectColumn.deleted_at.is_(None), ProjectColumn.is_archive == False,  # noqa: E712
                   WorkflowStageDefinition.is_active == True,  # noqa: E712
                   CompactorBoardGrant.can_read == True,  # noqa: E712
                   or_(and_(WorkflowStageDefinition.continuous_effects["auto_compact"].as_boolean().is_(True),
                            CompactorBoardGrant.can_compact.is_(True)),
                       and_(WorkflowStageDefinition.continuous_effects["auto_archive"].as_boolean().is_(True),
                            WorkflowStageDefinition.counts_as_completed.is_(True), CompactorBoardGrant.can_archive.is_(True))),
                   or_(state.id.is_(None), state.next_check_at <= now))
            .order_by(func.coalesce(state.next_check_at, Card.created_at), Card.id).limit(limit)).all()


def _claim(service, project_id, profile_id, card_id, now, *, effect="compact"):
    # Reuse the authority lock order. The profile lock also serializes its capacity check.
    with DbSession.atomic() as db:
        _, _, card, _ = service._automatic_context(db, project_id, profile_id, card_id, effect=effect)
        column = db.exec(SqlBuilder.select.table(ProjectColumn).where(ProjectColumn.id == card.project_column_id)).first()
        stage = db.exec(SqlBuilder.select.table(WorkflowStageDefinition).where(WorkflowStageDefinition.key == column.workflow_stage)).first()
        interval = stage.continuous_effects.get("interval_seconds", 300)
        if type(interval) is not int or not 60 <= interval <= 86400:
            raise CommentCompactionError("policy_changed")
        state = db.exec(SqlBuilder.select.table(WorkflowCompactionSchedule).where(
            WorkflowCompactionSchedule.card_id == card_id, WorkflowCompactionSchedule.profile_id == profile_id,
        ).execution_options(populate_existing=True).with_for_update()).first()
        if state and (_utc(state.next_check_at) > now or (state.lease_expires_at and _utc(state.lease_expires_at) > now)):
            return None
        active, = db.exec(select(func.count()).select_from(WorkflowCompactionSchedule).where(
            WorkflowCompactionSchedule.profile_id == profile_id, WorkflowCompactionSchedule.lease_expires_at > now,
        )).first()
        if active >= MAX_RUNNING:
            return None
        token, expires = uuid4().hex, now + timedelta(seconds=min(3600, Env.AI_REQUEST_TIMEOUT + 60))
        if state is None:
            state = WorkflowCompactionSchedule(card_id=card_id, profile_id=profile_id, next_check_at=expires)
            db.insert(state)
        state.lease_token, state.lease_expires_at = token, expires
        state.next_check_at = max(expires, now + timedelta(seconds=interval))
        state.last_status = "running"
        db.update(state)
        return state.id, token, interval


def _finish(claim, started, status, now):
    identity, token, interval = claim
    with DbSession.atomic() as db:
        return db.exec(update(WorkflowCompactionSchedule).where(
            WorkflowCompactionSchedule.id == identity, WorkflowCompactionSchedule.lease_token == token,
            WorkflowCompactionSchedule.lease_expires_at > now,
        ).values(lease_token=None, lease_expires_at=None, next_check_at=max(now, started + timedelta(seconds=interval)),
                 last_status=status, updated_at=now)) == 1


async def run_due_compactions(*, limit=5, now=None):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Continuous compaction limit must be between 1 and 100")
    if DbSession.has_active_transaction():
        raise CommentCompactionError("transaction_not_supported")
    now = _utc(now or SafeDateTime.now())
    service = DomainService()
    binding = service.internal_bot.get_comment_compaction_binding()
    if binding is None:
        return 0
    processed = 0
    for project_id, card_id in _due(binding.id, now, limit):
        started = max(now, SafeDateTime.now())
        effect = "compact"
        try:
            claim = _claim(service.card_comment_compaction, project_id, binding.id, card_id, started)
        except CommentCompactionError:
            effect = "archive"
            try:
                claim = _claim(service.card_comment_compaction, project_id, binding.id, card_id, started, effect=effect)
            except CommentCompactionError:
                continue
        if claim is None:
            continue
        status = "failed"
        try:
            if effect == "compact":
                result = await service.card_comment_compaction.generate_automatic(project_id, binding.id, card_id, retry=True)
                status = result["status"]
            if effect == "archive" or status in {"completed", "skipped"}:
                try:
                    result = service.card.archive_automatically(project_id, binding.id, card_id)
                except (CompactorAuthorityError, AppGovernanceDenied):
                    if effect == "archive":
                        status = "ineligible"
                else:
                    if effect == "archive" or result["status"] == "completed":
                        status = result["status"]
        except CommentCompactionError:
            status = "ineligible"
        except asyncio.CancelledError:
            status = "cancelled"
            raise
        finally:
            _finish(claim, started, status, max(now, SafeDateTime.now()))
        processed += 1
    return processed


if __name__ == "__main__":
    asyncio.run(run_due_compactions())
