from langboard_shared.core.bootstrap import BaseCommand, BaseCommandOptions
from .CommandUtils import create_py, format_template, make_name


class CreateActivityCommandOptions(BaseCommandOptions):
    pass


class CreateActivityCommand(BaseCommand):
    @staticmethod
    def is_only_in_dev() -> bool:
        return True

    @property
    def option_class(self) -> type[CreateActivityCommandOptions]:
        return CreateActivityCommandOptions

    @property
    def command(self) -> str:
        return "act:new"

    @property
    def positional_name(self) -> str:
        return "activity name"

    @property
    def description(self) -> str:
        return "Activity model and service to create (If you give snake_case or camelCase, it will convert to PascalCase, and it will remove 'Activity' suffix)"

    @property
    def choices(self) -> list[str] | None:
        return None

    @property
    def store_type(self) -> type[bool] | type[str]:
        return str

    def execute(self, activity_name: str, _: CreateActivityCommandOptions) -> None:
        name = make_name(activity_name, "Activity")

        formats = {
            "class_name": name,
        }

        model_code = format_template("activity_db_model", formats)
        create_py("model", f"{name}Activity", model_code)

        task_code = format_template("activity_task", formats)
        create_py("task", f"activities/{name}", task_code)
