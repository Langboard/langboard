from typing import Any
from sqlalchemy import JSON
from ....core.db import ApiField, BaseDbModel, Field, SnowflakeIDField
from ....core.types import SnowflakeID
from ..Bot import Bot
from ..User import User


class BaseActivityModel(BaseDbModel):
    user_id: SnowflakeID | None = SnowflakeIDField(foreign_key=User, nullable=True)
    bot_id: SnowflakeID | None = SnowflakeIDField(foreign_key=Bot, nullable=True)
    activity_history: dict[str, Any] = Field(
        default={}, sa_type=JSON, api_field=ApiField(converter="convert_activity_history")
    )

    @classmethod
    def api_schema(cls, schema: dict | None = None) -> dict[str, Any]:
        return super().api_schema(
            {
                "filterable_map": "object",
                **(schema or {}),
            }
        )

    def convert_activity_history(self) -> dict[str, Any]:
        activity_history = {**self.activity_history}
        if "record_ids" in self.activity_history:
            record_ids = activity_history.pop("record_ids")
            for record_id, dict_key in record_ids:
                if dict_key not in activity_history:
                    activity_history[dict_key] = {}
                activity_history[dict_key]["uid"] = SnowflakeID(record_id).to_short_code()
        return activity_history

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self) -> list[str | tuple[str, str]]:
        return []
