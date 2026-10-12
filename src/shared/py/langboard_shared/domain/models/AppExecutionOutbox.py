"""Retained execution events committed with the human request, before delivery."""

from typing import Any
from sqlalchemy import JSON
from ...core.db import BaseDbModel, DateTimeField, Field, SnowflakeIDField
from ...core.types import SafeDateTime


class AppExecutionOutbox(BaseDbModel, table=True):
    # Receipt identities survive deletion of cards, accounts and connections.
    request_id: int = SnowflakeIDField(nullable=False, unique=True)
    project_id: int = SnowflakeIDField(nullable=False)
    app_key: str = Field(nullable=False)
    connection_id: int = SnowflakeIDField(nullable=False)
    event_type: str = Field(nullable=False)
    payload: dict = Field(default_factory=dict, sa_type=JSON, nullable=False)
    state: str = Field(default="pending", nullable=False, index=True)

    attempt: int = Field(default=0, nullable=False)
    claim_token: str | None = Field(default=None, nullable=True)
    claim_expires_at: SafeDateTime | None = DateTimeField(default=None, nullable=True)
    last_error: str | None = Field(default=None, nullable=True)
    delivery_receipts: list[dict] = Field(default_factory=list, sa_type=JSON, nullable=False)

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self):
        return ["request_id", "app_key", "state"]
