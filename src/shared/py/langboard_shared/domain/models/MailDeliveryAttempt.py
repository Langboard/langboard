"""Safe per-invocation SMTP evidence; never message content or transport replies."""

from typing import Any
from sqlalchemy import JSON, Index
from ...core.db import BaseDbModel, DateTimeField, Field
from ...core.types import SafeDateTime


class MailDeliveryAttempt(BaseDbModel, table=True):
    __table_args__ = (Index("ix_mail_delivery_attempt_started_at", "started_at"),)

    started_at: SafeDateTime = DateTimeField(default=SafeDateTime.now, nullable=False)
    completed_at: SafeDateTime | None = DateTimeField(default=None, nullable=True)
    outcome: str = Field(default="unknown", nullable=False, max_length=16, index=True)
    safe_error_code: str | None = Field(default=None, nullable=True, max_length=40)
    recipient_masked: str = Field(nullable=False, max_length=16)
    subject: str | None = Field(default=None, nullable=True, max_length=160)
    template_name: str | None = Field(default=None, nullable=True, max_length=64)
    trigger: str = Field(default="direct", nullable=False, max_length=64)
    source: str | None = Field(default=None, nullable=True, max_length=32)
    source_uid: str | None = Field(default=None, nullable=True, max_length=11, index=True)
    linked_ids: dict[str, Any] | None = Field(default=None, nullable=True, sa_type=JSON)

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self) -> list[str | tuple[str, str]]:
        return ["outcome", "trigger"]
