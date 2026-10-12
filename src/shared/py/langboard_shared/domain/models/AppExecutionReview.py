"""Retained stopped-to-Review handoff; never human approval."""

from typing import Any
from sqlalchemy import JSON
from ...core.db import BaseDbModel, Field, SnowflakeIDField


class AppExecutionReview(BaseDbModel, table=True):
    request_id: int = SnowflakeIDField(nullable=False, unique=True)
    snapshot: dict = Field(default_factory=dict, sa_type=JSON, nullable=False)
    receipt: dict = Field(default_factory=dict, sa_type=JSON, nullable=False)

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self):
        return ["request_id"]
