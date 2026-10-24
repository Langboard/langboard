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
from ...helpers import InfraHelper


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
    bot_processed = await run_due_workflow_bots(limit=limit, now=now)
    service = DomainService()
    binding = service.internal_bot.get_comment_compaction_binding()
    if binding is None:
        return bot_processed
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
    return processed + bot_processed



def _eligible_workflow_schedule(db, binding, card, now):
    from ...domain.models import Bot, BotSchedule, CardBotSchedule
    from ...domain.models.BotSchedule import BotScheduleRunningType, BotScheduleStatus

    row = db.exec(SqlBuilder.select.tables(Bot, BotSchedule, CardBotSchedule)
        .join(BotSchedule, BotSchedule.bot_id == Bot.id)
        .join(CardBotSchedule, CardBotSchedule.bot_schedule_id == BotSchedule.id)
        .where(Bot.id == InfraHelper.convert_id(binding["bot_uid"]), Bot.deleted_at.is_(None),
               BotSchedule.id == InfraHelper.convert_id(binding["schedule_uid"]),
               CardBotSchedule.card_id == card.id, BotSchedule.status == BotScheduleStatus.Started,
               BotSchedule.running_type.in_([BotScheduleRunningType.Infinite, BotScheduleRunningType.Duration]))
        .execution_options(populate_existing=True)).first()
    if row is None:
        return None
    bot, schedule, association = row
    if schedule.running_type == BotScheduleRunningType.Duration and (
        not schedule.start_at or not schedule.end_at or _utc(schedule.start_at) > _utc(now) or _utc(schedule.end_at) <= _utc(now)
    ):
        return None
    return bot, schedule, association


async def run_workflow_bound_schedule(bot, schedule, association, card):
    """Native cron and workflow cron share the same bound-card claim."""
    from ...domain.models.BotSchedule import BotScheduleRunningType

    if schedule.running_type not in {BotScheduleRunningType.Infinite, BotScheduleRunningType.Duration}:
        return False
    with DbSession.use(readonly=False) as db:
        column = db.exec(SqlBuilder.select.table(ProjectColumn).where(ProjectColumn.id == card.project_column_id)).first()
        stage = db.exec(SqlBuilder.select.table(WorkflowStageDefinition).where(
            WorkflowStageDefinition.key == column.workflow_stage,
        )).first() if column and column.workflow_stage else None
        bindings = stage.continuous_effects.get("bots", []) if stage and stage.is_active else []
    for binding in bindings:
        if binding["bot_uid"] == bot.get_uid() and binding["schedule_uid"] == schedule.get_uid():
            claim = _claim_workflow_bot(stage.id, card.id, binding, "bot_interval", SafeDateTime.now())
            if claim:
                await _dispatch_workflow_bot(claim, binding)
            return True
    return False

def _policy_actor_authorized(db, actor, project):
    from ...domain.models import ProjectAssignedUser, ProjectRole
    from ...domain.models.ProjectRole import ProjectRoleAction

    if actor.is_admin or project.owner_id == actor.id:
        return True
    member = db.exec(SqlBuilder.select.table(ProjectAssignedUser).where(
        ProjectAssignedUser.project_id == project.id, ProjectAssignedUser.user_id == actor.id,
    ).execution_options(populate_existing=True)).first()
    role = db.exec(SqlBuilder.select.table(ProjectRole).where(
        ProjectRole.project_id == project.id, ProjectRole.user_id == actor.id,
    ).execution_options(populate_existing=True)).first()
    return member is not None and role is not None and role.is_granted(ProjectRoleAction.Read)


