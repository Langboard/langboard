"""Durable browser delivery, scoped to one notification and browser session."""

from sqlalchemy import Index, UniqueConstraint
from ...core.db import BaseDbModel, DateTimeField, Field, SnowflakeIDField
from ...core.types import SafeDateTime, SnowflakeID
from .UserNotification import UserNotification
from .UserWebPushSubscription import UserWebPushSubscription


class CardReminderWebPushDelivery(BaseDbModel, table=True):
    __table_args__ = (
        UniqueConstraint("notification_id", "subscription_id", name="uq_reminder_web_push_recipient_device"),
        Index("ix_reminder_web_push_due", "state", "next_attempt_at"),
    )

    notification_id: SnowflakeID = SnowflakeIDField(foreign_key=UserNotification, nullable=False)
    subscription_id: SnowflakeID = SnowflakeIDField(foreign_key=UserWebPushSubscription, nullable=False)
    endpoint_digest: str = Field(max_length=64, nullable=False)
    state: str = Field(default="pending", max_length=16, nullable=False)
    next_attempt_at: SafeDateTime = DateTimeField(default=SafeDateTime.now, nullable=False)
    last_result: str | None = Field(default=None, max_length=32)
    attempts: int = Field(default=0, nullable=False)

    def notification_data(self) -> dict:
        return {}

    def _get_repr_keys(self):
        return ["notification_id", "subscription_id", "state", "attempts"]
