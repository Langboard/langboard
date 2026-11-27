from typing import Any
from ...core.db import ApiField, BaseDbModel, Field, SnowflakeIDField
from ...core.types import SnowflakeID
from .bases.BotTriggerCondition import BotTriggerCondition
from .Bot import Bot
from .Card import Card
from .Project import Project
from .ProjectColumn import ProjectColumn


class BotDefaultScopeBranch(BaseDbModel, table=True):
    bot_id: SnowflakeID = SnowflakeIDField(foreign_key=Bot, index=True, api_field=ApiField(name="bot_uid"))
    name: str = Field(nullable=False, api_field=ApiField())

    @classmethod
    def api_schema(cls, schema: dict | None = None) -> dict[str, Any]:
        return super().api_schema(
            {
                "conditions_map": {
                    f"Enum[{', '.join([Project.__tablename__, ProjectColumn.__tablename__, Card.__tablename__])}]": [
                        BotTriggerCondition
                    ]
                },
                **(schema or {}),
            }
        )

    def api_response(self, conditions_map: dict[str, list[str]]) -> dict[str, Any]:
        return {
            **super().api_response(),
            "conditions_map": conditions_map,
        }

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self) -> list[str | tuple[str, str]]:
        return ["name"]
