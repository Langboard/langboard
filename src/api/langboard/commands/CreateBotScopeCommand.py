from langboard_shared.core.bootstrap import BaseCommand, BaseCommandOptions
from langboard_shared.core.utils.StringCase import StringCase
from .CommandUtils import create_py, format_template, make_name


class CreateBotScopeCommandOptions(BaseCommandOptions):
    pass


class CreateBotScopeCommand(BaseCommand):
    @staticmethod
    def is_only_in_dev() -> bool:
        return True

    @property
    def option_class(self) -> type[CreateBotScopeCommandOptions]:
        return CreateBotScopeCommandOptions

    @property
    def command(self) -> str:
        return "bot:new"

    @property
    def positional_name(self) -> str:
        return "bot scope, schedule, and log name"

    @property
    def description(self) -> str:
        return "Bot scope, schedule, and log models to create (If you give snake_case or camelCase, it will convert to PascalCase, and it will remove 'BotScope', 'BotSchedule', 'BotLog' suffix)"

    @property
    def choices(self) -> list[str] | None:
        return None

    @property
    def store_type(self) -> type[bool] | type[str]:
        return str

    def execute(self, scope_name: str, _: CreateBotScopeCommandOptions) -> None:
        name = make_name(scope_name, "BotScope")
        name = make_name(scope_name, "BotSchedule")
        name = make_name(scope_name, "BotLog")

        formats = {
            "class_name": name,
            "column_name": StringCase(name).to_snake(),
        }

        model_code = format_template("bot_schedule_db_model", formats)
        create_py("model", f"{name}BotSchedule", model_code)

        model_code = format_template("bot_scope_db_model", formats)
        create_py("model", f"{name}BotScope", model_code)

        model_code = format_template("bot_log_db_model", formats)
        create_py("model", f"{name}BotLog", model_code)
