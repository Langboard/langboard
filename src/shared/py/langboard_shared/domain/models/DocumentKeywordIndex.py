"""Desired document projection state retained through source/connection deletion."""

from typing import Any
from sqlalchemy import JSON, BigInteger
from ...core.db import BaseDbModel, DateTimeField, Field, SnowflakeIDField
from ...core.types import SafeDateTime, SnowflakeID


class DocumentKeywordIndex(BaseDbModel, table=True):
    attachment_id: SnowflakeID = SnowflakeIDField(nullable=False, unique_groups=("destination",))
    card_id: SnowflakeID = SnowflakeIDField(nullable=False)
    project_id: SnowflakeID = SnowflakeIDField(nullable=False)
    destination_fingerprint: str = Field(nullable=False, unique_groups=("destination",))
    destination: dict[str, str] = Field(sa_type=JSON, nullable=False, exclude=True)
    connection_id: SnowflakeID | None = SnowflakeIDField(nullable=True, index=True)
    source_generation: str = Field(nullable=False)
    version: int = Field(default=1, sa_type=BigInteger, nullable=False)
    completed_version: int = Field(default=0, sa_type=BigInteger, nullable=False)
    operation: str = Field(default="index", nullable=False)
    due_at: SafeDateTime | None = DateTimeField(default=None, nullable=True)
    claim_token: str | None = Field(default=None, nullable=True)
    attempt_count: int = Field(default=0, nullable=False)
    last_error: str | None = Field(default=None, nullable=True)

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self):
        return ["version", "operation", "due_at"]
