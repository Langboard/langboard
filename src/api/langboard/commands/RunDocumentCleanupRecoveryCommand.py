from langboard_shared.core.bootstrap import BaseCommand, BaseCommandOptions
from langboard_shared.tasks.docling.DocumentCleanupTask import recover_cleanup


class RunDocumentCleanupRecoveryCommandOptions(BaseCommandOptions):
    pass


class RunDocumentCleanupRecoveryCommand(BaseCommand):
    @staticmethod
    def is_only_in_dev() -> bool:
        return False

    @property
    def option_class(self) -> type[RunDocumentCleanupRecoveryCommandOptions]:
        return RunDocumentCleanupRecoveryCommandOptions

    @property
    def command(self) -> str:
        return "run:document-cleanup:recover"

    @property
    def positional_name(self) -> str:
        return ""

    @property
    def description(self) -> str:
        return "Recover due document vector cleanup requests"

    @property
    def choices(self) -> list[str] | None:
        return None

    @property
    def store_type(self) -> type[bool] | type[str]:
        return bool

    def execute(self, _: RunDocumentCleanupRecoveryCommandOptions) -> None:
        recover_cleanup()
