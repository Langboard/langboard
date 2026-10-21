"""Short transactional fences; HTTP must run outside these transactions.

An expired dispatch lease is uncertain, never permission to replay an external effect.
Graph admission reconciliation must resolve it before any new attempt.
"""

from dataclasses import dataclass
from datetime import timedelta, timezone
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


def reserve_schedule_run(schedule, association, *, now, max_attempts=6, retry_window_seconds=86400):
    if schedule.running_type != BotScheduleRunningType.Onetime:
        raise ValueError("A one-time schedule is required")
    if not 1 <= max_attempts <= 10 or not 1 <= retry_window_seconds <= 86400:
        raise ValueError("Invalid schedule retry limits")
    # Edited start/interval/bot creates another occurrence; the caller must also recheck the live schedule.
    fingerprint = [
        str(schedule.id),
        association.__tablename__,
        str(association.id),
        str(schedule.bot_id),
        _utc(schedule.start_at).isoformat() if schedule.start_at else None,
        schedule.interval_str,
    ]
    key = sha256(dumps(fingerprint, separators=(",", ":")).encode()).hexdigest()
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


def claim_schedule_run(run_id, *, now, lease_seconds=300):
    if not 1 <= lease_seconds <= 3600:
        raise ValueError("Invalid schedule lease")
    with DbSession.atomic() as db:
        db.exec(
            update(BotScheduleRun)
            .execution_options(synchronize_session=False)
            .where(
                BotScheduleRun.id == run_id,
                BotScheduleRun.state == "dispatching",
                BotScheduleRun.lease_expires_at <= now,
            )
            .values(state="uncertain", lease_token=None, lease_expires_at=None, error_code="dispatch_lease_expired")
        )
        db.exec(
            update(BotScheduleRun)
            .execution_options(synchronize_session=False)
            .where(
                BotScheduleRun.id == run_id,
                BotScheduleRun.state == "pending",
                BotScheduleRun.expires_at <= now,
            )
            .values(state="failed", error_code="retry_window_expired")
        )
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
        if outcome == BotRequestOutcome.Skipped:
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
            .values(state=state, next_attempt_at=due, lease_token=None, lease_expires_at=None, error_code=error)
        )
        return changed == 1
