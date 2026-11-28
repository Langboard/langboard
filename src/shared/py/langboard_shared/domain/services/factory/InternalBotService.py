from json import loads
from re import fullmatch
from typing import Any, Literal
from ....core.db import DbSession, SqlBuilder
from ....core.domain import BaseDomainService
from ....core.domain.BaseDomainService import TMutableValidatorMap
from ....core.security import KeyVault
from ....core.storage import FileModel
from ....core.types.ParamTypes import TInternalBotParam
from ....helpers import InfraHelper
from ....publishers import InternalBotPublisher, ProjectPublisher
from ...models import InternalBot
from ...models.BaseBotModel import BotPlatform, BotPlatformRunningType
from ...models.InternalBot import InternalBotType
from .GraphApprovalRequestService import GraphApprovalRequestService


class InternalBotService(BaseDomainService):
    @staticmethod
    def name() -> str:
        """DO NOT EDIT THIS METHOD"""
        return "internal_bot"

    def get_by_id_like(self, internal_bot: TInternalBotParam | None) -> InternalBot | None:
        internal_bot = InfraHelper.get_by_id_like(InternalBot, internal_bot)
        return internal_bot

    def get_current_by_id_like(self, internal_bot: TInternalBotParam | None) -> InternalBot | None:
        """Read the primary before using or returning provider-backed content."""
        if not internal_bot:
            return None
        with DbSession.use(readonly=False) as db:
            return db.exec(
                SqlBuilder.select.table(InternalBot).where(InternalBot.id == InfraHelper.convert_id(internal_bot))
            ).first()

    def get_document_vision_binding(self) -> InternalBot | None:
        """Resolve the global document-vision alias independently of project bots."""
        return self.repo.internal_bot.get_default_by_type(InternalBotType.DocumentVision)

    def get_document_embedding_binding(self) -> InternalBot | None:
        """Global embedding configuration is independent of executable project bots."""
        return self.repo.internal_bot.get_default_by_type(InternalBotType.DocumentEmbedding)

    def get_comment_compaction_binding(self) -> InternalBot | None:
        """Use the current dedicated default; never fall back to a chat bot or a replica."""
        with DbSession.use(readonly=False) as db:
            binding = db.exec(SqlBuilder.select.table(InternalBot).where(
                InternalBot.bot_type == InternalBotType.CommentCompaction, InternalBot.is_default == True,  # noqa: E712
                InternalBot.deleted_at.is_(None),
            ).limit(1).execution_options(populate_existing=True)).first()
        if binding and binding.platform == BotPlatform.Default and binding.platform_running_type == BotPlatformRunningType.Default and not binding.api_url:
            return binding
        return None

    @staticmethod
    def _require_compaction_platform(platform, running_type, api_url):
        if platform != BotPlatform.Default or running_type != BotPlatformRunningType.Default or api_url:
            raise ValueError("This internal role requires the native provider binding")

    def is_document_embedding_enabled(self) -> bool:
        binding = self.get_document_embedding_binding()
        if not binding:
            return False
        from ....tasks.docling.DocumentEmbedding import validate_embedding_config

        try:
            _, settings = validate_embedding_config(binding.value)
            return settings.enabled
        except (ValueError, TypeError):
            return False

    def is_document_processing_enabled(self) -> bool:
        binding = self.get_document_vision_binding()
        if not binding:
            return False
        try:
            return loads(binding.value).get("document_processing_enabled", True) is True
        except (ValueError, AttributeError):
            return False

    def get_api_list(self, is_setting: bool) -> list[dict[str, Any]]:
        internal_bots = InfraHelper.get_all(InternalBot)
        return [internal_bot.api_response(is_setting=is_setting) for internal_bot in internal_bots]

    @staticmethod
    def _require_image_transaction_owner():
        if DbSession.has_active_transaction():
            raise RuntimeError("Image credential storage must own its transaction")

    @staticmethod
    def _delete_owned_image_key(provider, locator: str):
        if not fullmatch(r"image-generation-[0-9a-f]{32}", locator):
            return
        with DbSession.use(readonly=False) as db:
            shared = db.exec(SqlBuilder.select.table(InternalBot).where(InternalBot.api_key == locator)).first()
        if not shared:
            provider.delete_key(locator)

    def _persist_image(self, internal_bot: InternalBot, operation: str, previous_locator: str = ""):
        """Compensate new Vault writes and retire old material only after commit."""
        provider = KeyVault.provider
        new_locator = internal_bot.api_key if operation != "delete" and internal_bot.api_key != previous_locator else ""
        try:
            with DbSession.atomic() as db:
                getattr(self.repo.internal_bot, operation)(internal_bot)
                if previous_locator and (operation == "delete" or previous_locator != internal_bot.api_key):
                    db.after_commit(lambda: self._delete_owned_image_key(provider, previous_locator))
                publish = {
                    "insert": InternalBotPublisher.created,
                    "update": InternalBotPublisher.updated,
                    "delete": InternalBotPublisher.deleted,
                }[operation]
                db.after_commit(lambda: publish(internal_bot))
        except Exception:
            if new_locator:
                provider.delete_key(new_locator)
            raise

    def create(
        self,
        bot_type: InternalBotType,
        display_name: str,
        platform: BotPlatform,
        platform_running_type: BotPlatformRunningType,
        api_url: str = "",
        value: str = "",
        api_key: str = "",
        avatar: FileModel | None = None,
        is_default: bool = False,
    ) -> InternalBot:
        if bot_type in (InternalBotType.CommentCompaction, InternalBotType.AvatarSummary):
            self._require_compaction_platform(platform, platform_running_type, api_url)
        if bot_type == InternalBotType.AvatarSummary:
            from ....ai.avatar_summary import model_settings

            model_settings(value)
        if bot_type == InternalBotType.ImageGeneration:
            self._require_image_transaction_owner()
            from ....ai.image_generation import prepare_image_binding

            if platform != BotPlatform.Default or platform_running_type != BotPlatformRunningType.Default or api_url:
                raise ValueError("Image generation requires the native provider binding")
            value, api_key = prepare_image_binding(value, api_key)
        internal_bot = InternalBot(
            bot_type=bot_type,
            display_name=display_name,
            platform=platform,
            platform_running_type=platform_running_type,
            api_url=api_url,
            api_key=api_key,
            value=value,
            is_default=is_default,
            avatar=avatar,
        )

        if bot_type == InternalBotType.ImageGeneration:
            self._persist_image(internal_bot, "insert")
            return internal_bot
        self.repo.internal_bot.insert(internal_bot)

        InternalBotPublisher.created(internal_bot)

        return internal_bot

    def copy(self, internal_bot: TInternalBotParam | None) -> InternalBot | None:
        source_internal_bot = InfraHelper.get_by_id_like(InternalBot, internal_bot)
        if not source_internal_bot:
            return None

        copied_key = source_internal_bot.api_key
        if source_internal_bot.bot_type == InternalBotType.ImageGeneration:
            self._require_image_transaction_owner()
        if source_internal_bot.bot_type == InternalBotType.ImageGeneration and copied_key:
            from uuid import uuid4

            copied_key = KeyVault.store_secret(f"image-generation-{uuid4().hex}", KeyVault.get_key(copied_key))
        copied_internal_bot = InternalBot(
            bot_type=source_internal_bot.bot_type,
            display_name=f"{source_internal_bot.display_name} Copy",
            platform=source_internal_bot.platform,
            platform_running_type=source_internal_bot.platform_running_type,
            api_url=source_internal_bot.api_url,
            api_key=copied_key,
            value=source_internal_bot.value,
            is_default=False,
            avatar=source_internal_bot.avatar,
        )

        if source_internal_bot.bot_type == InternalBotType.ImageGeneration:
            self._persist_image(copied_internal_bot, "insert")
            return copied_internal_bot
        self.repo.internal_bot.insert(copied_internal_bot)

        InternalBotPublisher.created(copied_internal_bot)

        return copied_internal_bot

    def update(self, internal_bot: TInternalBotParam | None, form: dict) -> InternalBot | Literal[True] | None:
        internal_bot = InfraHelper.get_by_id_like(InternalBot, internal_bot)
        if not internal_bot:
            return None

        if internal_bot.bot_type == InternalBotType.AvatarSummary:
            from ....ai.avatar_summary import model_settings

            model_settings(form.get("value") if form.get("value") is not None else internal_bot.value)
        previous_locator = internal_bot.api_key
        if internal_bot.bot_type in (InternalBotType.CommentCompaction, InternalBotType.AvatarSummary):
            self._require_compaction_platform(
                form.get("platform") or internal_bot.platform,
                form.get("platform_running_type") or internal_bot.platform_running_type,
                form.get("api_url") if form.get("api_url") is not None else internal_bot.api_url,
            )
        if internal_bot.bot_type == InternalBotType.ImageGeneration:
            self._require_image_transaction_owner()
            from ....ai.image_generation import prepare_image_binding

            form = {key: value for key, value in form.items() if value is not None}
            if (
                form.get("platform", internal_bot.platform) != BotPlatform.Default
                or form.get("platform_running_type", internal_bot.platform_running_type)
                != BotPlatformRunningType.Default
                or form.get("api_url", internal_bot.api_url)
            ):
                raise ValueError("Image generation requires the native provider binding")
            form["value"], form["api_key"] = prepare_image_binding(
                form.get("value", internal_bot.value), form.get("api_key"), internal_bot
            )

        validators: TMutableValidatorMap = {
            "display_name": "default",
            "platform": "default",
            "platform_running_type": "default",
            "api_url": "default",
            "api_key": "default",
            "value": "default",
            "avatar": "default",
        }

        if "platform" in form and form["platform"] != internal_bot.platform:
            if form["platform"] not in InternalBot.ALLOWED_ALL_IPS_BY_PLATFORMS:
                form.pop("platform", None)
                form.pop("platform_running_type", None)
            else:
                available_running_types = InternalBot.AVAILABLE_RUNNING_TYPES_BY_PLATFORM[form["platform"]]
                platform_running_type = form.get("platform_running_type", available_running_types[0])
                if platform_running_type not in available_running_types:
                    form["platform_running_type"] = available_running_types[0]

        if "platform_running_type" in form:
            platform = form.get("platform", internal_bot.platform)
            if platform not in InternalBot.AVAILABLE_RUNNING_TYPES_BY_PLATFORM:
                form.pop("platform_running_type", None)
            else:
                available_running_types = InternalBot.AVAILABLE_RUNNING_TYPES_BY_PLATFORM[platform]
                if form["platform_running_type"] not in available_running_types:
                    form.pop("platform_running_type", None)

        previous_values = {key: getattr(internal_bot, key) for key in validators}
        old_record = self.apply_mutates(internal_bot, form, validators)
        if not old_record:
            return True

        if internal_bot.bot_type == InternalBotType.ImageGeneration:
            try:
                self._persist_image(internal_bot, "update", previous_locator)
            except Exception:
                for key, value in previous_values.items():
                    setattr(internal_bot, key, value)
                raise
            return internal_bot
        self.repo.internal_bot.update(internal_bot)

        InternalBotPublisher.updated(internal_bot)

        return internal_bot

    def change_default(self, internal_bot: TInternalBotParam | None) -> InternalBot | Literal[True] | None:
        internal_bot = InfraHelper.get_by_id_like(InternalBot, internal_bot)
        if not internal_bot:
            return None

        if internal_bot.is_default:
            return True

        self.repo.internal_bot.replace_default(internal_bot, internal_bot.bot_type)

        InternalBotPublisher.default_changed(internal_bot)

        return internal_bot

    def delete(self, internal_bot: TInternalBotParam | None) -> bool:
        internal_bot = InfraHelper.get_by_id_like(InternalBot, internal_bot)
        if not internal_bot or internal_bot.is_default:
            return False

        if internal_bot.bot_type == InternalBotType.ImageGeneration:
            self._require_image_transaction_owner()
            self._persist_image(internal_bot, "delete", internal_bot.api_key)
            return True

        if internal_bot.bot_type == InternalBotType.AvatarSummary:
            self.repo.internal_bot.delete(internal_bot)
            InternalBotPublisher.deleted(internal_bot)
            return True

        default_internal_bot = self.repo.internal_bot.get_default_by_type(internal_bot.bot_type)
        if not default_internal_bot:
            display_name = internal_bot.bot_type.value.replace("_", " ").title()
            new_default_internal_bot = self.create(
                bot_type=internal_bot.bot_type,
                display_name=display_name,
                platform=BotPlatform.Default,
                platform_running_type=BotPlatformRunningType.Default,
                is_default=True,
            )
            default_internal_bot = new_default_internal_bot

        projects = self.repo.project_assigned_internal_bot.get_all_projects_by_internal_bot(internal_bot)

        self._get_service(GraphApprovalRequestService).cancel_pending_by_internal_bot(
            internal_bot,
            reason="internal bot deleted",
        )
        self.repo.project_assigned_internal_bot.reassign_and_delete(internal_bot, default_internal_bot)

        for project in projects:
            ProjectPublisher.internal_bot_changed(project, default_internal_bot.id)
        InternalBotPublisher.deleted(internal_bot)

        return True
