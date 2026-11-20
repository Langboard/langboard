"""Provision a separate compactor from an explicitly selected provider connection."""

from json import dumps, loads
from langboard_shared.core.bootstrap import BaseCommand, BaseCommandOptions
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.types import SnowflakeID
from langboard_shared.domain.models import InternalBot
from langboard_shared.domain.models.BaseBotModel import BotPlatform, BotPlatformRunningType
from langboard_shared.domain.models.InternalBot import InternalBotType
from langboard_shared.publishers import InternalBotPublisher
from pydantic import Field


class CreateCommentCompactorCommandOptions(BaseCommandOptions):
    provider_uid: str = Field(default="", description="Existing Z.ai Coding Plan connection to use for the new compactor")


def create_comment_compactor(provider_uid: str) -> dict:
    """Copy only connection fields, never the chat profile's tools, prompt or identity."""
    if not provider_uid:
        raise ValueError("An explicit provider profile is required")
    try:
        source_id = SnowflakeID.from_short_code(provider_uid)
    except (ValueError, TypeError):
        raise ValueError("Invalid provider profile") from None
    with DbSession.atomic() as db:
        # The connection row also serializes repeated provisioning from this source.
        source = db.exec(SqlBuilder.select.table(InternalBot).where(
            InternalBot.id == source_id, InternalBot.deleted_at.is_(None),
        ).with_for_update().execution_options(populate_existing=True)).first()
        if not source or source.platform != BotPlatform.Default or source.platform_running_type != BotPlatformRunningType.Default or source.api_url:
            raise ValueError("A native Z.ai Coding Plan connection is required")
        try:
            settings = loads(source.value)
            if (not isinstance(settings, dict) or settings.get("agent_llm") != "Z.ai Coding Plan"
                    or settings.get("base_url", "").rstrip("/") != "https://api.z.ai/api/coding/paas/v4"
                    or not all(isinstance(settings.get(key), str) and settings[key].strip() for key in ("api_key", "model_name"))):
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise ValueError("A configured Z.ai Coding Plan connection is required") from None
        existing = db.exec(SqlBuilder.select.table(InternalBot).where(
            InternalBot.bot_type == InternalBotType.CommentCompaction,
            InternalBot.deleted_at.is_(None),
        )).all()
        compactor = next((row for row in existing if row.display_name == "컴팩터"), None)
        if compactor:
            return {"created": False, "uid": compactor.get_uid(), "display_name": compactor.display_name}
        if any(row.is_default for row in existing):
            raise ValueError("An existing compaction default must be reviewed before provisioning")
        compactor = InternalBot(
            display_name="컴팩터", bot_type=InternalBotType.CommentCompaction,
            platform=BotPlatform.Default, platform_running_type=BotPlatformRunningType.Default,
            is_default=True, value=dumps({key: settings[key] for key in ("agent_llm", "base_url", "api_key", "model_name")}),
        )
        db.insert(compactor)
        db.after_commit(lambda: InternalBotPublisher.created(compactor))
        return {"created": True, "uid": compactor.get_uid(), "display_name": compactor.display_name}


class CreateCommentCompactorCommand(BaseCommand):
    @staticmethod
    def is_only_in_dev() -> bool:
        return False

    @property
    def option_class(self):
        return CreateCommentCompactorCommandOptions

    @property
    def command(self) -> str:
        return "internal-bot:compactor:create"

    @property
    def positional_name(self) -> str:
        return ""

    @property
    def description(self) -> str:
        return "Create the independent compactor using an explicitly selected Z.ai Coding Plan connection"

    @property
    def choices(self):
        return None

    @property
    def store_type(self):
        return bool

    def execute(self, options: CreateCommentCompactorCommandOptions) -> None:
        print(dumps(create_comment_compactor(options.provider_uid), ensure_ascii=False))
