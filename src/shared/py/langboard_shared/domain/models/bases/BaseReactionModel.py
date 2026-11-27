from abc import abstractmethod
from typing import Any
from ....core.db import BaseDbModel, Field, SnowflakeIDField
from ....core.types import SnowflakeID
from ..Bot import Bot
from ..User import User


REACTION_TYPES = [
    "check-mark",
    "confusing",
    "eyes",
    "heart",
    "laughing",
    "party-popper",
    "rocket",
    "thumbs-down",
    "thumbs-up",
]


class BaseReactionModel(BaseDbModel):
    user_id: SnowflakeID | None = SnowflakeIDField(foreign_key=User, nullable=True)
    bot_id: SnowflakeID | None = SnowflakeIDField(foreign_key=Bot, nullable=True)
    reaction_type: str = Field(nullable=False)

    @staticmethod
    @abstractmethod
    def get_target_column_name() -> str: ...

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self) -> list[str | tuple[str, str]]:
        return []
