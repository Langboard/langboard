"""Common durable rule execution, drained by the existing bounded workflow cron."""
import asyncio
from datetime import timedelta
from sqlalchemy import func, select
from ..core.db import DbSession, SqlBuilder
from ..core.types import SafeDateTime
from ..domain.constants.DomainEventType import DomainEventType
from ..domain.models import (
    Bot,
    Card,
    Project,
    ProjectColumn,
    User,
    WorkflowActionRun,
    WorkflowRule,
    WorkflowRuleAction,
    WorkflowRuleCondition,
    WorkflowRuleRun,
    WorkflowStageDefinition,
)
from ..domain.services import DomainService
from ..domain.services.factory.WorkflowRuleService import (
    CAUSE_RUN,
    COMPACTION_CONDITIONS,
    NATIVE_ACTIONS,
    admit,
    rows,
    utc,
)
from ..Env import Env
from ..helpers import InfraHelper


MAX_RUNNING = 2
INTERVAL_SCAN_PAGE_SIZE = 100


def _current(db, run):
    """Re-read current revision, residency, audience and actor authority at each action."""
    card = db.exec(SqlBuilder.select.table(Card).where(Card.id == run.card_id)
                   .execution_options(populate_existing=True).with_for_update()).first()
    stage = db.exec(SqlBuilder.select.table(WorkflowStageDefinition).where(WorkflowStageDefinition.id == run.stage_id)
                    .execution_options(populate_existing=True).with_for_update()).first()
    rule = db.exec(SqlBuilder.select.table(WorkflowRule).where(WorkflowRule.id == run.rule_id)
                   .execution_options(populate_existing=True).with_for_update()).first()
    project = db.exec(SqlBuilder.select.table(Project).where(Project.id == run.project_id)
                      .execution_options(populate_existing=True)).first()
    column = db.exec(SqlBuilder.select.table(ProjectColumn).where(ProjectColumn.id == card.project_column_id)
                     .execution_options(populate_existing=True)).first() if card else None
    if (card is None or card.deleted_at or card.archived_at or card.is_linked_resource
            or card.visibility not in {"INTERNAL", "SHARED"} or project is None or project.deleted_at
            or card.project_id != project.id or column is None or column.deleted_at or column.is_archive
            or column.project_id != project.id or stage is None or column.workflow_stage != stage.key
            or rule is None or not rule.is_enabled or rule.retired or rule.revision != run.rule_revision
            or stage.edit_revision() != run.stage_revision or card.workflow_stage_entered_key != stage.key
            or card.workflow_stage_entered_at is None or utc(card.workflow_stage_entered_at).isoformat() != run.residency):
        raise ValueError("workflow_context_changed")
    if not stage.is_active and not (run.event_type == "stage_entered" and all(
            action.kind in NATIVE_ACTIONS for action in rows(db, WorkflowRuleAction, rule_id=rule.id))):
        raise ValueError("workflow_stage_inactive")
    actor = None
    if run.actor_id is not None:
        from ..domain.models import ProjectAssignedUser, ProjectRole
        from ..domain.models.ProjectRole import ProjectRoleAction
        actor = db.exec(SqlBuilder.select.table(User).where(User.id == run.actor_id)
                        .execution_options(populate_existing=True)).first()
        if actor is None or actor.deleted_at or not actor.activated_at:
            raise ValueError("workflow_actor_revoked")
        if not actor.is_admin and project.owner_id != actor.id:
            member = rows(db, ProjectAssignedUser, project_id=project.id, user_id=actor.id)
            role = db.exec(SqlBuilder.select.table(ProjectRole).where(
                ProjectRole.project_id == project.id, ProjectRole.user_id == actor.id,
            ).execution_options(populate_existing=True)).first()
            if not member or role is None or not role.is_granted(ProjectRoleAction.Read):
                raise ValueError("workflow_actor_revoked")
    return rule, stage, project, card, column, actor


