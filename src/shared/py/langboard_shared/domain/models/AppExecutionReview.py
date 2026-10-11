"""Immutable native Review movement receipt for one app execution request."""
from typing import Any
from sqlalchemy import JSON
from ...core.db import BaseDbModel, Field, SnowflakeIDField


class AppExecutionReview(BaseDbModel, table=True):
    request_id: int = SnowflakeIDField(nullable=False, unique=True)
    fingerprint: str = Field(nullable=False)
    receipt: dict[str, Any] = Field(default_factory=dict, sa_type=JSON, nullable=False)

    def notification_data(self):
        return {}

    def _get_repr_keys(self):
        return ["request_id"]
