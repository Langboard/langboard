"""Retained app-owned human request; not an execution or approval receipt."""

from typing import Any
from sqlalchemy import JSON, CheckConstraint, UniqueConstraint
from ...core.db import BaseDbModel, DateTimeField, Field, SnowflakeIDField
from ...core.types import SafeDateTime


class AppCardInteraction(BaseDbModel, table=True):
    __table_args__ = (
        UniqueConstraint("connection_id", "card_id", "app_request_key", name="uq_app_card_interaction_key"),
        CheckConstraint("state IN ('pending','expired','cancelled')", name="ck_app_card_interaction_state"),
    )
    project_id: int = SnowflakeIDField(nullable=False)
    card_id: int = SnowflakeIDField(nullable=False, index=True)
    connection_id: int = SnowflakeIDField(nullable=False)
    app_key: str = Field(nullable=False)
    app_request_key: str = Field(nullable=False)
    revision: int = Field(default=1, nullable=False)
    state: str = Field(default="pending", nullable=False)
    request: dict[str, Any] = Field(default_factory=dict, sa_type=JSON, nullable=False)
    workflow_snapshot: dict[str, Any] = Field(default_factory=dict, sa_type=JSON, nullable=False)
    expires_at: SafeDateTime = DateTimeField(default=SafeDateTime.now, nullable=False)

    def notification_data(self):
        return {}

    def _get_repr_keys(self):
        return ["card_id", "app_key", "revision", "state"]
