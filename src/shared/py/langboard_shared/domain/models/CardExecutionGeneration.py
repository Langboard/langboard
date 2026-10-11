"""Explicit human run intent; counters and retained requests are not runtime permits."""

from typing import Any
from sqlalchemy import JSON
from ...core.db import BaseDbModel, Field, SnowflakeIDField
from .Card import Card


class CardExecutionGeneration(BaseDbModel, table=True):
    card_id: int = SnowflakeIDField(foreign_key=Card, nullable=False, unique=True)
    execution_generation: int = Field(default=0, nullable=False)

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self):
        return ["card_id", "execution_generation"]


class CardExecutionIntent(BaseDbModel, table=True):
    # No foreign keys: requests remain available after card/account deletion.
    card_id: int = SnowflakeIDField(nullable=False, index=True, unique_groups=("request", "generation"))
    actor_id: int = SnowflakeIDField(nullable=False)
    idempotency_key: str = Field(nullable=False, unique_groups=("request",))
    execution_generation: int = Field(nullable=False, unique_groups=("generation",))
    app_key: str = Field(nullable=False)
    connection_id: int = SnowflakeIDField(nullable=False)
    snapshot: dict = Field(default_factory=dict, sa_type=JSON, nullable=False)

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self):
        return ["card_id", "app_key", "execution_generation"]
