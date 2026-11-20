"""Derived comment summaries; source text and provider secrets are never stored here."""

from typing import Any
from sqlalchemy import JSON, CheckConstraint
from ...core.db import BaseDbModel, DateTimeField, Field, SnowflakeIDField
from ...core.types import SafeDateTime, SnowflakeID
from .Card import Card
from .InternalBot import InternalBot
from .User import User


class CardCommentCompaction(BaseDbModel, table=True):
    __table_args__ = (
        CheckConstraint("status IN ('pending','running','completed','failed','cancelled')", name="comment_compaction_status"),
        CheckConstraint("attempt >= 0", name="comment_compaction_attempt"),
        CheckConstraint("(status = 'completed' AND draft IS NOT NULL) OR (status <> 'completed' AND draft IS NULL)",
                        name="comment_compaction_draft_state"),
    )
    request_key: str = Field(nullable=False, unique=True, max_length=64)
    card_id: SnowflakeID = SnowflakeIDField(foreign_key=Card, nullable=False, index=True)
    # NULL identifies the dedicated internal_bot_id as the automatic principal.
    # Never put an InternalBot ID in the User foreign key.
    user_id: SnowflakeID | None = SnowflakeIDField(foreign_key=User, nullable=True)
    internal_bot_id: SnowflakeID = SnowflakeIDField(foreign_key=InternalBot, nullable=False)
    source_revision: str = Field(nullable=False, max_length=64)
    provider_revision: str = Field(nullable=False, max_length=64)
    policy_version: int = Field(default=1, nullable=False)
    source_manifest: list[dict[str, str]] = Field(nullable=False, sa_type=JSON)
    status: str = Field(default="pending", nullable=False, max_length=20)
    attempt: int = Field(default=0, nullable=False)
    lease_expires_at: SafeDateTime | None = DateTimeField(default=None, nullable=True)
    draft: dict[str, Any] | None = Field(default=None, nullable=True, sa_type=JSON(none_as_null=True))
    error_code: str | None = Field(default=None, nullable=True, max_length=32)

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self):
        return ["card_id", "status", "attempt"]
