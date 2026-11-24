"""Durable card reminder schedules and the occurrences they materialize."""

from sqlalchemy import JSON, CheckConstraint, Index, UniqueConstraint, text
from sqlalchemy.sql.naming import conv
from ...core.db import BaseDbModel, DateTimeField, Field, SnowflakeIDField
from ...core.types import SafeDateTime, SnowflakeID


class CardReminderRule(BaseDbModel, table=True):
    __table_args__ = (
        CheckConstraint("revision > 0 AND consumed_count >= 0", name=conv("ck_card_reminder_rule_counters")),
        Index("ix_card_reminder_rule_due", "next_due_at"),
        UniqueConstraint("owner_id", "request_uid", name="uq_card_reminder_rule_request"),
    )

    card_id: SnowflakeID = SnowflakeIDField(foreign_key="card.id", nullable=False, index=True)
    owner_id: SnowflakeID = SnowflakeIDField(foreign_key="user.id", nullable=False, index=True)
    external_delivery: bool = Field(default=False, nullable=False)
    is_shared: bool = Field(default=False, nullable=False)
    schedule: dict = Field(nullable=False, sa_type=JSON)
    recipient_overrides: dict | None = Field(default=None, sa_type=JSON)
    enabled: bool = Field(default=True, nullable=False)
    request_uid: str | None = Field(default=None, max_length=36)
    request_fingerprint: str | None = Field(default=None, max_length=64)
    creation_receipt: dict | None = Field(default=None, sa_type=JSON)
    revision: int = Field(default=1, nullable=False)
    consumed_count: int = Field(default=0, nullable=False)
    next_due_at: SafeDateTime | None = DateTimeField(default=None, nullable=True)
    next_regular_due_at: SafeDateTime | None = DateTimeField(default=None, nullable=True)
    deadline_snapshot: SafeDateTime | None = DateTimeField(default=None, nullable=True)

    def notification_data(self) -> dict:
        return {}

    def _get_repr_keys(self):
        return ["card_id", "owner_id", "revision", "next_due_at"]


class CardReminderOccurrence(BaseDbModel, table=True):
    __table_args__ = (
        UniqueConstraint("rule_id", "due_at", name="uq_card_reminder_occurrence_due"),
        Index(
            "ix_card_reminder_occurrence_pending_move",
            "rule_id", "moved_to", "id",
            postgresql_where=text("moved_to IS NOT NULL AND moved_processed_at IS NULL AND skipped IS FALSE"),
            sqlite_where=text("moved_to IS NOT NULL AND moved_processed_at IS NULL AND skipped IS FALSE"),
        ),
    )

    rule_id: SnowflakeID = SnowflakeIDField(foreign_key="card_reminder_rule.id", nullable=False)
    rule_revision: int = Field(nullable=False)
    due_at: SafeDateTime = DateTimeField(default=None, nullable=False)
    skipped: bool = Field(default=False, nullable=False)
    moved_to: SafeDateTime | None = DateTimeField(default=None, nullable=True)
    moved_processed_at: SafeDateTime | None = DateTimeField(default=None, nullable=True)

    def notification_data(self) -> dict:
        return {}

    def _get_repr_keys(self):
        return ["rule_id", "rule_revision", "due_at"]
