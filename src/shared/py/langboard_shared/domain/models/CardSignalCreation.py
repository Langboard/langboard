from typing import Any
from ...core.db import BaseDbModel, Field, SnowflakeIDField
from .AppResourceBinding import AppResourceBinding
from .Card import Card
from .User import User


class CardSignalCreation(BaseDbModel, table=True):
    """Actor-scoped creation receipt; unlink never permits accidental duplicate creation."""

    actor_id: int = SnowflakeIDField(foreign_key=User, nullable=False)
    resource_id: int = SnowflakeIDField(foreign_key=AppResourceBinding, nullable=False)
    external_id: str = Field(nullable=False)
    commit_sha: str = Field(nullable=False)
    card_id: int = SnowflakeIDField(foreign_key=Card, nullable=False)

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self):
        return ["actor_id", "resource_id", "card_id"]
