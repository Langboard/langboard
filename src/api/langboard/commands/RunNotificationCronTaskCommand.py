from asyncio import run
from langboard_shared.core.bootstrap import BaseCommand, BaseCommandOptions


class RunNotificationCronTaskCommandOptions(BaseCommandOptions):
    pass


class RunNotificationCronTaskCommand(BaseCommand):
    @staticmethod
    def is_only_in_dev() -> bool:
        return True

    @property
    def option_class(self) -> type[RunNotificationCronTaskCommandOptions]:
        return RunNotificationCronTaskCommandOptions

    @property
    def command(self) -> str:
        return "run:notification:cron"

    @property
    def positional_name(self) -> str:
        return "cron interval string"

    @property
    def description(self) -> str:
        return "Run the scheduled notification task (DO NOT USE THIS COMMAND DIRECTLY)"

    @property
    def choices(self) -> list[str] | None:
        return None

    @property
    def store_type(self) -> type[bool] | type[str]:
        return str

    def execute(self, cron_time_str: str, _: RunNotificationCronTaskCommandOptions) -> None:
        from langboard_shared.tasks.notifications.NotificationScheduleTask import run_scheduled_notifications

        run(run_scheduled_notifications(cron_time_str))
