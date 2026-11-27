from typing import Any
from ...core.db import ApiField, BaseDbModel, DateTimeField, Field, SnowflakeIDField
from ...core.types import SafeDateTime, SnowflakeID
from .User import User


class ChatSession(BaseDbModel, table=True):
    user_id: SnowflakeID = SnowflakeIDField(foreign_key=User, index=True, api_field=ApiField(name="user_uid"))
    title: str = Field(default="", nullable=False, api_field=ApiField())
    api_permission_level: str = Field(
        default="read", nullable=False, sa_column_kwargs={"server_default": "read"}, api_field=ApiField()
    )
    last_messaged_at: SafeDateTime | None = DateTimeField(default=None, nullable=True, api_field=ApiField())

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self) -> list[str | tuple[str, str]]:
        return ["user_id", "title", "api_permission_level", "last_messaged_at"]
