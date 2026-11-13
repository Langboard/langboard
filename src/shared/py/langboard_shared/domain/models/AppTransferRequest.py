"""Durable human transfer intent; never an execution request or resource grant."""

from typing import Any
from sqlalchemy import JSON
from ...core.db import BaseDbModel, Field, SnowflakeIDField


class AppTransferRequest(BaseDbModel, table=True):
    actor_id: int = SnowflakeIDField(nullable=False, index=True, unique_groups=("client_request",))
    client_request_id: str = Field(nullable=False, max_length=36, unique_groups=("client_request",))
    project_id: int = SnowflakeIDField(nullable=False, index=True)
    card_id: int = SnowflakeIDField(nullable=False, index=True)
    connection_id: int = SnowflakeIDField(nullable=False)
    app_key: str = Field(nullable=False)
    intent: dict[str, Any] = Field(default_factory=dict, sa_type=JSON, nullable=False)
    authority: dict[str, Any] = Field(default_factory=dict, sa_type=JSON, nullable=False)
    receipt: dict[str, Any] = Field(default_factory=dict, sa_type=JSON, nullable=False)

    def notification_data(self):
        return {}

    def _get_repr_keys(self):
        return ["card_id", "app_key"]