def _claim_workflow_bot(stage_id, card_id, binding, source, now, *, actor_id=None, residency=None, condition=None, event_key=None):
    """Serialize each card's claims before external I/O; uncertain runs are never replayed."""
    from ...domain.models import Bot, User, WorkflowEffectRun
    from ...domain.services.factory.WorkflowStageService import WorkflowStageService

    with DbSession.atomic() as db:
        card = db.exec(SqlBuilder.select.table(Card).where(Card.id == card_id)
                       .execution_options(populate_existing=True).with_for_update()).first()
        if card is None or card.deleted_at or card.archived_at or card.is_linked_resource or card.visibility not in {"INTERNAL", "SHARED"}:
            return None
        column = db.exec(SqlBuilder.select.table(ProjectColumn).where(
            ProjectColumn.id == card.project_column_id, ProjectColumn.project_id == card.project_id,
        )).first()
        stage = db.exec(SqlBuilder.select.table(WorkflowStageDefinition).where(
            WorkflowStageDefinition.id == stage_id,
        ).execution_options(populate_existing=True)).first()
        project = db.exec(SqlBuilder.select.table(Project).where(Project.id == card.project_id)).first()
        if (stage is None or not stage.is_active or column is None or column.deleted_at or column.is_archive
                or column.workflow_stage != stage.key or project is None or project.deleted_at):
            return None
        continuous = source == "bot_interval"
        configured = WorkflowStageService.normalize_bot_bindings(
            stage.continuous_effects.get("bots", []) if continuous else (stage.policy_bots if source == "bot_policy" else stage.entry_bots),
            continuous=continuous, policy=source == "bot_policy",
        )
        if binding not in configured:
            return None
        if continuous and _eligible_workflow_schedule(db, binding, card, now) is None:
            return None
        if residency is not None and (card.workflow_stage_entered_key != stage.key
                                      or card.workflow_stage_entered_at is None
                                      or _utc(card.workflow_stage_entered_at).isoformat() != residency):
            return None
        bot = db.exec(SqlBuilder.select.table(Bot).where(Bot.id == InfraHelper.convert_id(binding["bot_uid"]))).first()
        actor = db.exec(SqlBuilder.select.table(User).where(User.id == (actor_id or project.owner_id))).first()
        if bot is None or bot.deleted_at or actor is None or actor.deleted_at or not actor.activated_at:
            return None
        if source == "bot_policy" and not _policy_actor_authorized(db, actor, project):
            return None
        previous = SqlBuilder.select.table(WorkflowEffectRun).where(
            WorkflowEffectRun.stage_id == stage.id, WorkflowEffectRun.card_id == card.id,
            WorkflowEffectRun.actor_id == bot.id, WorkflowEffectRun.source == source,
        )
        if continuous:
            previous = previous.where(or_(WorkflowEffectRun.started_at > now - timedelta(seconds=binding["interval_seconds"]),
                and_(WorkflowEffectRun.status == "running", WorkflowEffectRun.lease_expires_at > now)))
        elif source == "bot_policy":
            previous = previous.where(WorkflowEffectRun.execution_key == event_key)
        else:
            previous = previous.where(WorkflowEffectRun.execution_key == f"entry:{stage.id}:{card.id}:{bot.id}:{residency}")
        if db.exec(previous).first() is not None:
            return None
        run = WorkflowEffectRun(
            stage_id=stage.id, stage_key=stage.key, stage_revision=stage.edit_revision(),
            project_id=project.id, card_id=card.id, actor_id=bot.id, actor_type="bot", source=source,
            status="running", effects=[f"bot:{bot.get_uid()}", condition or binding["condition"]],
            started_at=now, finished_at=now, duration_ms=0, result={},
            execution_key=(event_key if source == "bot_policy" else f"entry:{stage.id}:{card.id}:{bot.id}:{residency}" if not continuous else f"interval:{uuid4().hex}"),
            lease_expires_at=now + timedelta(seconds=Env.AI_REQUEST_TIMEOUT * (Env.AI_REQUEST_TRIALS + 1) + 60),
        )
        db.insert(run)
        return run, actor, project, card


