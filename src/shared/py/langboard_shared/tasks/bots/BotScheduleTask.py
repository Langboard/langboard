from ...ai import BotScheduleHelper
from ...core.broker import Broker
from ...core.db import BaseDbModel, DbSession, SqlBuilder
from ...core.types import SafeDateTime
from ...domain.constants.DomainEventType import DomainEventType
from ...domain.models import Bot, Card, Project, ProjectColumn
from ...domain.models.bases import BaseBotScheduleModel
from ...domain.models.BotLog import BotLogType
from ...domain.models.BotSchedule import BotSchedule, BotScheduleRunningType, BotScheduleStatus
from ...domain.models.BotScheduleRun import BotScheduleRun
from ...helpers import BotHelper, ModelHelper
from ...publishers import ProjectBotPublisher
from .utils import BotTaskHelper, BotTaskSchemaHelper
from .utils.BotScheduleFailureNotification import notify_schedule_failure, recover_schedule_failure_notifications
from .utils.BotScheduleRunStore import (
    claim_schedule_run,
    finish_schedule_run,
    get_schedule_run,
    load_schedule_context,
    occurrence_key,
    reconcile_expired_schedule_runs,
    reserve_schedule_run,
)
from .utils.BotTaskHelper import logger
from .utils.requests.BaseBotRequest import BotRequestOutcome
from .utils.requests.Utils import create_request


@BotTaskSchemaHelper.schema(
    DomainEventType.BotCronScheduled,
    {
        "project_uid": "string",
        "project_column_uid": "string",
        "card_uid?": "string",
        "scope": "string",
    },
)
@Broker.wrap_async_task_decorator
async def bot_cron_scheduled(bot: Bot, bot_schedule: BotSchedule, schedule_model: BaseBotScheduleModel):
    await _run_scheduler(bot, bot_schedule, schedule_model)


async def run_scheduled_bots_cron(interval_str: str):
    if interval_str == "recover":
        await recover_due_onetime_schedules()
        return

    if interval_str.startswith("scheduled "):
        await _check_bot_schedule_runnable(interval_str.removeprefix("scheduled "))
        return

    interval_str = BotScheduleHelper.utils.convert_valid_interval_str(interval_str)
    if not interval_str:
        logger.error(f"Invalid interval string: {interval_str}")
        return

    model_classes = ModelHelper.get_models_by_base_class(BaseBotScheduleModel)
    records: list[tuple[BaseBotScheduleModel, BotSchedule, Bot]] = []
    with DbSession.use(readonly=True) as db:
        for model_class in model_classes:
            result = db.exec(
                SqlBuilder.select.tables(model_class, BotSchedule, Bot)
                .join(
                    BotSchedule,
                    model_class.column("bot_schedule_id") == BotSchedule.column("id"),
                )
                .join(Bot, BotSchedule.column("bot_id") == Bot.column("id"))
                .where(
                    (Bot.column("deleted_at").is_(None))
                    & (BotSchedule.column("interval_str") == interval_str)
                    & (BotSchedule.column("status") == BotScheduleStatus.Started)
                    & (
                        (BotSchedule.column("running_type") != BotScheduleRunningType.Onetime)
                        | (BotSchedule.column("start_at") <= SafeDateTime.now())
                    )
                )
            )
            records.extend(result.all())

    for schedule_model, bot_schedule, bot in records:
        await _run_scheduler(bot, bot_schedule, schedule_model)