def _conditions(db, service, run, context, now):
    rule, _, project, card, _, actor = context
    conditions = sorted(rows(db, WorkflowRuleCondition, rule_id=rule.id), key=lambda c: c.order)
    volume = None
    checks = []
    for condition in conditions:
        kind = condition.kind
        if kind in COMPACTION_CONDITIONS:
            if volume is None:
                from ..domain.models import CardCommentCompaction
                from ..domain.services.CompactorBoardAuthority import require_compactor_card
                profile = service.internal_bot.get_comment_compaction_binding()
                if profile is None:
                    raise ValueError("compactor_unavailable")
                _, profile, card = require_compactor_card(db, project.id, profile.id, card.id, action="compact")
                covered = set()
                for row in rows(db, CardCommentCompaction, card_id=card.id, status="completed"):
                    block = service.card_comment_compaction._completed_block(db, project, card, row, profile, {"status": "completed", "uid": row.get_uid()})
                    if block["status"] == "completed":
                        covered.update(block["evidence_refs"])
                volume = service.card_comment_compaction._uncovered_comment_volume(db, project, card, covered)
            actual = volume[{"uncompacted_characters": "characters", "uncompacted_comments": "comments", "uncompacted_turns": "turns"}[kind]]
        elif kind == "stage_residency_seconds":
            actual = (utc(now) - utc(card.workflow_stage_entered_at)).total_seconds()
        elif kind == "actor_is_not_author":
            actual = actor is not None and actor.id != card.created_by_user_id
        elif kind in {"has_unfinished_items", "timers_stopped"}:
            total, completed, started, paused = service.card.repo.checkitem.get_work_state_counts([card.id]).get(card.id, (0, 0, 0, 0))
            actual = total != completed if kind == "has_unfinished_items" else not (started or paused)
        elif kind == "approvals_clear":
            from ..domain.services.CardApprovalGate import pending_card_approvals
            actual = not pending_card_approvals([card.id]).get(int(card.id), 0)
        elif kind == "dependencies_clear":
            from ..domain.services.CardDependencyState import card_dependency_state
            actual = card_dependency_state(db, project.id, card.id)["state"] == "clear"
        else:
            raise ValueError("unsupported_condition")
        checks.append({"kind": kind, "matched": actual >= condition.value if condition.operator == "gte" else actual == condition.value})
    return checks, (all(item["matched"] for item in checks) if rule.condition_match == "all" else any(item["matched"] for item in checks))


def _finish_action(db, claim, status, result=None, error=None):
    claim.status, claim.result, claim.error_code = status, result or {}, error
    claim.finished_at, claim.lease_expires_at = SafeDateTime.now(), None
    claim.attempts = [*claim.attempts, {"attempt": claim.attempt, "status": status,
        "started_at": claim.started_at.isoformat() if claim.started_at else None,
        "finished_at": claim.finished_at.isoformat(), "error_code": error}]
    db.update(claim)


def _native(db, service, action, context):
    _, stage, project, card, column, actor = context
    if actor is None:
        raise ValueError("workflow_actor_missing")
    if action.kind in {"checkitems.complete", "timers.stop"}:
        from ..domain.models import ProjectRole
        from ..domain.models.ProjectRole import ProjectRoleAction
        if not actor.is_admin and project.owner_id != actor.id:
            role = db.exec(SqlBuilder.select.table(ProjectRole).where(
                ProjectRole.project_id == project.id, ProjectRole.user_id == actor.id,
            ).execution_options(populate_existing=True).with_for_update()).first()
            if role is None or not role.is_granted(ProjectRoleAction.CardUpdate):
                raise ValueError("workflow_actor_revoked")
        return service.checkitem.complete_unchecked_by_card(actor, project, card, complete=action.kind == "checkitems.complete")
    if action.kind == "author.notify":
        return {"notified": int(service.notification.notify_workflow_stage_entered(actor, project, card, column, stage.key))}
    raise ValueError("unsupported_native_action")


