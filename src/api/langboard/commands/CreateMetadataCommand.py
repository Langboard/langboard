from langboard_shared.core.bootstrap import BaseCommand, BaseCommandOptions
from langboard_shared.core.utils.StringCase import StringCase
from .CommandUtils import create_py, format_template, make_name


class CreateMetadataCommandOptions(BaseCommandOptions):
    pass


class CreateMetadataCommand(BaseCommand):
    @staticmethod
    def is_only_in_dev() -> bool:
        return True

    @property
    def option_class(self) -> type[CreateMetadataCommandOptions]:
        return CreateMetadataCommandOptions

    @property
    def command(self) -> str:
        return "meta:new"

    @property
    def positional_name(self) -> str:
        return "metadata name"

    @property
    def description(self) -> str:
        return "Metadata model to create (If you give snake_case or camelCase, it will convert to PascalCase, and it will remove 'Metadata' suffix)"

    @property
    def choices(self) -> list[str] | None:
        return None

    @property
    def store_type(self) -> type[bool] | type[str]:
        return str

    def execute(self, metadata_name: str, _: CreateMetadataCommandOptions) -> None:
        name = make_name(metadata_name, "Metadata")
        snake_name = StringCase(name).to_snake()

        formats = {
            "class_name": name,
            "snake_name": snake_name,
        }

        model_code = format_template("metadata_db_model", formats)
        create_py("model", f"{name}Metadata", model_code)