async def _check_bot_schedule_runnable(interval_str: str):
    current_time = SafeDateTime.now()
    model_classes = ModelHelper.get_models_by_base_class(BaseBotScheduleModel)
    records: list[tuple[BaseBotScheduleModel, BotSchedule, Bot]] = []
    with DbSession.use(readonly=True) as db:
        for model_class in model_classes:
            result = db.exec(
                SqlBuilder.select.tables(model_class, BotSchedule, Bot)
                .join(
                    BotSchedule,
                    model_class.column("bot_schedule_id") == BotSchedule.column("id"),
                )
                .join(Bot, BotSchedule.column("bot_id") == Bot.column("id"))
                .where(
                    (Bot.column("deleted_at").is_(None))
                    & (
                        (BotSchedule.column("status") == BotScheduleStatus.Pending)
                        | (
                            (BotSchedule.column("status") == BotScheduleStatus.Started)
                            & (BotSchedule.column("running_type") == BotScheduleRunningType.Onetime)
                        )
                    )
                    & (BotSchedule.column("start_at") <= current_time)
                    & (BotSchedule.column("interval_str") == interval_str)
                )
            )
            records.extend(result.all())

    with DbSession.atomic() as db:
        db.exec(
            SqlBuilder.update.table(BotSchedule)
            .values({"status": BotScheduleStatus.Started})
            .where(
                (BotSchedule.column("status") == BotScheduleStatus.Pending)
                & (BotSchedule.column("start_at") <= current_time)
                & (BotSchedule.column("interval_str") == interval_str)
                & (BotSchedule.column("running_type") == BotScheduleRunningType.Onetime)
                & BotSchedule.id.in_([schedule.id for _, schedule, _ in records])
            )
        )
        for association, schedule, _ in records:
            if schedule.running_type == BotScheduleRunningType.Onetime:
                # The original cron tick owns the first occurrence. Commit it with Started.
                reserve_schedule_run(schedule, association, now=current_time)

    for schedule_model, bot_schedule, bot in records:
        if bot_schedule.running_type == BotScheduleRunningType.Duration:
            if (
                not bot_schedule.start_at
                or not bot_schedule.end_at
                or bot_schedule.start_at >= bot_schedule.end_at
                or bot_schedule.end_at < current_time
            ):
                continue

        BotScheduleHelper.change_status(
            schedule_model.__class__,
            schedule_model,
            BotScheduleStatus.Started,
            bot_schedule=bot_schedule,
        )

        model = BotHelper.get_target_model_by_bot_model("schedule", schedule_model)
        if not model:
            continue

        project = None
        if isinstance(model, ProjectColumn) or isinstance(model, Card):
            with DbSession.use(readonly=True) as db:
                result = db.exec(
                    SqlBuilder.select.table(Project).where(Project.column("id") == model.project_id).limit(1)
                )
                project = result.first()

        if project:
            ProjectBotPublisher.rescheduled(project, schedule_model, {"status": bot_schedule.status.value})

        await _run_scheduler(bot, bot_schedule, schedule_model, model)


async def _run_scheduler(
    bot: Bot,
    bot_schedule: BotSchedule,
    schedule_model: BaseBotScheduleModel,
    model: BaseDbModel | None = None,
    *,
    workflow: bool = False,
    workflow_claim: dict | None = None,
):
    if bot_schedule.running_type == BotScheduleRunningType.Onetime:
        context = load_schedule_context(bot_schedule.id, schedule_model.__tablename__, schedule_model.id)
        if context is None:
            return []
        current_bot, current_schedule, current_association = context
        if occurrence_key(bot_schedule, schedule_model) != occurrence_key(current_schedule, current_association):
            return []
        bot, bot_schedule, schedule_model = current_bot, current_schedule, current_association
        # Discard any target supplied in an old broker message.
        model = None
    if bot_schedule.status != BotScheduleStatus.Started:
        return

    if not model:
        model = BotHelper.get_target_model_by_bot_model("schedule", schedule_model)
        if not model:
            return

    if isinstance(model, Card) and not workflow:
        from ..WorkflowRuleTask import run_workflow_bound_schedule

        if await run_workflow_bound_schedule(bot, bot_schedule, schedule_model, model):
            return []

    project = None
    data = {}
    with DbSession.use(readonly=True) as db:
        if isinstance(model, ProjectColumn):
            result = db.exec(SqlBuilder.select.table(Project).where(Project.column("id") == model.project_id).limit(1))
            project = result.first()
            if not project:
                return
            data = {
                "project_column_uid": model.get_uid(),
                "project_uid": project.get_uid(),
                "scope": ProjectColumn.__tablename__,
            }
        elif isinstance(model, Card):
            result = db.exec(
                SqlBuilder.select.tables(ProjectColumn, Project)
                .join(Project, ProjectColumn.column("project_id") == Project.column("id"))
                .where(ProjectColumn.column("id") == model.project_column_id)
                .limit(1)
            )
            column, project = result.first() or (None, None)
            if not column or not project:
                return
            data = {
                "project_column_uid": column.get_uid(),
                "card_uid": model.get_uid(),
                "project_uid": project.get_uid(),
                "scope": Card.__tablename__,
            }
        else:
            return

    if workflow_claim:
        data["workflow_rule"] = workflow_claim
    lease = None
    if bot_schedule.running_type == BotScheduleRunningType.Onetime:
        run = reserve_schedule_run(bot_schedule, schedule_model, now=SafeDateTime.now())
        lease = claim_schedule_run(run.id, now=SafeDateTime.now())
        if lease is None:
            saved = get_schedule_run(run.id)
            if saved.state in {"failed", "uncertain"}:
                notify_schedule_failure(saved.id)
            if saved.state == "accepted":
                BotScheduleHelper.change_status(schedule_model.__class__, schedule_model, BotScheduleStatus.Stopped)
            elif saved.state != run.state and saved.state in {"failed", "uncertain"}:
                await _record_admission_failure(bot, data, project, model, saved)
            return []
        data["schedule_execution"] = {
            "occurrence_key": lease.occurrence_key,
            "token": lease.token,
            "attempt": lease.attempt,
        }

    with DbSession.use(readonly=False) as db:
        schedule_model.last_rnu_at = SafeDateTime.now()
        db.update(schedule_model)

    try:
        outcomes = await BotTaskHelper.run(
            bot,
            DomainEventType.BotCronScheduled,
            data,
            project,
            model,
            emit_webhook=lease is None or lease.attempt == 1,
        )
    except Exception:
        if lease:
            finish_schedule_run(lease, BotRequestOutcome.Uncertain, now=SafeDateTime.now())
            notify_schedule_failure(lease.run_id)
        raise
    if lease:
        outcome = outcomes[0] if len(outcomes) == 1 else BotRequestOutcome.Skipped
        finish_schedule_run(lease, outcome, now=SafeDateTime.now())
        run = get_schedule_run(lease.run_id)
        if run.state != "accepted":
            if run.state in {"failed", "uncertain", "cancelled"}:
                await _record_admission_failure(bot, data, project, model, run)
            logger.warning(
                "One-time schedule admission: occurrence=%s state=%s attempts=%s error=%s",
                run.occurrence_key,
                run.state,
                run.attempt,
                run.error_code,
            )
            return outcomes

    old_status = bot_schedule.status
    if bot_schedule.running_type == BotScheduleRunningType.Onetime:
        context = load_schedule_context(bot_schedule.id, schedule_model.__tablename__, schedule_model.id)
        if context is None or occurrence_key(context[1], context[2]) != lease.occurrence_key:
            return outcomes
        BotScheduleHelper.change_status(schedule_model.__class__, schedule_model, BotScheduleStatus.Stopped)
    elif bot_schedule.running_type == BotScheduleRunningType.Duration:
        if bot_schedule.end_at and bot_schedule.end_at < SafeDateTime.now():
            BotScheduleHelper.change_status(schedule_model.__class__, schedule_model, BotScheduleStatus.Stopped)

    if project and bot_schedule.status != old_status:
        ProjectBotPublisher.rescheduled(project, schedule_model, {"status": bot_schedule.status.value})
    return outcomes