def execute_atomic_prefix(db, run, service):
    """Native entry effects commit with the move. External actions stay queued."""
    context = _current(db, run)
    run.condition_results, matched = _conditions(db, service, run, context, SafeDateTime.now())
    claims = {claim.action_id: claim for claim in rows(db, WorkflowActionRun, rule_run_id=run.id)}
    previous = None
    for action in sorted(rows(db, WorkflowRuleAction, rule_id=run.rule_id), key=lambda a: a.order):
        claim = claims[action.id]
        if not matched:
            _finish_action(db, claim, "skipped", error="conditions_not_met")
            continue
        if action.kind not in NATIVE_ACTIONS:
            break
        if action.requires_previous_success and (previous is None or previous.status != "succeeded"):
            _finish_action(db, claim, "skipped", error="predecessor_not_succeeded")
        elif claim.status == "queued":
            claim.attempt += 1
            claim.started_at = SafeDateTime.now()
            _finish_action(db, claim, "succeeded", _native(db, service, action, context))
        previous = claim
    if not matched or all(c.status == "succeeded" for c in claims.values()):
        run.status, run.finished_at = ("succeeded" if matched else "skipped"), SafeDateTime.now()
    db.update(run)


def _eligible_schedule(db, action, card, now):
    from ..domain.models import BotSchedule, CardBotSchedule
    from ..domain.models.BotSchedule import BotScheduleRunningType, BotScheduleStatus
    row = db.exec(SqlBuilder.select.tables(Bot, BotSchedule, CardBotSchedule)
        .join(BotSchedule, BotSchedule.bot_id == Bot.id)
        .join(CardBotSchedule, CardBotSchedule.bot_schedule_id == BotSchedule.id)
        .where(Bot.id == action.executor_id, Bot.deleted_at.is_(None),
               BotSchedule.id == InfraHelper.convert_id(action.config["schedule_uid"]), CardBotSchedule.card_id == card.id,
               BotSchedule.status == BotScheduleStatus.Started,
               BotSchedule.running_type.in_([BotScheduleRunningType.Infinite, BotScheduleRunningType.Duration]))
        .execution_options(populate_existing=True)).first()
    if row is None:
        return None
    _, schedule, _ = row
    if schedule.running_type == BotScheduleRunningType.Duration and (not schedule.start_at or not schedule.end_at
            or utc(schedule.start_at) > utc(now) or utc(schedule.end_at) <= utc(now)):
        return None
    return row


def _event_data(run, actor, project, card):
    from .bots.utils import BotTaskDataHelper
    data = BotTaskDataHelper.create_card(actor, project, card)
    mapping = {"CardComment": "comment_uid", "CardAttachment": "attachment_uid", "Checkitem": "checkitem_uid", "Checklist": "checklist_uid"}
    for model, key in mapping.items():
        if run.event_context.get("refs", {}).get(model):
            data[key] = run.event_context["refs"][model]
    metadata = run.event_context.get("metadata", {})
    for key in ("reaction_type", "cardified_card_uid", "old_project_column_uid"):
        if key in metadata:
            data[key] = metadata[key]
    if metadata.get("old_project_column_uid"):
        with DbSession.use(readonly=False) as db:
            old_column = db.exec(SqlBuilder.select.table(ProjectColumn).where(
                ProjectColumn.id == InfraHelper.convert_id(metadata["old_project_column_uid"]))).first()
        if old_column is not None:
            data["old_project_column_name"] = old_column.name
            data["old_project_column_is_archive"] = old_column.is_archive
    data["workflow_rule"] = {"run_uid": run.get_uid(), "event_id": run.event_id, "rule_uid": InfraHelper.convert_uid(run.rule_id)}
    return data


