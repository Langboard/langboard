from langboard_shared.core.routing import BaseFormModel, form_model
from langboard_shared.core.schema import TimeBasedPagination
from langboard_shared.core.types import SafeDateTime
from langboard_shared.core.types.BotRelatedTypes import AVAILABLE_BOT_TARGET_TABLES
from langboard_shared.domain.models.BotSchedule import BotSchedule, BotScheduleRunningType, BotScheduleStatus
from pydantic import Field


class BotSchedulePagination(TimeBasedPagination):
    status: BotScheduleStatus | None = Field(
        default=None, title=f"Status: {', '.join(BotScheduleStatus.__members__.keys())} (Default: None)"
    )


@form_model
class CreateBotCronTimeForm(BaseFormModel):
    interval_str: str = Field(..., title="Cron interval string (UNIX crontab format - * * * * *)")
    running_type: BotScheduleRunningType | None = Field(
        default=BotScheduleRunningType.Infinite,
        title=f"Running type: {', '.join(BotScheduleRunningType.__members__.keys())} (Default: {BotScheduleRunningType.Infinite.name})",
    )
    target_table: str = Field(..., title=f"Target table name ({', '.join(AVAILABLE_BOT_TARGET_TABLES.keys())})")
    target_uid: str = Field(..., title="Target UID")
    start_at: SafeDateTime | None = Field(
        default=None,
        title=f"Start time (Required if running_type is one of {', '.join([schedule_type.name for schedule_type in BotSchedule.RUNNING_TYPES_WITH_START_AT])})",
    )
    end_at: SafeDateTime | None = Field(
        default=None,
        title=f"End time (Required if running_type is {', '.join([schedule_type.name for schedule_type in BotSchedule.RUNNING_TYPES_WITH_END_AT])})",
    )
    timezone: str | float = Field(
        default="UTC",
        title="Timezone (Default: UTC). Can be a string like 'Europe/Moscow' or a float like 3.0 for UTC+3)",
    )


@form_model
class UpdateBotCronTimeForm(BaseFormModel):
    interval_str: str | None = Field(default=None, title="Cron interval string (UNIX crontab format - * * * * *)")
    running_type: BotScheduleRunningType | None = Field(
        default=None,
        title=f"Running type: {', '.join(BotScheduleRunningType.__members__.keys())}",
    )
    target_table: str = Field(..., title=f"Target table name ({', '.join(AVAILABLE_BOT_TARGET_TABLES.keys())})")
    target_uid: str | None = Field(default=None, title="Target UID")
    project_uid: str | None = Field(default=None, title="Project UID")
    start_at: SafeDateTime | None = Field(
        default=None,
        title=f"Start time (Required if running_type is one of {', '.join([schedule_type.name for schedule_type in BotSchedule.RUNNING_TYPES_WITH_START_AT])})",
    )
    end_at: SafeDateTime | None = Field(
        default=None,
        title=f"End time (Required if running_type is {', '.join([schedule_type.name for schedule_type in BotSchedule.RUNNING_TYPES_WITH_END_AT])})",
    )
    timezone: str | float = Field(
        default="UTC",
        title="Timezone (Default: UTC). Can be a string like 'Europe/Moscow' or a float like 3.0 for UTC+3)",
    )


@form_model
class DeleteBotCronTimeForm(BaseFormModel):
    target_table: str = Field(..., title=f"Target table name ({', '.join(AVAILABLE_BOT_TARGET_TABLES.keys())})")
    target_uid: str | None = Field(default=None, title="Target UID")
    project_uid: str | None = Field(default=None, title="Project UID")
