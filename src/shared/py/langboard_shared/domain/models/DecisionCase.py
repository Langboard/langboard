"""Immutable human judgment round; execution plans remain owned by GraphApprovalRequest."""

from typing import Any
from sqlalchemy import JSON
from ...core.db import BaseDbModel, DateTimeField, Field, SnowflakeIDField
from ...core.types import SafeDateTime, SnowflakeID
from .Card import Card
from .Project import Project
from .User import User


class DecisionCase(BaseDbModel, table=True):
    project_id: SnowflakeID = SnowflakeIDField(foreign_key=Project, nullable=False, index=True)
    card_id: SnowflakeID = SnowflakeIDField(foreign_key=Card, nullable=False, index=True)
    creator_id: SnowflakeID = SnowflakeIDField(foreign_key=User, nullable=False, unique_groups=("request",))
    request_id: str = Field(nullable=False, unique_groups=("request",))
    request_hash: str = Field(nullable=False)
    round_id: str = Field(nullable=False, unique=True)
    source_kind: str = Field(nullable=False)
    question: str = Field(nullable=False)
    target_revision: str = Field(nullable=False)
    policy_revision: str = Field(nullable=False)
    snapshot: dict = Field(sa_type=JSON, nullable=False)
    actor_snapshot: dict | None = Field(default=None, sa_type=JSON(none_as_null=True), nullable=True)
    deadline: SafeDateTime | None = DateTimeField(default=None, nullable=True)
    revision: int = Field(default=0, nullable=False)
    response_order: int = Field(default=0, nullable=False)
    state: str = Field(default="pending", nullable=False)

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self):
        return ["card_id", "round_id", "revision", "state"]
