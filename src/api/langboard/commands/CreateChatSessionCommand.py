from langboard_shared.core.bootstrap import BaseCommand, BaseCommandOptions
from langboard_shared.core.utils.StringCase import StringCase
from .CommandUtils import create_py, format_template, make_name


class CreateChatSessionCommandOptions(BaseCommandOptions):
    pass


class CreateChatSessionCommand(BaseCommand):
    @staticmethod
    def is_only_in_dev() -> bool:
        return True

    @property
    def option_class(self) -> type[CreateChatSessionCommandOptions]:
        return CreateChatSessionCommandOptions

    @property
    def command(self) -> str:
        return "chat:new"

    @property
    def positional_name(self) -> str:
        return "chat session model name"

    @property
    def description(self) -> str:
        return "Chat session model to create (If you give snake_case or camelCase, it will convert to PascalCase, and it will remove 'ChatSession' suffix)"

    @property
    def choices(self) -> list[str] | None:
        return None

    @property
    def store_type(self) -> type[bool] | type[str]:
        return str

    def execute(self, name: str, _: CreateChatSessionCommandOptions) -> None:
        name = make_name(name, "ChatSession")

        formats = {
            "class_name": name,
            "column_name": StringCase(name).to_snake(),
        }

        model_code = format_template("chat_session_db_model.py", formats)
        create_py("model", f"{name}ChatSession", model_code)
