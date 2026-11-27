from langboard_shared.core.bootstrap import BaseCommand, BaseCommandOptions
from langboard_shared.core.utils.StringCase import StringCase
from .CommandUtils import create_factory_py, create_py, format_template, make_name


class CreateRoleCommandOptions(BaseCommandOptions):
    pass


class CreateRoleCommand(BaseCommand):
    @staticmethod
    def is_only_in_dev() -> bool:
        return True

    @property
    def option_class(self) -> type[CreateRoleCommandOptions]:
        return CreateRoleCommandOptions

    @property
    def command(self) -> str:
        return "role:new"

    @property
    def positional_name(self) -> str:
        return "role name"

    @property
    def description(self) -> str:
        return "Role model and repository to create (If you give snake_case or camelCase, it will convert to PascalCase, and it will remove 'Role' suffix)"

    @property
    def choices(self) -> list[str] | None:
        return None

    @property
    def store_type(self) -> type[bool] | type[str]:
        return str

    def execute(self, role_name: str, _: CreateRoleCommandOptions) -> None:
        name = make_name(role_name, "Role")

        formats = {
            "class_name": name,
            "snake_name": StringCase(name).to_snake(),
        }

        model_code = format_template("role_db_model", formats)
        create_py("model", f"{name}Role", model_code)

        repository_code = format_template("role_repository", formats)
        create_factory_py("repository", name, repository_code, factory=("roles", "Role"))
