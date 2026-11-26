"""One browser push destination per authenticated refresh session."""

from sqlalchemy import Index, Text
from ...core.db import BaseDbModel, DateTimeField, Field, SnowflakeIDField
from ...core.types import SafeDateTime, SnowflakeID
from .User import User


class UserWebPushSubscription(BaseDbModel, table=True):
    __table_args__ = (Index("ix_user_web_push_subscription_session_expires_at", "session_expires_at"),)

    user_id: SnowflakeID = SnowflakeIDField(foreign_key=User, nullable=False, index=True)
    session_digest: str = Field(max_length=64, unique=True, nullable=False)
    session_expires_at: SafeDateTime = DateTimeField(default=None, nullable=False)
    endpoint_digest: str | None = Field(default=None, max_length=64, unique=True)
    encrypted_subscription: str | None = Field(default=None, sa_type=Text)
    auth_version: int = Field(nullable=False)
    signed_out: bool = Field(default=False, nullable=False)

    def notification_data(self) -> dict:
        return {}

    def _get_repr_keys(self):
        return ["user_id", "session_expires_at", "signed_out"]