async def _bot(service, run, action, context, claim):
    from .bots.utils import BotTaskHelper
    from .bots.utils.requests.BaseBotRequest import BotRequestOutcome
    _, _, project, card, _, actor = context
    if run.event_type == "interval":
        from .bots.BotScheduleTask import _run_scheduler
        with DbSession.atomic() as db:
            eligible = _eligible_schedule(db, action, card, SafeDateTime.now())
        outcomes = await _run_scheduler(*eligible, card, workflow=True, workflow_claim={"run_uid": run.get_uid(), "event_id": run.event_id, "idempotency_key": claim.idempotency_key}) if eligible else []
    else:
        event = DomainEventType.CardMoved if run.event_type == "stage_entered" else DomainEventType(run.event_type)
        with DbSession.atomic():
            bots = BotTaskHelper.get_scoped_bots(event, project_id=project.id, project_column_id=card.project_column_id, card_id=card.id)
        bots = [item for item in bots if item[0].id == action.executor_id][:1]
        data = _event_data(run, actor, project, card)
        data["workflow_rule"]["idempotency_key"] = claim.idempotency_key
        outcomes = await BotTaskHelper.run(bots, event, data, project, emit_webhook=False) if bots else []
    outcomes = outcomes or []
    result = {outcome.value: outcomes.count(outcome) for outcome in BotRequestOutcome if outcome in outcomes}
    if BotRequestOutcome.Uncertain in outcomes:
        return "uncertain", result
    if BotRequestOutcome.Accepted in outcomes:
        return "accepted", result
    if BotRequestOutcome.RetryableFailure in outcomes:
        return "retryable", result
    if BotRequestOutcome.PermanentFailure in outcomes:
        return "failed", result
    return "skipped", result


