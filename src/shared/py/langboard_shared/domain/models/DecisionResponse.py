"""Immutable actor/round response revisions; request UUIDs bind the full command."""

from typing import Any
from sqlalchemy import JSON
from ...core.db import BaseDbModel, DateTimeField, Field, SnowflakeIDField
from ...core.types import SafeDateTime, SnowflakeID
from .DecisionCase import DecisionCase
from .DecisionOption import DecisionOption
from .User import User


class DecisionResponse(BaseDbModel, table=True):
    decision_id: SnowflakeID = SnowflakeIDField(
        foreign_key=DecisionCase, nullable=False, index=True, unique_groups=("actor", "order")
    )
    round_id: str = Field(nullable=False, unique_groups=("actor",))
    actor_id: SnowflakeID = SnowflakeIDField(foreign_key=User, nullable=False, unique_groups=("actor", "request"))
    request_id: str = Field(nullable=False, unique_groups=("request",))
    request_hash: str = Field(nullable=False)
    choice: str = Field(nullable=False)
    response_revision: int = Field(
        default=1, nullable=False, unique_groups=("actor",), sa_column_kwargs={"server_default": "1"}
    )
    option_id: SnowflakeID | None = SnowflakeIDField(foreign_key=DecisionOption, nullable=True)
    response_order: int = Field(nullable=False, unique_groups=("order",))
    responded_at: SafeDateTime = DateTimeField(default=SafeDateTime.now, nullable=False)
    receipt: dict = Field(sa_type=JSON, nullable=False)

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self):
        return ["decision_id", "round_id", "actor_id", "response_order"]
