from typing import Any
from ...core.db import ApiField, BaseDbModel, Field, SnowflakeIDField
from ...core.types import SnowflakeID
from .User import User


class UserGroup(BaseDbModel, table=True):
    user_id: SnowflakeID = SnowflakeIDField(foreign_key=User, nullable=False, index=True)
    name: str = Field(nullable=False, api_field=ApiField())
    order: int = Field(nullable=False, default=0, api_field=ApiField())

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self) -> list[str | tuple[str, str]]:
        return ["name", "order"]
