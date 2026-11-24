"""Private audit evidence; never included in generic card/socket projections."""

from typing import Any
from ...core.db import BaseDbModel, Field, SnowflakeIDField
from ...core.types import SnowflakeID
from .Bot import Bot
from .Card import Card
from .User import User


class CardVisibilityChange(BaseDbModel, table=True):
    card_id: SnowflakeID = SnowflakeIDField(foreign_key=Card, nullable=False)
    changed_by_user_id: SnowflakeID | None = SnowflakeIDField(foreign_key=User, nullable=True)
    changed_by_bot_id: SnowflakeID | None = SnowflakeIDField(foreign_key=Bot, nullable=True)
    channel: str = Field(nullable=False)
    previous_visibility: str = Field(nullable=False)
    next_visibility: str = Field(nullable=False)

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self):
        return ["card_id", "changed_by_user_id", "changed_by_bot_id", "previous_visibility", "next_visibility"]
