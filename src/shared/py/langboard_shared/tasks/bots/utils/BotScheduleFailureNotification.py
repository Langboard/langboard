"""Transactional occurrence fence for native operational notifications.

Terminal next_attempt_at is a notification retry cursor, never an admission retry.
The far-future cursor is a durable receipt even if the user deletes the notification.
"""

from datetime import datetime, timedelta, timezone
from sqlalchemy import update
from ....core.db import DbSession, SqlBuilder
from ....core.types import SafeDateTime
from ....domain.models import Card, Project, ProjectColumn, User
from ....domain.models.BotScheduleRun import BotScheduleRun
from ....domain.services import DomainService
from ....helpers import BotHelper
from .BotScheduleRunStore import load_schedule_context, occurrence_key
from .BotTaskHelper import logger


NOTIFICATION_RECOVERY_BATCH = 50
NOTIFICATION_RETRY_SECONDS = 300
NOTIFICATION_DELIVERED_AT = datetime(9999, 1, 1, tzinfo=timezone.utc)
SAFE_ERROR_CODES = {
    "permanent_failure",
    "retry_limit_exceeded",
    "retry_window_expired",
    "admission_uncertain",
    "dispatch_lease_expired",
    "graph_admission_reserved",
}


def notify_schedule_failure(run_id):
    now = SafeDateTime.now()
    try:
        with DbSession.atomic() as db:
            # UPDATE first: PostgreSQL row lock and SQLite writer serialization. The
            # cursor and UserNotification commit or roll back in the same transaction.
            claimed = db.exec(
                update(BotScheduleRun)
                .execution_options(synchronize_session=False)
                .where(
                    BotScheduleRun.id == run_id,
                    BotScheduleRun.state.in_(["failed", "uncertain"]),
                    BotScheduleRun.next_attempt_at <= now,
                )
                .values(next_attempt_at=now + timedelta(seconds=NOTIFICATION_RETRY_SECONDS))
            )
            if claimed != 1:
                return False
            run = db.exec(
                SqlBuilder.select.table(BotScheduleRun)
                .execution_options(populate_existing=True)
                .where(BotScheduleRun.id == run_id)
            ).first()
            context = load_schedule_context(run.schedule_id, run.scope_table, run.scope_id)
            if context is None or occurrence_key(context[1], context[2]) != run.occurrence_key:
                return False
            model = BotHelper.get_target_model_by_bot_model("schedule", context[2])
            if not isinstance(model, (Card, ProjectColumn)) or model.deleted_at:
                return False
            project = db.exec(SqlBuilder.select.table(Project).where(Project.id == model.project_id)).first()
            column = (
                model
                if isinstance(model, ProjectColumn)
                else db.exec(
                    SqlBuilder.select.table(ProjectColumn).where(ProjectColumn.id == model.project_column_id)
                ).first()
            )
            if project is None or project.deleted_at or column is None or column.deleted_at:
                return False
            if column.project_id != project.id:
                return False
            owner = db.exec(SqlBuilder.select.table(User).where(User.id == project.owner_id)).first()
            if owner is None:
                return False
            with DomainService.use() as service:
                delivered = service.notification.notify_bot_schedule_failed(
                    owner,
                    project,
                    model,
                    {
                        "occurrence_key": run.occurrence_key,
                        "schedule_uid": context[1].get_uid(),
                        "project_column_uid": column.get_uid(),
                        "state": run.state,
                        "attempt": run.attempt,
                        "max_attempts": run.max_attempts,
                        "error_code": run.error_code if run.error_code in SAFE_ERROR_CODES else "unknown_failure",
                    },
                )
            if delivered:
                db.exec(
                    update(BotScheduleRun)
                    .where(BotScheduleRun.id == run.id)
                    .values(next_attempt_at=NOTIFICATION_DELIVERED_AT)
                )
            return delivered
    except Exception:
        # Admission was committed separately. Failed delivery cannot replay work.
        logger.exception("Schedule failure notification deferred: run=%s", run_id)
        with DbSession.atomic() as db:
            db.exec(
                update(BotScheduleRun)
                .where(
                    BotScheduleRun.id == run_id,
                    BotScheduleRun.state.in_(["failed", "uncertain"]),
                    BotScheduleRun.next_attempt_at <= now,
                )
                .values(next_attempt_at=now + timedelta(seconds=NOTIFICATION_RETRY_SECONDS))
            )
        return False


def recover_schedule_failure_notifications():
    with DbSession.use(readonly=True) as db:
        runs = db.exec(
            SqlBuilder.select.table(BotScheduleRun)
            .where(
                BotScheduleRun.state.in_(["failed", "uncertain"]),
                BotScheduleRun.next_attempt_at <= SafeDateTime.now(),
            )
            .order_by(BotScheduleRun.next_attempt_at, BotScheduleRun.id)
            .limit(NOTIFICATION_RECOVERY_BATCH)
        ).all()
    for run in runs:
        notify_schedule_failure(run.id)
