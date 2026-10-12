"""Retained app receipt of a delivered request; never an execution permit."""

from typing import Any
from ...core.db import BaseDbModel, Field, SnowflakeIDField


class AppExecutionAcknowledgment(BaseDbModel, table=True):
    request_id: int = SnowflakeIDField(nullable=False, unique=True)
    event_id: int = SnowflakeIDField(nullable=False, unique=True)
    app_key: str = Field(nullable=False)
    connection_id: int = SnowflakeIDField(nullable=False)
    runtime_reference: str = Field(nullable=False, max_length=200)

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self):
        return ["request_id", "event_id", "app_key"]
