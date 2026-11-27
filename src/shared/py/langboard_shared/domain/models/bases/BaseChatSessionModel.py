from typing import Any
from ....core.db import BaseDbModel, SnowflakeIDField
from ....core.types import SnowflakeID
from ..ChatSession import ChatSession


class BaseChatSessionModel(BaseDbModel):
    chat_session_id: SnowflakeID = SnowflakeIDField(foreign_key=ChatSession, index=True)

    @classmethod
    def get_filterable_column(cls) -> str:
        return f"{cls.__tablename__.replace('_chat_session', '')}_id"

    @classmethod
    def api_schema(cls, schema: dict | None = None) -> dict[str, Any]:
        return super().api_schema(
            {
                "filterable_table": "string",
                "filterable_uid": "string",
                **(schema or {}),
            }
        )

    def api_response(self) -> dict[str, Any]:
        return {
            "filterable_table": self.__tablename__.replace("_chat_session", ""),
            **super().api_response(),
        }

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self) -> list[str | tuple[str, str]]:
        keys: list[str | tuple[str, str]] = ["chat_session_id"]
        keys.extend([field for field in self.model_fields if field not in BaseChatSessionModel.model_fields])
        return keys
