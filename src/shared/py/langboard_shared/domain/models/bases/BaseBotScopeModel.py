from abc import abstractmethod
from typing import Any
from ....core.db import ApiField, BaseDbModel, CSVType, Field, SnowflakeIDField
from ....core.types import SnowflakeID
from ..Bot import Bot
from ..BotDefaultScopeBranch import BotDefaultScopeBranch
from .BotTriggerCondition import BotTriggerCondition


class BaseBotScopeModel(BaseDbModel):
    bot_id: SnowflakeID = SnowflakeIDField(foreign_key=Bot, index=True, api_field=ApiField(name="bot_uid"))
    default_scope_branch_id: SnowflakeID | None = SnowflakeIDField(
        foreign_key=BotDefaultScopeBranch,
        nullable=True,
        index=True,
        api_field=ApiField(name="default_scope_branch_uid"),
    )
    conditions: list[BotTriggerCondition] = Field(
        nullable=False, sa_type=CSVType(BotTriggerCondition), api_field=ApiField()
    )
    is_frozen: bool = Field(default=False, nullable=False, api_field=ApiField())

    @staticmethod
    @abstractmethod
    def get_available_conditions() -> set[BotTriggerCondition]: ...

    @staticmethod
    @abstractmethod
    def get_scope_column_name() -> str: ...

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self) -> list[str | tuple[str, str]]:
        keys: list[str | tuple[str, str]] = ["bot_id"]
        keys.append(self.get_scope_column_name())
        return keys
