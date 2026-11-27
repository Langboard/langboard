from abc import abstractmethod
from typing import Any
from ....core.db import BaseDbModel, DateTimeField, SnowflakeIDField
from ....core.types import SafeDateTime, SnowflakeID
from ..BotSchedule import BotSchedule


class BaseBotScheduleModel(BaseDbModel):
    bot_schedule_id: SnowflakeID = SnowflakeIDField(foreign_key=BotSchedule, nullable=False, index=True)
    last_rnu_at: SafeDateTime | None = DateTimeField(default=None, nullable=True)

    @staticmethod
    @abstractmethod
    def get_scope_column_name() -> str: ...

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self) -> list[str | tuple[str, str]]:
        keys: list[str | tuple[str, str]] = ["bot_schedule_id"]
        keys.extend([field for field in self.model_fields if field not in BaseBotScheduleModel.model_fields])
        return keys
