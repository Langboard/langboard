"""Durable one-time schedule admission attempts, without request secrets."""

from datetime import datetime, timezone
from hashlib import sha256
from json import dumps
from sqlalchemy import CheckConstraint, Index
from ...core.db import BaseDbModel, DateTimeField, Field, SnowflakeIDField
from ...core.types import SafeDateTime, SnowflakeID


def schedule_time_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def schedule_occurrence_key(schedule, association) -> str:
    fingerprint = [
        str(schedule.id),
        association.__tablename__,
        str(association.id),
        str(schedule.bot_id),
        schedule_time_utc(schedule.start_at).isoformat() if schedule.start_at else None,
        schedule.interval_str,
    ]
    return sha256(dumps(fingerprint, separators=(",", ":")).encode()).hexdigest()


class BotScheduleRun(BaseDbModel, table=True):
    __table_args__ = (
        Index("ix_bot_schedule_run_occurrence", "occurrence_key", unique=True),
        Index("ix_bot_schedule_run_due", "state", "next_attempt_at"),
        CheckConstraint(
            "attempt >= 0 AND max_attempts BETWEEN 1 AND 10 AND attempt <= max_attempts",
            name="bot_schedule_run_attempts",
        ),
        CheckConstraint(
            "state IN ('pending','dispatching','accepted','failed','uncertain','cancelled')",
            name="bot_schedule_run_state",
        ),
        CheckConstraint(
            "(state = 'dispatching' AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL) OR "
            "(state <> 'dispatching' AND lease_token IS NULL AND lease_expires_at IS NULL)",
            name="bot_schedule_run_lease",
        ),
    )
    # Evidence survives deletion of a schedule. Reading it still requires source authority.
    schedule_id: SnowflakeID = SnowflakeIDField(nullable=False)
    scope_table: str = Field(nullable=False, max_length=64)
    scope_id: SnowflakeID = SnowflakeIDField(nullable=False)
    occurrence_key: str = Field(nullable=False, max_length=64)
    state: str = Field(default="pending", nullable=False, max_length=16)
    attempt: int = Field(default=0, nullable=False)
    max_attempts: int = Field(default=6, nullable=False)
    next_attempt_at: SafeDateTime = DateTimeField(default=SafeDateTime.now, nullable=False)
    expires_at: SafeDateTime = DateTimeField(default=SafeDateTime.now, nullable=False)
    lease_token: str | None = Field(default=None, nullable=True, max_length=32)
    lease_expires_at: SafeDateTime | None = DateTimeField(default=None, nullable=True)
    error_code: str | None = Field(default=None, nullable=True, max_length=64)

    def notification_data(self) -> dict:
        return {}

    def _get_repr_keys(self):
        return ["schedule_id", "state", "attempt"]
