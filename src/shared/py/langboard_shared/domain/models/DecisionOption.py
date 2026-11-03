"""Relational immutable options for one named single-choice Poll round."""

from typing import Any
from ...core.db import BaseDbModel, Field, SnowflakeIDField
from ...core.types import SnowflakeID
from .DecisionCase import DecisionCase


class DecisionOption(BaseDbModel, table=True):
    decision_id: SnowflakeID = SnowflakeIDField(
        foreign_key=DecisionCase, nullable=False, index=True, unique_groups=("key", "position")
    )
    key: str = Field(nullable=False, unique_groups=("key",))
    label: str = Field(nullable=False)
    position: int = Field(nullable=False, unique_groups=("position",))

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self):
        return ["decision_id", "key", "position"]
