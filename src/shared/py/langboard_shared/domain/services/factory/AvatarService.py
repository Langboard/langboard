"""Explicit avatar preview, revision-controlled application and native asset retention."""

from asyncio import timeout
from base64 import b64decode
from io import BytesIO
from threading import BoundedSemaphore
from uuid import uuid4
from anyio import to_thread
from ....ai.image_generation import (
    MAX_IMAGE_BYTES,
    binding_signature,
    generate_preview,
    safe_preview,
    validate_image_config,
)
from ....core.db import DbSession, SqlBuilder
from ....core.storage import Storage, StorageName
from ....Env import Env
from ....publishers import ProjectPublisher, UserPublisher
from ....security import Auth, RoleFinder, RoleSecurity
from ...models import InternalBot, SettingRole, User
from ...models.BaseBotModel import BotPlatform, BotPlatformRunningType
from ...models.InternalBot import InternalBotType
from ...models.ProjectRole import ProjectRoleAction
from ...models.SettingRole import SettingRoleAction
from .InternalBotService import InternalBotService
from .WorkflowStageService import WorkflowStageService


_UPLOAD_CAPACITY = BoundedSemaphore(2)


class AvatarError(Exception):
    def __init__(self, code: str, status: int):
        self.code, self.status = code, status


class AvatarService:
    def __init__(self, service):
        self.service = service
        self.authority = WorkflowStageService(None, None, None)

    def _target(self, actor: User, project_uid: str | None = None, *, lock=False, bot_draft=False):
        if not isinstance(actor, User):
            raise AvatarError("avatar_forbidden", 403)
        with DbSession.use(readonly=False) as db:
            query = SqlBuilder.select.table(User).where(User.id == actor.id).execution_options(populate_existing=True)
            current = db.exec(query.with_for_update() if lock else query).first()
            if current is None or not current.activated_at:
                raise AvatarError("avatar_forbidden", 403)
            if bot_draft and current.email not in Env.FULL_ADMIN_ACCESS_EMAILS:
                if not current.is_admin or not RoleSecurity(SettingRole).is_authorized(
                    current.id, {}, [SettingRoleAction.BotCreate], RoleFinder.setting
                ):
                    raise AvatarError("avatar_forbidden", 403)
            if project_uid is None:
                return current
            for action in (ProjectRoleAction.Read, ProjectRoleAction.Update):
                board = self.authority._authorized_app_board(current, project_uid, action, lock=lock, revocation=True)
                if board is None:
                    raise AvatarError("avatar_forbidden", 403)
            return board

    @staticmethod
    def state(target) -> dict:
        return {
            "avatar": target.avatar.path if target.avatar else None,
            "previous_avatar": target.previous_avatar.path if target.previous_avatar else None,
            "avatar_revision": target.avatar_revision,
            "can_restore": target.avatar_revision > 0,
        }

    def get(self, actor, project_uid=None):
        return self.state(self._target(actor, project_uid))

    def profiles(self, actor, project_uid=None, *, bot_draft=False):
        self._target(actor, project_uid, bot_draft=bot_draft)
        with DbSession.use(readonly=False) as db:
            profiles = db.exec(
                SqlBuilder.select.table(InternalBot).where(
                    InternalBot.bot_type == InternalBotType.ImageGeneration,
                    InternalBot.platform == BotPlatform.Default,
                    InternalBot.platform_running_type == BotPlatformRunningType.Default,
                )
            ).all()
        result = []
        for profile in profiles:
            try:
                config = validate_image_config(profile.value)
            except (ValueError, TypeError):
                continue
            if config.enabled:
                result.append(
                    {"uid": profile.get_uid(), "display_name": profile.display_name, "model_name": config.model_name}
                )
        return {"profiles": result, "configured": bool(result)}

    def _binding(self, uid):
        binding = InternalBotService(None, None, None).get_current_by_id_like(uid)
        if (
            binding is None
            or binding.bot_type != InternalBotType.ImageGeneration
            or binding.platform != BotPlatform.Default
            or binding.platform_running_type != BotPlatformRunningType.Default
            or not validate_image_config(binding.value).enabled
        ):
            raise AvatarError("avatar_profile_unavailable", 409)
        return binding

    async def preview(self, actor, profile_uid, prompt, confirmed, project_uid=None, *, bot_draft=False):
        def authorize():
            self._target(actor, project_uid, bot_draft=bot_draft)

        await to_thread.run_sync(authorize)
        if confirmed is not True or not prompt.strip() or len(prompt) > 2000:
            raise AvatarError("avatar_confirmation_required", 400)

        def prepare():
            binding = self._binding(profile_uid)
            return binding, binding_signature(binding)

        binding, signature = await to_thread.run_sync(prepare)
        async with timeout(60):
            preview = await generate_preview(binding, prompt.strip())

        def verify():
            authorize()
            if signature != binding_signature(self._binding(profile_uid)):
                raise AvatarError("avatar_profile_changed", 409)

        await to_thread.run_sync(verify)
        return {"preview_data_url": preview, "width": 512, "height": 512}

    def context(self, actor, project_uid):
        from .AvatarSummaryService import AvatarSummaryService

        return AvatarSummaryService(self.service).context(actor, project_uid)

    @staticmethod
    def _publish(target, state):
        if isinstance(target, User):
            Auth.reset_user(target)
            UserPublisher.updated(target, {"avatar": state["avatar"], "avatar_revision": state["avatar_revision"]})
        else:
            ProjectPublisher.updated(target, {"avatar": state["avatar"], "avatar_revision": state["avatar_revision"]})

    def mutate(self, actor, expected_revision, project_uid=None, *, upload=None, restore=False):
        if not _UPLOAD_CAPACITY.acquire(blocking=False):
            raise AvatarError("avatar_processing_busy", 429)
        try:
            return self._mutate(actor, expected_revision, project_uid, upload=upload, restore=restore)
        finally:
            _UPLOAD_CAPACITY.release()

    def _mutate(self, actor, expected_revision, project_uid=None, *, upload=None, restore=False):
        if DbSession.has_active_transaction():
            raise RuntimeError("Avatar storage must own its transaction")
        self._target(actor, project_uid)
        if type(expected_revision) is not int or expected_revision < 0:
            raise AvatarError("avatar_invalid_revision", 400)
        new_file = None
        try:
            # Normalize before storage. The multipart handler runs in FastAPI's
            # native thread pool, keeping raster CPU work off the event loop.
            if not restore:
                if upload is None:
                    raise AvatarError("avatar_image_required", 400)
                raw = upload.file.read(MAX_IMAGE_BYTES + 1)
                try:
                    png = b64decode(safe_preview(raw).split(",", 1)[1], validate=True)
                except (OSError, ValueError):
                    raise AvatarError("avatar_invalid_image", 400) from None
                new_file = Storage.upload_named(
                    BytesIO(png), StorageName.Avatar, f"avatar-{uuid4().hex}.png", "avatar.png"
                )
                if new_file is None:
                    raise AvatarError("avatar_storage_unavailable", 503)
            with DbSession.atomic() as db:
                target = self._target(actor, project_uid, lock=True)
                if target.avatar_revision != expected_revision:
                    raise AvatarError("avatar_revision_conflict", 409)
                if restore and target.avatar_revision == 0:
                    raise AvatarError("avatar_no_previous", 409)
                current = target.avatar
                target.avatar = target.previous_avatar if restore else new_file
                target.previous_avatar = current
                target.avatar_revision += 1
                db.update(target)
                state = self.state(target)
                db.after_commit(lambda: self._publish(target, state))
            return state
        except Exception:
            if new_file:
                # Only this request's unique newly uploaded asset can be removed.
                Storage.delete(new_file)
            raise
