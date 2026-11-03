"""Short transactional fences; HTTP must run outside these transactions.

An expired dispatch lease is uncertain, never permission to replay an external effect.
Graph admission reconciliation must resolve it before any new attempt.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from json import dumps
from uuid import uuid4
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from ....core.db import DbSession, SqlBuilder
from ....domain.models.BotSchedule import BotScheduleRunningType
from ....domain.models.BotScheduleRun import BotScheduleRun
from .requests.BaseBotRequest import BotRequestOutcome


@dataclass(frozen=True)
class ScheduleRunLease:
    run_id: int
    occurrence_key: str
    token: str
    attempt: int


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def occurrence_key(schedule, association):
    fingerprint = [
        str(schedule.id),
        association.__tablename__,
        str(association.id),
        str(schedule.bot_id),
        _utc(schedule.start_at).isoformat() if schedule.start_at else None,
        schedule.interval_str,
    ]
    return sha256(dumps(fingerprint, separators=(",", ":")).encode()).hexdigest()


def get_schedule_run(run_id):
    with DbSession.atomic() as db:
        return db.exec(
            SqlBuilder.select.table(BotScheduleRun)
            .execution_options(populate_existing=True)
            .where(BotScheduleRun.id == run_id)
        ).first()


def reserve_schedule_run(schedule, association, *, now, max_attempts=6, retry_window_seconds=86400):
    if schedule.running_type != BotScheduleRunningType.Onetime:
        raise ValueError("A one-time schedule is required")
    if not 1 <= max_attempts <= 10 or not 1 <= retry_window_seconds <= 86400:
        raise ValueError("Invalid schedule retry limits")
    # Edited start/interval/bot creates another occurrence; the caller must also recheck the live schedule.
    key = occurrence_key(schedule, association)
    try:
        with DbSession.atomic() as db:
            existing = db.exec(
                SqlBuilder.select.table(BotScheduleRun)
                .execution_options(populate_existing=True)
                .where(BotScheduleRun.occurrence_key == key)
            ).first()
            if existing:
                return existing
            record = BotScheduleRun(
                schedule_id=schedule.id,
                scope_table=association.__tablename__,
                scope_id=association.id,
                occurrence_key=key,
                max_attempts=max_attempts,
                next_attempt_at=now,
                expires_at=now + timedelta(seconds=retry_window_seconds),
            )
            db.insert(record)
            return record
    except IntegrityError:
        # Another worker reserved the same immutable occurrence.
        with DbSession.atomic() as db:
            existing = db.exec(
                SqlBuilder.select.table(BotScheduleRun)
                .execution_options(populate_existing=True)
                .where(BotScheduleRun.occurrence_key == key)
            ).first()
            if existing is None:
                raise
            return existing


def _expire_schedule_runs(db, run_ids, *, now):
    # Terminal bookkeeping needs no target authority and must never acquire a dispatch lease.
    db.exec(
        update(BotScheduleRun)
        .execution_options(synchronize_session=False)
        .where(
            BotScheduleRun.id.in_(run_ids),
            BotScheduleRun.state == "dispatching",
            BotScheduleRun.lease_expires_at <= now,
        )
        .values(state="uncertain", lease_token=None, lease_expires_at=None, error_code="dispatch_lease_expired")
    )
    db.exec(
        update(BotScheduleRun)
        .execution_options(synchronize_session=False)
        .where(
            BotScheduleRun.id.in_(run_ids),
            BotScheduleRun.state == "pending",
            BotScheduleRun.expires_at <= now,
        )
        .values(state="failed", error_code="retry_window_expired")
    )


def reconcile_expired_schedule_runs(*, now, batch_size=100):
    """Bounded recovery even when a deleted source can no longer resolve its target."""
    if not 1 <= batch_size <= 1000:
        raise ValueError("Invalid schedule recovery batch")
    with DbSession.atomic() as db:
        records = db.exec(
            SqlBuilder.select.table(BotScheduleRun)
            .where(
                ((BotScheduleRun.state == "pending") & (BotScheduleRun.expires_at <= now))
                | ((BotScheduleRun.state == "dispatching") & (BotScheduleRun.lease_expires_at <= now))
            )
            .order_by(BotScheduleRun.id)
            .limit(batch_size)
        ).all()
        if records:
            _expire_schedule_runs(db, [record.id for record in records], now=now)


def claim_schedule_run(run_id, *, now, lease_seconds=300):
    if not 1 <= lease_seconds <= 3600:
        raise ValueError("Invalid schedule lease")
    with DbSession.atomic() as db:
        _expire_schedule_runs(db, [run_id], now=now)
        token = uuid4().hex
        claimed = db.exec(
            update(BotScheduleRun)
            .execution_options(synchronize_session=False)
            .where(
                BotScheduleRun.id == run_id,
                BotScheduleRun.state == "pending",
                BotScheduleRun.next_attempt_at <= now,
                BotScheduleRun.expires_at > now,
                BotScheduleRun.attempt < BotScheduleRun.max_attempts,
            )
            .values(
                state="dispatching",
                attempt=BotScheduleRun.attempt + 1,
                lease_token=token,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
                error_code=None,
            )
        )
        if claimed != 1:
            return None
        record = db.exec(
            SqlBuilder.select.table(BotScheduleRun)
            .execution_options(populate_existing=True)
            .where(BotScheduleRun.id == run_id)
        ).first()
        return ScheduleRunLease(int(record.id), record.occurrence_key, token, record.attempt)


def finish_schedule_run(lease, outcome, *, now):
    if not isinstance(outcome, BotRequestOutcome):
        raise ValueError("An explicit bot admission outcome is required")
    with DbSession.atomic() as db:
        record = db.exec(
            SqlBuilder.select.table(BotScheduleRun)
            .execution_options(populate_existing=True)
            .where(
                BotScheduleRun.id == lease.run_id,
                BotScheduleRun.state == "dispatching",
                BotScheduleRun.lease_token == lease.token,
                BotScheduleRun.attempt == lease.attempt,
                BotScheduleRun.lease_expires_at > now,
            )
        ).first()
        if record is None:
            return False
        state, error, due = "accepted", None, now
        if outcome == BotRequestOutcome.Uncertain:
            state, error = "uncertain", "admission_uncertain"
        elif outcome == BotRequestOutcome.Skipped:
            state, error = "cancelled", "scope_excluded"
        elif outcome == BotRequestOutcome.PermanentFailure:
            state, error = "failed", "permanent_failure"
        elif outcome == BotRequestOutcome.RetryableFailure:
            due = now + timedelta(seconds=min(30 * 2 ** (lease.attempt - 1), 900))
            state, error = "pending", "retryable_failure"
            if record.attempt >= record.max_attempts:
                state, error = "failed", "retry_limit_exceeded"
            elif _utc(due) >= _utc(record.expires_at):
                state, error = "failed", "retry_window_expired"
        changed = db.exec(
            update(BotScheduleRun)
            .execution_options(synchronize_session=False)
            .where(
                BotScheduleRun.id == lease.run_id,
                BotScheduleRun.state == "dispatching",
                BotScheduleRun.lease_token == lease.token,
                BotScheduleRun.attempt == lease.attempt,
                BotScheduleRun.lease_expires_at > now,
            )
            .values(
                state=state, next_attempt_at=now if state in {"failed", "uncertain"} else due,
                lease_token=None, lease_expires_at=None, error_code=error,
            )
        )
        return changed == 1


def load_schedule_context(schedule_id, scope_table, scope_id):
    """Read current authority and immutable occurrence inputs, never broker snapshots."""
    from ....core.types import SafeDateTime
    from ....domain.models import Bot
    from ....domain.models.bases import BaseBotScheduleModel
    from ....domain.models.BotSchedule import BotSchedule, BotScheduleStatus
    from ....helpers import ModelHelper

    association_class = ModelHelper.get_model_by_table_name(scope_table)
    if not isinstance(association_class, type) or not issubclass(association_class, BaseBotScheduleModel):
        return None
    with DbSession.atomic() as db:
        association = db.exec(
            SqlBuilder.select.table(association_class).where(association_class.id == scope_id)
        ).first()
        schedule = db.exec(SqlBuilder.select.table(BotSchedule).where(BotSchedule.id == schedule_id)).first()
        if (
            association is None
            or schedule is None
            or association.bot_schedule_id != schedule.id
            or schedule.status != BotScheduleStatus.Started
            or schedule.running_type != BotScheduleRunningType.Onetime
            or schedule.start_at is None
            or _utc(schedule.start_at) > _utc(SafeDateTime.now())
        ):
            return None
        bot = db.exec(SqlBuilder.select.table(Bot).where(Bot.id == schedule.bot_id)).first()
        if bot is None or bot.deleted_at:
            return None
        return bot, schedule, association


def admit_graph_schedule(api_request):
    """Persist admission before enqueueing. A crash after this fence cannot enqueue twice."""
    from ....core.types import SafeDateTime, SnowflakeID
    from ....helpers import BotHelper
    from .BotTaskHelper import BotTaskHelper

    execution = api_request.schedule_execution
    now = SafeDateTime.now()
    with DbSession.atomic() as db:
        record = db.exec(
            SqlBuilder.select.table(BotScheduleRun)
            .execution_options(populate_existing=True)
            .where(BotScheduleRun.occurrence_key == execution.occurrence_key)
        ).first()
        if record is None:
            return None
        context = load_schedule_context(record.schedule_id, record.scope_table, record.scope_id)
        if context is None:
            return None
        bot, schedule, association = context
        model = BotHelper.get_target_model_by_bot_model("schedule", association)
        if (
            bot.get_uid() != api_request.uid
            or occurrence_key(schedule, association) != record.occurrence_key
            or model is None
            or not BotTaskHelper.can_dispatch_card_scope({}, None, model)
            or (
                api_request.project_uid is not None
                and getattr(model, "project_id", None) is not None
                and SnowflakeID(model.project_id).to_short_code() != api_request.project_uid
            )
        ):
            return None
        if not _matches_graph_schedule_request(api_request, bot, model):
            return None
        if record.state == "accepted":
            return False
        changed = db.exec(
            update(BotScheduleRun)
            .execution_options(synchronize_session=False)
            .where(
                BotScheduleRun.id == record.id,
                BotScheduleRun.state == "dispatching",
                BotScheduleRun.lease_token == execution.token,
                BotScheduleRun.attempt == execution.attempt,
                BotScheduleRun.lease_expires_at > now,
                BotScheduleRun.expires_at > now,
            )
            .values(state="uncertain", lease_token=None, lease_expires_at=None, error_code="graph_admission_reserved")
        )
        if changed == 1:
            return True
        # A concurrent admission may have won after our initial read.
        latest = get_schedule_run(record.id)
        return False if latest.state == "accepted" else None


def confirm_graph_schedule_admission(execution):
    """Queue registration succeeded; record admission, never execution completion."""
    with DbSession.atomic() as db:
        return (
            db.exec(
                update(BotScheduleRun)
                .execution_options(synchronize_session=False)
                .where(
                    BotScheduleRun.occurrence_key == execution.occurrence_key,
                    BotScheduleRun.state == "uncertain",
                    BotScheduleRun.error_code == "graph_admission_reserved",
                    BotScheduleRun.attempt == execution.attempt,
                )
                .values(state="accepted", error_code=None)
            )
            == 1
        )


def _matches_graph_schedule_request(api_request, bot, model):
    """Bind every runtime input to the current source; reject swapped caller context."""
    from ....core.types import SnowflakeID
    from ....core.utils.Converter import convert_python_data
    from ....domain.models import BotLog, Card, Project, ProjectColumn
    from ....helpers import BotHelper
    from .requests.DefaultRequest import DefaultRequest

    with DbSession.atomic() as db:
        if not isinstance(model, (Card, ProjectColumn)):
            return False
        project = db.exec(SqlBuilder.select.table(Project).where(Project.id == model.project_id)).first()
        if project is None or project.deleted_at:
            return False
        column = (
            model
            if isinstance(model, ProjectColumn)
            else db.exec(
                SqlBuilder.select.table(ProjectColumn).where(ProjectColumn.id == model.project_column_id)
            ).first()
        )
        if column is None or column.deleted_at or column.project_id != project.id:
            return False
        try:
            log_id = SnowflakeID.from_short_code(api_request.log_uid)
        except (TypeError, ValueError):
            return False
        log = db.exec(SqlBuilder.select.table(BotLog).where(BotLog.id == log_id, BotLog.bot_id == bot.id)).first()
        log_class = BotHelper.get_bot_model_class("log", model.__tablename__)
        if log is None or log_class is None or api_request.scope_log_table != log_class.__tablename__:
            return False
        scope_log = db.exec(
            SqlBuilder.select.table(log_class).where(
                log_class.bot_log_id == log.id, log_class.column(f"{model.__tablename__}_id") == model.id
            )
        ).first()
        if scope_log is None:
            return False
        data = {
            "project_uid": project.get_uid(),
            "project_column_uid": column.get_uid(),
            "scope": model.__tablename__,
            "schedule_execution": api_request.schedule_execution.model_dump(),
        }
        if isinstance(model, Card):
            data["card_uid"] = model.get_uid()
        expected = DefaultRequest(bot, "", "bot_cron_scheduled", data, project, model).create_request_data(
            (log, scope_log)
        )
        payload = convert_python_data(expected["data"], recursive=True)
        actual = convert_python_data(api_request.model_dump(), recursive=True)
        for candidate in (payload, actual):
            for variables in (candidate["tweaks"], candidate["tweaks"].get("LangboardCalledVariablesComponent", {})):
                runner = variables.get("current_runner_data") or {}
                for field in ("created_at", "updated_at"):
                    if runner.get(field):
                        try:
                            runner[field] = datetime.fromisoformat(runner[field]).astimezone(timezone.utc).isoformat()
                        except (TypeError, ValueError):
                            return False
        return all(
            actual[key] == payload[key]
            for key in ("uid", "project_uid", "session_id", "thread_id", "tweaks", "input_value", "run_type")
        )
