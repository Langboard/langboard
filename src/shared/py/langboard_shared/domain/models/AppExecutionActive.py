"""Retained explicit Active handoff and its current-authority snapshot."""

from typing import Any
from sqlalchemy import JSON
from ...core.db import BaseDbModel, Field, SnowflakeIDField


class AppExecutionActive(BaseDbModel, table=True):
    lease_id: int = SnowflakeIDField(nullable=False, unique=True)
    start_id: int = SnowflakeIDField(nullable=False)
    request_id: int = SnowflakeIDField(nullable=False)
    project_id: int = SnowflakeIDField(nullable=False)
    card_id: int = SnowflakeIDField(nullable=False)
    generation: int = Field(nullable=False)
    column_id: int = SnowflakeIDField(nullable=False)
    snapshot: dict = Field(default_factory=dict, sa_type=JSON, nullable=False)
    inputs: dict = Field(default_factory=dict, sa_type=JSON, nullable=False)

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self):
        return ["lease_id", "request_id", "column_id"]
