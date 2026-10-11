"""Immutable native workflow movement receipt for one accepted app answer."""

from typing import Any
from sqlalchemy import JSON
from ...core.db import BaseDbModel, Field, SnowflakeIDField


class AppCardInteractionTransition(BaseDbModel, table=True):
    response_id: int = SnowflakeIDField(nullable=False, unique=True)
    actor_id: int = SnowflakeIDField(nullable=False)
    expected_card_revision: int = Field(nullable=False)
    receipt: dict[str, Any] = Field(default_factory=dict, sa_type=JSON, nullable=False)

    def notification_data(self):
        return {}

    def _get_repr_keys(self):
        return ["response_id", "actor_id"]
