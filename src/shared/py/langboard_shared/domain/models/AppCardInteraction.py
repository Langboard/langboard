"""Retained app-owned human request; not an execution or approval receipt."""

from typing import Any
from sqlalchemy import JSON
from ...core.db import BaseDbModel, DateTimeField, Field, SnowflakeIDField
from ...core.types import SafeDateTime


class AppCardInteraction(BaseDbModel, table=True):
    project_id: int = SnowflakeIDField(nullable=False)
    connection_id: int = SnowflakeIDField(nullable=False, unique_groups=("key",))
    card_id: int = SnowflakeIDField(nullable=False, index=True, unique_groups=("key",))
    app_key: str = Field(nullable=False)
    app_request_key: str = Field(nullable=False, unique_groups=("key",))
    revision: int = Field(default=1, nullable=False)
    state: str = Field(default="pending", nullable=False)
    request: dict[str, Any] = Field(default_factory=dict, sa_type=JSON, nullable=False)
    authority_snapshot: dict[str, Any] = Field(default_factory=dict, sa_type=JSON, nullable=False)
    workflow_snapshot: dict[str, Any] = Field(default_factory=dict, sa_type=JSON, nullable=False)
    expires_at: SafeDateTime = DateTimeField(default=SafeDateTime.now, nullable=False)

    def notification_data(self):
        return {}

    def _get_repr_keys(self):
        return ["card_id", "app_key", "revision", "state"]
