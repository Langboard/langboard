from langboard_shared.core.bootstrap import BaseCommand, BaseCommandOptions
from pydantic import Field
from .CommandUtils import create_py, format_template, make_name


class CreateModelCommandOptions(BaseCommandOptions):
    soft_delete: bool = Field(default=False, description="Use soft delete feature", short="sdel")  # type: ignore


class CreateModelCommand(BaseCommand):
    @staticmethod
    def is_only_in_dev() -> bool:
        return True

    @property
    def option_class(self) -> type[CreateModelCommandOptions]:
        return CreateModelCommandOptions

    @property
    def command(self) -> str:
        return "model:new"

    @property
    def positional_name(self) -> str:
        return "model name"

    @property
    def description(self) -> str:
        return "Model to create (If you give snake_case or camelCase, it will convert to PascalCase)"

    @property
    def choices(self) -> list[str] | None:
        return None

    @property
    def store_type(self) -> type[bool] | type[str]:
        return str

    def execute(self, model_name: str, options: CreateModelCommandOptions) -> None:
        """Creates a new model file in the models directory."""
        name = make_name(model_name)

        base_model = "BaseDbModel"
        if options.soft_delete:
            base_model = "SoftDeleteModel"

        formats = {
            "class_name": name,
            "base_class": base_model,
        }

        code = format_template("db_model", formats)

        create_py("model", name, code)
