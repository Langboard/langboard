from enum import Enum
from typing import Any, ClassVar
from ...core.db import ApiField, BaseDbModel, DateTimeField, EnumLikeType, Field, SnowflakeIDField
from ...core.types import SafeDateTime, SnowflakeID
from .Bot import Bot


class BotScheduleRunningType(Enum):
    Infinite = "infinite"
    Duration = "duration"
    Reserved = "reserved"
    Onetime = "onetime"


class BotScheduleStatus(Enum):
    Pending = "pending"  # The schedule is pending and not yet started (BotScheduleRunningType.Duration, BotScheduleRunningType.Reserved, BotScheduleRunningType.Onetime)
    Started = "started"  # The schedule is started and running (BotScheduleRunningType.Infinite, BotScheduleRunningType.Duration, BotScheduleRunningType.Reserved)
    Stopped = "stopped"  # The schedule is stopped and not running (BotScheduleRunningType.Infinite, BotScheduleRunningType.Duration, BotScheduleRunningType.Reserved, BotScheduleRunningType.Onetime)


class BotSchedule(BaseDbModel, table=True):
    RUNNING_TYPES_WITH_START_AT: ClassVar[list[BotScheduleRunningType]] = [
        BotScheduleRunningType.Duration,
        BotScheduleRunningType.Reserved,
        BotScheduleRunningType.Onetime,
    ]
    RUNNING_TYPES_WITH_END_AT: ClassVar[list[BotScheduleRunningType]] = [BotScheduleRunningType.Duration]
    bot_id: SnowflakeID = SnowflakeIDField(
        foreign_key=Bot, nullable=False, index=True, api_field=ApiField(name="bot_uid")
    )
    running_type: BotScheduleRunningType = Field(
        default=BotScheduleRunningType.Infinite,
        nullable=False,
        sa_type=EnumLikeType(BotScheduleRunningType),
        api_field=ApiField(),
    )
    status: BotScheduleStatus = Field(nullable=False, sa_type=EnumLikeType(BotScheduleStatus), api_field=ApiField())
    interval_str: str = Field(nullable=False, api_field=ApiField())
    start_at: SafeDateTime | None = DateTimeField(default=None, nullable=True, api_field=ApiField())
    end_at: SafeDateTime | None = DateTimeField(default=None, nullable=True, api_field=ApiField())

    def notification_data(self) -> dict[str, Any]:
        return {}

    def _get_repr_keys(self) -> list[str | tuple[str, str]]:
        return ["bot_id", "running_type", "status", "interval_str", "start_at", "end_at"]
