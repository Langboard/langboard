"""Explicit human run intent; counters and retained requests are not runtime permits."""

from typing import Any
from sqlalchemy import JSON, Column, ForeignKey, Integer, MetaData, Table
from sqlalchemy.orm import registry
from ...core.db import BaseDbModel, Field, SnowflakeIDField
from ...core.db.ColumnTypes import SnowflakeIDType
from .Card import Card


# This counter predates app runtime tables. It has no application-row ID or
# timestamps. Keep its migration-owned shape and avoid registering a duplicate
# table in BaseDbModel.metadata during native card/schema initialization.
class CardExecutionGeneration:
    __table__ = Table(
        "card_execution_generation", MetaData(naming_convention=BaseDbModel.metadata.naming_convention),
        Column("card_id", SnowflakeIDType(), ForeignKey(Card.__table__.c.id, ondelete="CASCADE"), primary_key=True),
        Column("execution_generation", Integer, nullable=False, default=0, server_default="0"),
    )


registry().map_imperatively(CardExecutionGeneration, CardExecutionGeneration.__table__)


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
