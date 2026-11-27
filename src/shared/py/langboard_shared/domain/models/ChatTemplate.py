from typing import Any
from sqlalchemy import TEXT
from ...core.db import ApiField, BaseDbModel, Field, SnowflakeIDField
from ...core.types import SnowflakeID


class ChatTemplate(BaseDbModel, table=True):
    filterable_table: str | None = Field(None, nullable=True, api_field=ApiField())
    filterable_id: SnowflakeID | None = SnowflakeIDField(nullable=True, api_field=ApiField(name="filterable_uid"))
    name: str = Field(nullable=False, api_field=ApiField())
    template: str = Field(default="", sa_type=TEXT, api_field=ApiField())

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self) -> list[str | tuple[str, str]]:
        return ["filterable_table", "filterable_id", "name"]
