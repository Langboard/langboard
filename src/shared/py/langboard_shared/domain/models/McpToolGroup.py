from typing import Any
from ...core.db import ApiField, BaseDbModel, CSVType, DateTimeField, Field, SnowflakeIDField
from ...core.types import SafeDateTime, SnowflakeID
from .User import User


class McpToolGroup(BaseDbModel, table=True):
    user_id: SnowflakeID | None = SnowflakeIDField(foreign_key=User, nullable=True, api_field=ApiField(name="user_uid"))
    name: str = Field(nullable=False, api_field=ApiField())
    description: str = Field(default="", nullable=False, api_field=ApiField())
    tools: list[str] = Field(default=[], sa_type=CSVType(str), api_field=ApiField())
    activated_at: SafeDateTime | None = DateTimeField(default=None, nullable=True, api_field=ApiField())

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self) -> list[str | tuple[str, str]]:
        return ["user_id", "name", "activated_at"]
