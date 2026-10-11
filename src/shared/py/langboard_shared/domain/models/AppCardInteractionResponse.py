"""Immutable human answer receipt; independent of transport and execution outcomes."""

from typing import Any
from sqlalchemy import JSON
from ...core.db import BaseDbModel, Field, SnowflakeIDField


class AppCardInteractionResponse(BaseDbModel, table=True):
    interaction_id: int = SnowflakeIDField(nullable=False, unique=True)
    actor_id: int = SnowflakeIDField(nullable=False)
    expected_revision: int = Field(nullable=False)
    idempotency_key: str = Field(nullable=False)
    execution_generation: int | None = Field(default=None, nullable=True)
    values: dict[str, Any] = Field(default_factory=dict, sa_type=JSON, nullable=False)

    def notification_data(self):
        return {}

    def _get_repr_keys(self):
        return ["interaction_id", "actor_id", "expected_revision"]