async def _record_admission_failure(bot, data, project, model, run):
    if run.state in {"failed", "uncertain"}:
        notify_schedule_failure(run.id)
    request = create_request(bot, DomainEventType.BotCronScheduled.value, data, project, model)
    if request and request._can_dispatch_scope():
        await request._create_log(
            BotLogType.Error,
            f"Schedule admission {run.state}: occurrence={run.occurrence_key}; "
            f"attempts={run.attempt}/{run.max_attempts}; reason={run.error_code}. "
            "Admission does not establish execution success.",
        )


async def recover_due_onetime_schedules():
    """Recover existing admissions; only the original cron tick may create a first occurrence."""
    now = SafeDateTime.now()
    reconcile_expired_schedule_runs(now=now)
    recover_schedule_failure_notifications()
    # Resolve the current occurrence once per association. Joining all past runs
    # materializes every accepted receipt after schedule edits, indefinitely.
    for association_class in ModelHelper.get_models_by_base_class(BaseBotScheduleModel):
        after_id = 0
        while True:
            with DbSession.use(readonly=True) as db:
                records = db.exec(
                    SqlBuilder.select.tables(association_class, BotSchedule, Bot)
                    .join(BotSchedule, association_class.bot_schedule_id == BotSchedule.id)
                    .join(Bot, BotSchedule.bot_id == Bot.id)
                    .where(
                        association_class.id > after_id,
                        BotSchedule.running_type == BotScheduleRunningType.Onetime,
                        BotSchedule.status == BotScheduleStatus.Started,
                        BotSchedule.start_at <= now,
                        Bot.deleted_at.is_(None),
                    )
                    .order_by(association_class.id)
                    .limit(100)
                ).all()
                if not records:
                    break
                current = {
                    occurrence_key(schedule, association): (association, schedule, bot)
                    for association, schedule, bot in records
                }
                due = db.exec(
                    SqlBuilder.select.table(BotScheduleRun).where(
                        BotScheduleRun.occurrence_key.in_(current),
                        ((BotScheduleRun.state == "pending") & (BotScheduleRun.next_attempt_at <= now))
                        | ((BotScheduleRun.state == "dispatching") & (BotScheduleRun.lease_expires_at <= now))
                        | (BotScheduleRun.state == "accepted"),
                    )
                ).all()
            after_id = records[-1][0].id
            for saved in due:
                association, schedule, bot = current[saved.occurrence_key]
                if (
                    saved.schedule_id != schedule.id
                    or saved.scope_table != association_class.__tablename__
                    or saved.scope_id != association.id
                ):
                    continue
                await _run_scheduler(bot, schedule, association)