async def run_rule(run_id, *, now=None):
    if DbSession.has_active_transaction():
        raise ValueError("Workflow dispatch requires a committed transaction")
    evaluation_now = utc(now or SafeDateTime.now())
    service = DomainService()
    with DbSession.atomic() as db:
        run = db.exec(SqlBuilder.select.table(WorkflowRuleRun).where(WorkflowRuleRun.id == run_id).with_for_update()).first()
        if (run is None or run.status not in {"queued", "running"}
                or utc(run.next_attempt_at) > evaluation_now):
            return
        claims = rows(db, WorkflowActionRun, rule_run_id=run.id)
        # Recover transport receipts before context changes can close the run.
        # A live dispatcher still owns its lease; an expired one is ambiguous.
        if any(claim.status == "running" and claim.lease_expires_at
               and utc(claim.lease_expires_at) > utc(SafeDateTime.now()) for claim in claims):
            return
        for claim in claims:
            if claim.status == "running":
                _finish_action(db, claim, "uncertain", error="lease_expired")
        unmatched_status = "uncertain" if any(claim.status == "uncertain" for claim in claims) else "skipped"
        try:
            context = _current(db, run)
            run.condition_results, matched = _conditions(db, service, run, context, evaluation_now)
        except Exception as error:
            run.status, run.error_code, run.finished_at = unmatched_status, getattr(error, "code", str(error))[:64], SafeDateTime.now()
            for claim in rows(db, WorkflowActionRun, rule_run_id=run.id):
                if claim.status == "queued":
                    _finish_action(db, claim, "skipped", error=run.error_code)
            db.update(run)
            return
        if not matched:
            run.status, run.finished_at = unmatched_status, SafeDateTime.now()
            for claim in rows(db, WorkflowActionRun, rule_run_id=run.id):
                if claim.status == "queued":
                    _finish_action(db, claim, "skipped", error="conditions_not_met")
            db.update(run)
            return
        action_ids = [a.id for a in sorted(rows(db, WorkflowRuleAction, rule_id=run.rule_id), key=lambda a: a.order)]
        run.status = "running"
        db.update(run)
    previous_status = None
    for action_id in action_ids:
        with DbSession.atomic() as db:
            run = db.exec(SqlBuilder.select.table(WorkflowRuleRun).where(WorkflowRuleRun.id == run_id).with_for_update()).first()
            claim = db.exec(SqlBuilder.select.table(WorkflowActionRun).where(WorkflowActionRun.rule_run_id == run_id,
                WorkflowActionRun.action_id == action_id).with_for_update().execution_options(populate_existing=True)).first()
            if claim.status == "running":
                if claim.lease_expires_at and utc(claim.lease_expires_at) > utc(SafeDateTime.now()):
                    return
                # The transport may have received a request. Never blindly replay it.
                _finish_action(db, claim, "uncertain", error="lease_expired")
            if claim.status != "queued":
                previous_status = claim.status
                continue
            action = db.exec(SqlBuilder.select.table(WorkflowRuleAction).where(WorkflowRuleAction.id == action_id)).first()
            try:
                context = _current(db, run)
                if action is None:
                    raise ValueError("workflow_action_changed")
                if action.requires_previous_success and previous_status != "succeeded":
                    raise ValueError("predecessor_not_succeeded")
                if action.kind not in NATIVE_ACTIONS:
                    from ..core.db.DbEngine import DbEngine
                    if DbEngine.get_main_engine().dialect.name == "postgresql":
                        db.exec(select(func.pg_advisory_xact_lock(7104389251164)))
                    active, = db.exec(select(func.count()).select_from(WorkflowActionRun).where(
                        WorkflowActionRun.status == "running", WorkflowActionRun.lease_expires_at > SafeDateTime.now())).first()
                    if active >= MAX_RUNNING:
                        return
                if action.executor_kind == "compactor":
                    from ..domain.services.CompactorBoardAuthority import require_compactor_card
                    profile = service.internal_bot.get_comment_compaction_binding()
                    if profile is None:
                        raise ValueError("compactor_unavailable")
                    require_compactor_card(db, run.project_id, profile.id, run.card_id,
                                          action="compact" if action.kind == "comments.compact" else "archive")
                    claim.executor_id = profile.id
                claim.attempt += 1
                claim.started_at, claim.status = SafeDateTime.now(), "running"
                claim.lease_expires_at = claim.started_at + timedelta(seconds=Env.AI_REQUEST_TIMEOUT * (Env.AI_REQUEST_TRIALS + 1) + 60)
                db.update(claim)
                if action.kind in NATIVE_ACTIONS:
                    token = CAUSE_RUN.set(run.id)
                    try:
                        _finish_action(db, claim, "succeeded", _native(db, service, action, context))
                    finally:
                        CAUSE_RUN.reset(token)
                    previous_status = claim.status
                    continue
            except Exception as error:
                _finish_action(db, claim, "skipped", error=getattr(error, "code", str(error))[:64])
                previous_status = claim.status
                continue
        status, result, error_code = "uncertain", {}, None
        token = CAUSE_RUN.set(run.id)
        try:
            if action.kind == "bot.invoke":
                status, result = await _bot(service, run, action, context, claim)
            elif action.kind == "comments.compact":
                result = await service.card_comment_compaction.generate_automatic(run.project_id, claim.executor_id, run.card_id, rule_id=run.rule_id, retry=True)
                status = ("succeeded" if result["status"] == "completed" else "skipped" if result["status"] == "skipped"
                          else "retryable" if result.get("error_code") == "generation_failed"
                          else "uncertain" if result.get("error_code") == "provider_error" else "failed")
            elif action.kind == "card.archive":
                result = service.card.archive_automatically(run.project_id, claim.executor_id, run.card_id, rule_id=run.rule_id)
                status = "succeeded" if result["status"] == "completed" else "skipped"
        except asyncio.CancelledError:
            error_code = "dispatch_cancelled"
            raise
        except Exception as error:
            # Exceptions after external I/O are ambiguous, not automatic retries.
            error_code = getattr(error, "code", "dispatch_uncertain")[:64]
        finally:
            CAUSE_RUN.reset(token)
            with DbSession.atomic() as db:
                current = db.exec(SqlBuilder.select.table(WorkflowActionRun).where(WorkflowActionRun.id == claim.id).with_for_update()).first()
                if current.status == "running":
                    if status == "retryable":
                        status = "queued" if current.attempt < 3 else "failed"
                        error_code = "retryable_rejection" if status == "queued" else "retry_exhausted"
                    _finish_action(db, current, status, result, error_code)
                    if result.get("legacy_effect_run_uid"):
                        current.legacy_effect_run_id = InfraHelper.convert_id(result["legacy_effect_run_uid"])
                        db.update(current)
            previous_status = status
        if status == "queued":
            with DbSession.atomic() as db:
                pending = db.exec(SqlBuilder.select.table(WorkflowRuleRun).where(WorkflowRuleRun.id == run_id).with_for_update()).first()
                pending.status, pending.finished_at = "queued", None
                pending.next_attempt_at = SafeDateTime.now() + timedelta(seconds=min(300, 30 * 2 ** (claim.attempt - 1)))
                db.update(pending)
            return
    with DbSession.atomic() as db:
        run = db.exec(SqlBuilder.select.table(WorkflowRuleRun).where(WorkflowRuleRun.id == run_id).with_for_update()).first()
        statuses = {claim.status for claim in rows(db, WorkflowActionRun, rule_run_id=run.id)}
        run.status = next((status for status in ("uncertain", "failed", "accepted", "cancelled", "skipped") if status in statuses), "succeeded")
        run.finished_at = SafeDateTime.now()
        db.update(run)


