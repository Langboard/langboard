"""Explicit personal subscriptions to ordinary card activity."""

from typing import Literal
from ...core.db import BaseDbModel, Field, SnowflakeIDField
from ...core.types import SnowflakeID
from .Card import Card
from .User import User


CardActivityMode = Literal["ignore", "comments", "comments_and_moves"]


class UserCardNotificationPreference(BaseDbModel, table=True):
    user_id: SnowflakeID = SnowflakeIDField(foreign_key=User, nullable=False, unique_groups=("subscription",))
    card_id: SnowflakeID = SnowflakeIDField(
        foreign_key=Card, nullable=False, index=True, unique_groups=("subscription",)
    )
    mode: str = Field(default="ignore", nullable=False)

    def notification_data(self) -> dict:
        return {}

    def _get_repr_keys(self):
        return ["user_id", "card_id", "mode"]
