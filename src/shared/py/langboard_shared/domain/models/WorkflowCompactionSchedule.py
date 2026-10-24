"""Durable, per-card continuous checks. No source text or delegated user authority."""

from sqlalchemy import Index
from ...core.db import BaseDbModel, DateTimeField, Field, SnowflakeIDField
from ...core.types import SafeDateTime, SnowflakeID


class WorkflowCompactionSchedule(BaseDbModel, table=True):
    __table_args__ = (
        Index("ix_workflow_compaction_schedule_identity", "card_id", "profile_id", unique=True),
        Index("ix_workflow_compaction_schedule_due", "next_check_at"),
        Index("ix_workflow_compaction_schedule_active", "profile_id", "lease_expires_at"),
    )
    card_id: SnowflakeID = SnowflakeIDField(nullable=False)
    profile_id: SnowflakeID = SnowflakeIDField(nullable=False)
    next_check_at: SafeDateTime = DateTimeField(default=SafeDateTime.now, nullable=False)
    lease_token: str | None = Field(default=None, nullable=True, max_length=32)
    lease_expires_at: SafeDateTime | None = DateTimeField(default=None, nullable=True)
    last_status: str | None = Field(default=None, nullable=True, max_length=32)

    def notification_data(self):
        return {}

    def _get_repr_keys(self):
        return ["card_id", "profile_id", "next_check_at"]