def _interval_candidates(db, model, query):
    """Bound materialization without truncating an ineligible prefix.

    Freeze the scan's upper identity so concurrent inserts cannot extend it
    indefinitely. Admission still rechecks each candidate's current authority.
    """
    last = db.exec(query.order_by(model.id.desc()).limit(1)).first()
    if last is None:
        return
    upper, cursor = last.id, None
    while True:
        page = query.where(model.id <= upper)
        if cursor is not None:
            page = page.where(model.id > cursor)
        candidates = db.exec(page.order_by(model.id).limit(INTERVAL_SCAN_PAGE_SIZE)).all()
        if not candidates:
            return
        cursor = candidates[-1].id
        yield from candidates


def _admit_intervals(now, limit):
    admitted = 0
    with DbSession.atomic() as db:
        rules = SqlBuilder.select.table(WorkflowRule).where(WorkflowRule.trigger_kind == "interval",
            WorkflowRule.is_enabled.is_(True), WorkflowRule.retired.is_(False))
        for rule in _interval_candidates(db, WorkflowRule, rules):
            stage = db.exec(SqlBuilder.select.table(WorkflowStageDefinition).where(WorkflowStageDefinition.id == rule.stage_id,
                WorkflowStageDefinition.is_active.is_(True))).first()
            if stage is None:
                continue
            cards = (SqlBuilder.select.table(Card).join(ProjectColumn, ProjectColumn.id == Card.project_column_id)
                .where(ProjectColumn.workflow_stage == stage.key, Card.workflow_stage_entered_at.is_not(None),
                    Card.deleted_at.is_(None), Card.archived_at.is_(None)))
            for card in _interval_candidates(db, Card, cards):
                elapsed = (utc(now) - utc(card.workflow_stage_entered_at)).total_seconds()
                slot = int(elapsed // rule.interval_seconds)
                if slot < 1:
                    continue
                due = utc(card.workflow_stage_entered_at) + timedelta(seconds=slot * rule.interval_seconds)
                event_id = f"interval:{rule.id}:{card.id}:{utc(card.workflow_stage_entered_at).isoformat()}:{due.isoformat()}"
                # One current slot only. No unbounded catch-up after downtime.
                # Accepted closes the local transport dispatch, not remote execution.
                # Native recurring schedules have no execution-completion receipt;
                # later slots remain eligible while the accepted slot stays immutable.
                # Queued/running dispatches and ambiguous outcomes still block admission.
                outstanding = db.exec(SqlBuilder.select.table(WorkflowRuleRun).where(
                    WorkflowRuleRun.rule_id == rule.id, WorkflowRuleRun.card_id == card.id,
                    WorkflowRuleRun.residency == utc(card.workflow_stage_entered_at).isoformat(),
                    WorkflowRuleRun.status.in_(["queued", "running", "uncertain"]))).first()
                exhausted = db.exec(SqlBuilder.select.table(WorkflowActionRun)
                    .join(WorkflowRuleRun, WorkflowRuleRun.id == WorkflowActionRun.rule_run_id).where(
                        WorkflowRuleRun.rule_id == rule.id, WorkflowRuleRun.rule_revision == rule.revision,
                        WorkflowRuleRun.card_id == card.id,
                        WorkflowRuleRun.residency == utc(card.workflow_stage_entered_at).isoformat(),
                        WorkflowActionRun.error_code == "retry_exhausted")).first()
                if outstanding or exhausted:
                    continue
                before = rows(db, WorkflowRuleRun, rule_id=rule.id, event_id=event_id,
                              residency=utc(card.workflow_stage_entered_at).isoformat())
                if before:
                    continue
                admitted += len(admit(db, None, card, "interval", event_id=event_id, occurred_at=due, rule_id=rule.id))
                if admitted >= limit:
                    return admitted
    return admitted


async def run_due_rules(*, limit=5, now=None):
    """Existing cron entry point now drains every normalized workflow action."""
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Workflow limit must be between 1 and 100")
    if DbSession.has_active_transaction():
        raise ValueError("Workflow cron requires a committed transaction")
    now = utc(now or SafeDateTime.now())
    _admit_intervals(now, limit)
    with DbSession.use(readonly=False) as db:
        active = db.exec(select(func.count()).select_from(WorkflowActionRun).where(
            WorkflowActionRun.status == "running", WorkflowActionRun.lease_expires_at > now)).first()[0]
        capacity = max(0, min(limit, MAX_RUNNING - active))
        ids = db.exec(SqlBuilder.select.column(WorkflowRuleRun.id).where(
            WorkflowRuleRun.status.in_(["queued", "running"]), WorkflowRuleRun.next_attempt_at <= now)
            .order_by(WorkflowRuleRun.id).limit(capacity)).all()
    for run_id in ids:
        await run_rule(run_id, now=now)
    return len(ids)


async def run_workflow_bound_schedule(bot, schedule, association, card):
    """The native scheduler yields configured schedules to the common interval queue."""
    from ..domain.models.BotSchedule import BotScheduleRunningType
    if (schedule.running_type not in {BotScheduleRunningType.Infinite, BotScheduleRunningType.Duration}
            or not card.workflow_stage_entered_key or card.workflow_stage_entered_at is None):
        return False
    with DbSession.use(readonly=False) as db:
        column = db.exec(SqlBuilder.select.table(ProjectColumn).where(ProjectColumn.id == card.project_column_id)).first()
        stage = db.exec(SqlBuilder.select.table(WorkflowStageDefinition).where(
            WorkflowStageDefinition.key == column.workflow_stage, WorkflowStageDefinition.is_active.is_(True))).first() if column else None
        if stage is None:
            return False
        for rule in rows(db, WorkflowRule, stage_id=stage.id, retired=False, is_enabled=True, trigger_kind="interval"):
            if any(a.kind == "bot.invoke" and a.executor_id == bot.id and a.config.get("schedule_uid") == schedule.get_uid()
                   for a in rows(db, WorkflowRuleAction, rule_id=rule.id)):
                return True
    return False


if __name__ == "__main__":
    asyncio.run(run_due_rules())