async def _dispatch_workflow_bot(claim, binding, *, data=None):
    from time import monotonic_ns
    from ...domain.models import Bot, User
    from ...domain.models.bases import BotTriggerCondition
    from .utils import BotTaskDataHelper, BotTaskHelper
    from .utils.requests.BaseBotRequest import BotRequestOutcome

    run, actor, project, card = claim
    clock = monotonic_ns()
    status, counts, error = "skipped", {}, None
    try:
        # A queued claim is evidence, not lasting permission after a stage edit or move.
        with DbSession.use(readonly=False) as db:
            card = db.exec(SqlBuilder.select.table(Card).where(Card.id == card.id)
                           .execution_options(populate_existing=True)).first()
            column = db.exec(SqlBuilder.select.table(ProjectColumn).where(
                ProjectColumn.id == card.project_column_id,
            )).first() if card else None
            stage = db.exec(SqlBuilder.select.table(WorkflowStageDefinition).where(
                WorkflowStageDefinition.id == run.stage_id,
            ).execution_options(populate_existing=True)).first()
            actor = db.exec(SqlBuilder.select.table(User).where(User.id == actor.id)
                            .execution_options(populate_existing=True)).first()
            project = db.exec(SqlBuilder.select.table(Project).where(Project.id == run.project_id)
                              .execution_options(populate_existing=True)).first()
            bot = db.exec(SqlBuilder.select.table(Bot).where(Bot.id == run.actor_id)
                          .execution_options(populate_existing=True)).first()
            configured = (stage.continuous_effects.get("bots", []) if run.source == "bot_interval" else (stage.policy_bots if run.source == "bot_policy" else stage.entry_bots)) if stage else []
            if (card is None or card.deleted_at or card.archived_at or card.is_linked_resource
                    or card.visibility not in {"INTERNAL", "SHARED"}
                    or column is None or column.deleted_at or column.is_archive
                    or project is None or project.deleted_at or card.project_id != project.id
                    or column.project_id != project.id or actor is None or actor.deleted_at or not actor.activated_at
                    or bot is None or bot.deleted_at or stage is None or not stage.is_active
                    or column.workflow_stage != stage.key or binding not in configured):
                error = "workflow_binding_changed"
                return
            if run.source == "bot_entry" and (
                card.workflow_stage_entered_key != stage.key or card.workflow_stage_entered_at is None
                or run.execution_key != f"entry:{stage.id}:{card.id}:{bot.id}:{_utc(card.workflow_stage_entered_at).isoformat()}"
            ):
                error = "workflow_residency_changed"
                return
        if run.source == "bot_policy":
            with DbSession.use(readonly=False) as db:
                if not _policy_actor_authorized(db, actor, project):
                    error = "workflow_actor_revoked"
                    return
        if run.source == "bot_interval":
            from .BotScheduleTask import _run_scheduler
            with DbSession.use(readonly=False) as db:
                eligible = _eligible_workflow_schedule(db, binding, card, SafeDateTime.now())
            outcomes = (await _run_scheduler(*eligible, card, workflow=True) or []) if eligible else []
            counts = {outcome.value: outcomes.count(outcome) for outcome in BotRequestOutcome if outcome in outcomes}
            if BotRequestOutcome.Accepted in outcomes:
                status = "accepted"
            elif any(outcome in outcomes for outcome in (BotRequestOutcome.RetryableFailure, BotRequestOutcome.PermanentFailure)):
                status = "failed"
            return
        if run.source == "bot_policy" and (
            card.workflow_stage_entered_key != stage.key or card.workflow_stage_entered_at is None
            or run.execution_key.split(":", 2)[1] != _utc(card.workflow_stage_entered_at).isoformat().replace(":", "_")
        ):
            error = "workflow_residency_changed"
            return
        condition = BotTriggerCondition(run.effects[-1])
        # Selection never creates a Hook or bypasses its conditions/frozen state.
        # Native scope lookup uses replicas by default. This authorization reread
        # shares a short primary transaction, which closes before transport starts.
        with DbSession.atomic():
            bots = BotTaskHelper.get_scoped_bots(condition, project_id=project.id,
                                                 project_column_id=card.project_column_id, card_id=card.id)
        bots = [item for item in bots if item[0].get_uid() == binding["bot_uid"]]
        # Multiple matching scopes must not dispatch the same selected Bot twice.
        bots = bots[:1]
        if bots:
            data = dict(data) if data is not None else BotTaskDataHelper.create_card(actor, project, card)
            data["workflow_stage"] = {"key": run.stage_key, "source": run.source, "run_uid": run.get_uid()}
            outcomes = await BotTaskHelper.run(bots, condition, data, project, emit_webhook=False)
            counts = {outcome.value: outcomes.count(outcome) for outcome in BotRequestOutcome if outcome in outcomes}
            if BotRequestOutcome.Accepted in outcomes:
                status = "accepted"
            elif any(outcome in outcomes for outcome in (BotRequestOutcome.RetryableFailure, BotRequestOutcome.PermanentFailure)):
                status = "failed"
    except asyncio.CancelledError:
        status, error = "uncertain", "dispatch_cancelled"
        raise
    except Exception:
        status, error = "failed", "bot_dispatch_failed"
    finally:
        # Existing BotLog records retain transport detail; workflow history stores bounded outcomes.
        with DbSession.atomic() as db:
            run.status, run.result, run.error_code = status, counts, error
            run.finished_at = SafeDateTime.now()
            run.duration_ms = max(0, (monotonic_ns() - clock) // 1_000_000)
            run.lease_expires_at = None
            db.update(run)


async def run_due_workflow_bots(*, limit=5, now=None):
    from ...domain.models import CardBotSchedule, WorkflowEffectRun
    from ...domain.services.factory.WorkflowStageService import WorkflowStageService

    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Workflow bot limit must be between 1 and 100")
    if DbSession.has_active_transaction():
        raise ValueError("Workflow bot dispatch requires a committed transaction")
    now = _utc(now or SafeDateTime.now())
    processed = 0
    with DbSession.use(readonly=False) as db:
        stages = db.exec(SqlBuilder.select.table(WorkflowStageDefinition).where(WorkflowStageDefinition.is_active.is_(True))).all()
    for stage in stages:
        bindings = WorkflowStageService.normalize_bot_bindings(stage.continuous_effects.get("bots", []), continuous=True)
        for binding in bindings:
            bot_id = InfraHelper.convert_id(binding["bot_uid"])
            with DbSession.use(readonly=False) as db:
                recent = select(WorkflowEffectRun.id).where(
                    WorkflowEffectRun.stage_id == stage.id, WorkflowEffectRun.card_id == Card.id,
                    WorkflowEffectRun.actor_id == bot_id, WorkflowEffectRun.source == "bot_interval",
                    WorkflowEffectRun.started_at > now - timedelta(seconds=binding["interval_seconds"]),
                ).exists()
                card_ids = db.exec(SqlBuilder.select.column(Card.id)
                    .join(ProjectColumn, ProjectColumn.id == Card.project_column_id)
                    .join(CardBotSchedule, CardBotSchedule.card_id == Card.id)
                    .where(CardBotSchedule.bot_schedule_id == InfraHelper.convert_id(binding["schedule_uid"]),
                           ProjectColumn.workflow_stage == stage.key, Card.deleted_at.is_(None),
                           Card.archived_at.is_(None), Card.visibility.in_(["INTERNAL", "SHARED"]),
                           Card.source_type.is_(None), ~recent)
                    .order_by(Card.id).limit(limit - processed)).all()
            for card_id in card_ids:
                claim = _claim_workflow_bot(stage.id, card_id, binding, "bot_interval", now)
                if claim:
                    await _dispatch_workflow_bot(claim, binding)
                    processed += 1
                if processed >= limit:
                    return processed
    return processed


if __name__ == "__main__":
    asyncio.run(run_due_compactions())
