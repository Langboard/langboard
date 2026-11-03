from abc import abstractmethod
from typing import Any
from ....core.db import ApiField, BaseDbModel, CSVType, Field, SnowflakeIDField
from ....core.types import SnowflakeID
from ...constants.DomainEventType import DomainEventType
from ..BotDefaultScopeBranch import BotDefaultScopeBranch


class BaseBotDefaultScope(BaseDbModel):
    bot_default_scope_branch_id: SnowflakeID = SnowflakeIDField(
        foreign_key=BotDefaultScopeBranch, index=True, api_field=ApiField(name="bot_default_scope_branch_uid")
    )
    conditions: list[DomainEventType] = Field(
        nullable=False, sa_type=CSVType(DomainEventType), api_field=ApiField()
    )

    @staticmethod
    @abstractmethod
    def get_available_conditions() -> set[DomainEventType]: ...

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self) -> list[str | tuple[str, str]]:
        return ["bot_default_scope_branch_id"]
