from typing import Any
from ...core.db import BaseDbModel, SnowflakeIDField
from ...core.types import SnowflakeID
from .Card import Card
from .ProjectAssignedUser import ProjectAssignedUser
from .User import User


class CardAssignedUser(BaseDbModel, table=True):
    project_assigned_id: SnowflakeID = SnowflakeIDField(foreign_key=ProjectAssignedUser, nullable=False, index=True)
    card_id: SnowflakeID = SnowflakeIDField(foreign_key=Card, nullable=False, index=True)
    user_id: SnowflakeID = SnowflakeIDField(foreign_key=User, nullable=False, index=True)

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self) -> list[str | tuple[str, str]]:
        return ["project_assigned_id", "card_id", "user_id"]
