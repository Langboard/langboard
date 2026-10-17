"""Retain server-owned vector cleanup pointers independently of deleted attachments."""

from typing import Any
from sqlalchemy import JSON, Index
from ...core.db import BaseDbModel, DateTimeField, Field
from ...core.types import SafeDateTime


class DocumentVectorCleanup(BaseDbModel, table=True):
    __table_args__ = (Index("ix_document_vector_cleanup_due_at", "due_at"),)
    pointer: dict[str, Any] = Field(sa_type=JSON, nullable=False)
    due_at: SafeDateTime = DateTimeField(default=SafeDateTime.now, nullable=False)
    claim_token: str | None = Field(default=None, nullable=True)
    attempt_count: int = Field(default=0, nullable=False)
    last_error: str | None = Field(default=None, nullable=True)

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self):
        return ["attempt_count", "due_at"]
