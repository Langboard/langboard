"""Native avatar authority, asset persistence and explicit external transmission."""

import asyncio
import importlib
import json
from io import BytesIO
import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langboard.middlewares import ApiAuthMiddleware, RoleMiddleware
from langboard.routes.account import AvatarApi as account
from langboard.routes.account.AccountApi import update_profile
from langboard.routes.account.AccountForm import UpdateProfileForm
from langboard.routes.board import BoardAvatarApi as boards
from langboard.routes.settings import BotSettingsApi as bots
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.routing import AppRouter
from langboard_shared.core.security import KeyVault
from langboard_shared.core.security.vault.LocalDevVaultProvider import LocalDevVaultProvider
from langboard_shared.core.storage import Storage
from langboard_shared.core.storage.LocalStorage import LocalStorage
from langboard_shared.domain.models import (
    Card,
    CardAssignedProjectLabel,
    GlobalLabel,
    InternalBot,
    ProjectLabel,
    SettingRole,
    UserProfile,
)
from langboard_shared.domain.models.BaseBotModel import BotPlatform, BotPlatformRunningType
from langboard_shared.domain.models.InternalBot import InternalBotType
from langboard_shared.domain.models.SettingRole import SettingRoleAction
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.factory.AvatarService import AvatarError, AvatarService
from langboard_shared.Env import Env
from langboard_shared.helpers import MiddlewareHelper
from langboard_shared.publishers import ProjectPublisher, UserPublisher
from langboard_shared.security import Auth, RoleSecurity
from PIL import Image, PngImagePlugin
from sqlalchemy import create_engine, event, inspect, text
from starlette.datastructures import UploadFile


avatar_module = importlib.import_module("langboard_shared.domain.services.factory.AvatarService")
CONFIG = {"agent_llm": "OpenAI", "base_url": "https://images.example/v1", "model_name": "gpt-image-1", "enabled": True}


def raster(color="blue"):
    output = BytesIO()
    info = PngImagePlugin.PngInfo()
    info.add_text("private", "metadata-secret")
    Image.new("RGB", (96, 128), color).save(output, "PNG", pnginfo=info)
    return UploadFile(BytesIO(output.getvalue()), filename="user-input.png")


@pytest.fixture
def avatars(board, monkeypatch, tmp_path):
    engine = DbEngine.get_main_engine()
    for model in (InternalBot, Card, UserProfile, GlobalLabel, ProjectLabel, CardAssignedProjectLabel):
        model.__table__.create(engine)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    monkeypatch.setattr(type(Env), "LOCAL_STORAGE_DIR", property(lambda _: tmp_path / "files"))
    monkeypatch.setattr(type(Env), "CARD_INTERNAL_ACCESS_MODE", property(lambda _: "project_members"))
    monkeypatch.setattr(Storage, "_storages", {"local": LocalStorage()})
    monkeypatch.setattr(KeyVault, "provider", LocalDevVaultProvider(tmp_path / "vault"))
    published = []
    monkeypatch.setattr(UserPublisher, "updated", lambda *args: published.append(args))
    monkeypatch.setattr(ProjectPublisher, "updated", lambda *args: published.append(args))
    monkeypatch.setattr(Auth, "reset_user", lambda user: None)
    service = DomainService()
    avatar = AvatarService(service)
    actor = board[1]
    project = board[2]
    with DbSession.use(readonly=False) as db:
        board[4].actions = ["read", "update"]
        db.update(board[4])
        for enabled in (True, False):
            profile = InternalBot(
                bot_type=InternalBotType.ImageGeneration,
                display_name=f"Images {enabled}",
                platform=BotPlatform.Default,
                platform_running_type=BotPlatformRunningType.Default,
                value=json.dumps({**CONFIG, "enabled": enabled}),
                api_key="private-vault-locator",
            )
            db.insert(profile)
            if enabled:
                binding = profile
        db.insert(UserProfile(user_id=actor.id, industry="Software", purpose="Work"))
    KeyVault.store_secret("private-vault-locator", "never-expose-material")
    return avatar, service, actor, project, board[4], binding, tmp_path, published


@pytest.mark.parametrize("scope", ["personal", "board"])
def test_apply_persist_refresh_restore_original_none_and_revision(avatars, scope):
    avatar, _, actor, project, _, _, folder, published = avatars
    uid = project.get_uid() if scope == "board" else None
    assert avatar.get(actor, uid) == {
        "avatar": None,
        "previous_avatar": None,
        "avatar_revision": 0,
        "can_restore": False,
    }
    first = avatar.mutate(actor, 0, uid, upload=raster())
    assert first["avatar"] and first["can_restore"] and first["avatar_revision"] == 1
    assert avatar.get(actor, uid) == first
    files = list((folder / "files" / "avatar").iterdir())
    assert len(files) == 1
    with Image.open(files[0]) as image:
        assert image.size == (512, 512) and image.format == "PNG" and image.info == {}
    restored = avatar.mutate(actor, 1, uid, restore=True)
    assert restored["avatar"] is None and restored["previous_avatar"] == first["avatar"]
    assert restored["avatar_revision"] == 2 and restored["can_restore"]
    reapplied = avatar.mutate(actor, 2, uid, restore=True)
    assert reapplied["avatar"] == first["avatar"]
    assert files[0].exists() and len(published) == 3
    if uid:
        assert avatar._target(actor, uid).api_response()["avatar"] == first["avatar"]


@pytest.mark.parametrize("scope", ["personal", "board"])
def test_stale_apply_restore_and_db_failure_cleanup_owned_only(avatars, monkeypatch, scope):
    avatar, _, actor, project, _, _, folder, published = avatars
    uid = project.get_uid() if scope == "board" else None
    first = avatar.mutate(actor, 0, uid, upload=raster())
    before = set((folder / "files" / "avatar").iterdir())
    for restore in (False, True):
        with pytest.raises(AvatarError) as error:
            avatar.mutate(actor, 0, uid, upload=raster("red"), restore=restore)
        assert error.value.status == 409
    assert set((folder / "files" / "avatar").iterdir()) == before
    engine = DbEngine.get_main_engine()

    def failure(connection):
        if len(list((folder / "files" / "avatar").iterdir())) > len(before):
            raise RuntimeError("commit failed")

    event.listen(engine, "commit", failure)
    try:
        with pytest.raises(RuntimeError, match="commit failed"):
            avatar.mutate(actor, 1, uid, upload=raster("red"))
    finally:
        event.remove(engine, "commit", failure)
    assert set((folder / "files" / "avatar").iterdir()) == before
    assert avatar.get(actor, uid) == first and len(published) == 1


def test_current_authority_and_invalid_raster_never_change_avatar(avatars):
    avatar, _, actor, project, role, _, folder, _ = avatars
    with DbSession.use(readonly=False) as db:
        role.actions = ["read"]
        db.update(role)
    with pytest.raises(AvatarError) as error:
        avatar.mutate(actor, 0, project.get_uid(), upload=raster())
    assert error.value.status == 403
    assert not (folder / "files").exists()
    for raw in (b"<svg>bad</svg>", b"x" * (8 * 1024 * 1024 + 1)):
        with pytest.raises((ValueError, AvatarError)):
            avatar.mutate(actor, 0, upload=UploadFile(BytesIO(raw), filename="bad.png"))
    assert avatar.get(actor)["avatar_revision"] == 0
    with DbSession.use(readonly=False) as db:
        current = avatar._target(actor)
        current.activated_at = None
        db.update(current)
    with pytest.raises(AvatarError):
        avatar.get(actor)


def test_profiles_and_prepared_context_never_transmit_private_material(avatars, monkeypatch):
    avatar, _, actor, project, _, _, _, _ = avatars
    calls = []
    monkeypatch.setattr(avatar_module, "generate_preview", lambda *args: calls.append(args))
    profiles = avatar.profiles(actor, project.get_uid())
    assert profiles["configured"] and len(profiles["profiles"]) == 1
    assert set(profiles["profiles"][0]) == {"uid", "display_name", "model_name"}
    with DbSession.use(readonly=False) as db:
        for index, visibility in enumerate(("SHARED", "INTERNAL", "PRIVATE")):
            db.insert(
                Card(
                    project_id=project.id,
                    project_column_id=40,
                    title=f"secret-title-{index}",
                    visibility=visibility,
                    owner_user_id=actor.id if visibility == "PRIVATE" else None,
                    created_by_user_id=actor.id,
                )
            )
        db.insert(
            Card(
                project_id=project.id,
                project_column_id=40,
                title="Linked private reference",
                visibility="SHARED",
                created_by_user_id=actor.id,
                source_type="project_wiki",
                source_uid="opaque",
            )
        )
        project.title = "Private client identity"
        db.update(project)
    prepared = avatar.context(actor, project.get_uid())
    assert prepared["external_request"] is False
    assert prepared["source_revision"] and prepared["sources"]
    assert "secret-title-0" in json.dumps(prepared)
    serialized = json.dumps([profiles, prepared])
    for sensitive in ("secret-title-1", "secret-title-2", "never-expose", "private-vault", "images.example", "PRIVATE"):
        assert sensitive not in serialized
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["permission", "deactivation", "profile"])
async def test_explicit_prompt_only_preview_and_post_await_revocation(avatars, monkeypatch, change):
    avatar, _, actor, project, role, binding, _, _ = avatars
    calls = []

    async def generated(profile, prompt):
        calls.append(prompt)
        with DbSession.use(readonly=False) as db:
            if change == "permission":
                role.actions = ["read"]
                db.update(role)
            elif change == "deactivation":
                current = avatar._target(actor)
                current.activated_at = None
                db.update(current)
            else:
                binding.value = json.dumps({**CONFIG, "enabled": False})
                db.update(binding)
        return "data:image/png;base64,fixture"

    monkeypatch.setattr(avatar_module, "generate_preview", generated)
    with pytest.raises(AvatarError) as denied:
        await avatar.preview(actor, binding.get_uid(), "explicit public prompt", False, project.get_uid())
    assert denied.value.status == 400 and calls == []
    with pytest.raises(AvatarError):
        await avatar.preview(actor, binding.get_uid(), "explicit public prompt", True, project.get_uid())
    assert calls == ["explicit public prompt"]


def test_ordinary_user_update_records_history_and_keeps_profile_semantics(avatars):
    avatar, service, actor, _, _, _, _, _ = avatars
    first = avatar.mutate(actor, 0, upload=raster())
    current = avatar._target(actor)
    assert service.user.update(current, {"firstname": "New", "affiliation": "Company", "delete_avatar": True})
    state = avatar.get(actor)
    assert state["avatar"] is None and state["previous_avatar"] == first["avatar"] and state["avatar_revision"] == 2
    refreshed = avatar._target(actor)
    assert refreshed.firstname == "New" and current.firstname == "New" and current.avatar_revision == 2
    assert service.user.repo.user_profile.get_by_user(refreshed).affiliation == "Company"
    assert avatar.mutate(actor, 2, restore=True)["avatar"] == first["avatar"]


@pytest.fixture
def avatar_http(avatars, monkeypatch):
    avatar, service, actor, project, _, _, _, _ = avatars
    identity = {"actor": actor, "allowed": True}

    def validate(scope, **kwargs):
        scope["auth"] = identity["actor"]
        return identity["actor"]

    monkeypatch.setattr(MiddlewareHelper, "validate_auth", validate)
    middleware = importlib.import_module("langboard.middlewares.ApiAuthMiddleware")
    monkeypatch.setattr(middleware, "DomainService", lambda: service)
    monkeypatch.setattr(RoleSecurity, "is_authorized", lambda *args: identity["allowed"])
    app = FastAPI()
    app.include_router(AppRouter.api)
    handlers = {
        update_profile,
        account.get_avatar,
        account.apply_avatar,
        account.restore_avatar,
        account.avatar_profiles,
        account.preview_avatar,
        boards.get_board_avatar,
        boards.apply_board_avatar,
        boards.restore_board_avatar,
        boards.board_avatar_profiles,
        boards.board_avatar_context,
        boards.board_avatar_semantic_preview,
        boards.preview_board_avatar,
        bots.bot_avatar_profiles,
        bots.preview_bot_avatar,
        bots.create_bot,
        bots.get_bots_in_settings,
    }
    for route in AppRouter.api.routes:
        if getattr(route, "endpoint", None) in handlers:
            for dependency in route.dependant.dependencies:
                if "create_factory" in str(dependency.call):
                    app.dependency_overrides[dependency.call] = lambda: service
    app.add_middleware(RoleMiddleware, routes=AppRouter.api.routes)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    with TestClient(app) as client:
        yield client, identity, project, avatars


def test_actual_http_auth_roles_json_preview_multipart_apply_restore(avatar_http, monkeypatch):
    client, identity, project, avatars = avatar_http
    actor = identity["actor"]
    identity["actor"] = 401
    assert client.get("/account/avatar").status_code == 401
    identity["actor"] = actor
    path = f"/board/{project.get_uid()}/avatar"
    identity["allowed"] = False
    assert client.get(path).status_code == 403
    identity["allowed"] = True
    assert client.get(path).json()["avatar_revision"] == 0
    image = raster().file.getvalue()
    response = client.put(path, data={"expected_revision": "0"}, files={"avatar": ("avatar.png", image, "image/png")})
    assert response.status_code == 200, response.text
    assert response.json()["avatar"]
    assert client.post(path + "/restore", json={"expected_revision": 0}).status_code == 409
    restored = client.post(path + "/restore", json={"expected_revision": 1})
    assert restored.status_code == 200 and restored.json()["avatar"] is None
    calls = []

    async def generated(profile, prompt):
        calls.append(prompt)
        return "data:image/png;base64,preview"

    monkeypatch.setattr(avatar_module, "generate_preview", generated)
    body = {"profile_uid": avatars[5].get_uid(), "prompt": "Public purpose", "confirmed": False}
    assert client.post("/account/avatar/preview", json=body).status_code == 400
    assert calls == []
    body["confirmed"] = True
    assert client.post("/account/avatar/preview", json=body).status_code == 200
    assert calls == ["Public purpose"]
    assert client.get("/account/avatar/image-profiles").status_code == 200


def test_additive_avatar_migration_and_single_head():
    scripts = ScriptDirectory.from_config(Config("alembic.ini"))
    heads = scripts.get_heads()
    assert len(heads) == 1
    assert "a28196e32054" in {revision.revision for revision in scripts.walk_revisions(head=heads[0])}
    migration = scripts.get_revision("a28196e32054").module
    assert migration.down_revision == "f17085d21943"
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE "user" (id BIGINT PRIMARY KEY, avatar JSON)'))
        connection.execute(text("CREATE TABLE project (id BIGINT PRIMARY KEY, title TEXT)"))
        connection.execute(text('INSERT INTO "user" (id,avatar) VALUES (1,NULL)'))
        connection.execute(text("INSERT INTO project (id,title) VALUES (2,'Existing')"))
        original = migration.op
        migration.op = Operations(MigrationContext.configure(connection))
        try:
            migration.upgrade()
            assert connection.execute(text('SELECT avatar_revision FROM "user"')).scalar() == 0
            assert connection.execute(text("SELECT title,avatar_revision FROM project")).first() == ("Existing", 0)
            assert {row["name"] for row in inspect(connection).get_columns("project")} >= {
                "avatar",
                "previous_avatar",
                "avatar_revision",
            }
            migration.downgrade()
            assert connection.execute(text("SELECT title FROM project")).scalar() == "Existing"
        finally:
            migration.op = original
    engine.dispose()


@pytest.mark.parametrize("delete", [False, True])
def test_ordinary_profile_stale_avatar_revision_rejects_all_writes(avatars, delete):
    avatar, service, actor, _, _, _, folder, _ = avatars
    first = avatar.mutate(actor, 0, upload=raster())
    files_before = set((folder / "files" / "avatar").iterdir())
    response = update_profile(
        UpdateProfileForm(firstname="Stale name", lastname="Stale", delete_avatar=delete, expected_avatar_revision=0),
        None if delete else raster("red"),
        actor,
        service,
    )
    assert response.status_code == 409
    assert avatar.get(actor) == first
    assert avatar._target(actor).firstname == "Test"
    assert set((folder / "files" / "avatar").iterdir()) == files_before


def test_existing_ordinary_profile_http_keeps_history_and_rereads(avatar_http):
    client, _, _, avatars = avatar_http
    avatar, _, actor, _, _, _, _, _ = avatars
    first = avatar.mutate(actor, 0, upload=raster())
    response = client.put(
        "/account/profile",
        data={"firstname": "Updated", "lastname": "Reader", "delete_avatar": "true", "expected_avatar_revision": "1"},
    )
    assert response.status_code == 200, response.text
    state = client.get("/account/avatar").json()
    assert state["avatar"] is None and state["previous_avatar"] == first["avatar"] and state["avatar_revision"] == 2
    assert avatar._target(actor).firstname == "Updated"


def test_native_avatar_upload_capacity_rejects_without_storage(avatars):
    avatar, _, actor, _, _, _, folder, _ = avatars
    capacity = avatar_module._UPLOAD_CAPACITY
    assert capacity.acquire(blocking=False) and capacity.acquire(blocking=False)
    try:
        with pytest.raises(AvatarError) as error:
            avatar.mutate(actor, 0, upload=raster())
        assert error.value.status == 429 and not (folder / "files").exists()
    finally:
        capacity.release()
        capacity.release()
    assert avatar.mutate(actor, 0, upload=raster())["avatar_revision"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["personal", "admin_preview", "saved_models"])
async def test_native_authority_slow_vault_preparation_and_readback_keep_eventloop_responsive(
    avatars, monkeypatch, operation
):
    from base64 import b64encode
    from threading import Event, Lock
    import httpx
    from anyio import to_thread
    from langboard.routes.settings import ImageGenerationApi
    from langboard_shared.ai import image_generation

    avatar, service, actor, _, _, binding, _, _ = avatars
    original_key = KeyVault.get_key
    entered = [Event() for _ in range(3)]
    released = [Event() for _ in range(3)]
    counter = 0
    guard = Lock()

    def slow_key(locator):
        nonlocal counter
        with guard:
            index = counter
            counter += 1
        assert index < 3, "unexpected extra Vault read"
        entered[index].set()
        assert released[index].wait(5), "test Vault release timed out"
        return original_key(locator)

    monkeypatch.setattr(KeyVault, "get_key", slow_key)
    png = raster().file.getvalue()
    requests = []

    async def target(url):
        return image_generation.ResolvedWebhookTarget(
            url.replace("images.example", "93.184.216.34"), "images.example", "images.example"
        )

    def handler(request):
        requests.append(request)
        if str(request.url).endswith("models"):
            return httpx.Response(200, json={"data": [{"id": "gpt-image-1"}]})
        return httpx.Response(200, json={"data": [{"b64_json": b64encode(png).decode()}]})

    client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: client(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(image_generation, "AsyncClient", httpx.AsyncClient)
    monkeypatch.setattr(image_generation, "ensure_public_webhook_url", target)
    if operation == "personal":
        pending = avatar.preview(actor, binding.get_uid(), "Explicit public purpose", True)
    elif operation == "admin_preview":
        pending = ImageGenerationApi.preview_image(
            binding.get_uid(),
            ImageGenerationApi.ImagePreviewForm(prompt="Explicit public purpose", confirmed=True),
            service,
        )
    else:
        pending = ImageGenerationApi.get_image_models(binding.get_uid(), service)
    task = asyncio.create_task(pending)
    try:
        for index in range(3):
            assert await to_thread.run_sync(entered[index].wait, 2)
            # Preparation, provider credential lookup and post-provider signature
            # are all real Vault boundaries. None may occupy the event loop.
            assert await asyncio.wait_for(asyncio.sleep(0, result="responsive"), 0.2) == "responsive"
            assert len(requests) == (1 if index == 2 else 0)
            released[index].set()
        result = await task
    finally:
        for event in released:
            event.set()
        if not task.done():
            await task
    if operation == "personal":
        assert result["preview_data_url"].startswith("data:image/png;base64,")
    else:
        assert result.status_code == 200
    assert counter == 3 and len(requests) == 1


@pytest.fixture
def bot_creator(avatars, monkeypatch):
    avatar, service, actor, project, _, binding, folder, published = avatars
    SettingRole.__table__.create(DbEngine.get_main_engine(), checkfirst=True)
    monkeypatch.setattr(type(Env), "FULL_ADMIN_ACCESS_EMAILS", property(lambda _: []))
    with DbSession.atomic() as db:
        actor.is_admin = True
        db.update(actor)
        role = SettingRole(user_id=actor.id, actions=[SettingRoleAction.BotCreate])
        db.insert(role)
    return avatar, service, actor, project, binding, role, folder, published


@pytest.mark.asyncio
async def test_bot_draft_avatar_uses_image_profile_without_persisting_any_target(bot_creator, monkeypatch):
    avatar, _, actor, project, binding, role, folder, published = bot_creator
    calls = []

    async def generate(profile, prompt):
        calls.append((profile.get_uid(), prompt))
        return "data:image/png;base64,fixture"

    monkeypatch.setattr(avatar_module, "generate_preview", generate)
    assert avatar.profiles(actor, bot_draft=True)["configured"]
    before = avatar.get(actor), avatar.get(actor, project.get_uid())
    with pytest.raises(AvatarError) as denied:
        await avatar.preview(actor, binding.get_uid(), "Tag bot", False, bot_draft=True)
    assert denied.value.status == 400 and not calls
    result = await avatar.preview(actor, binding.get_uid(), "Tag bot", True, bot_draft=True)
    assert result["preview_data_url"] == "data:image/png;base64,fixture"
    assert calls == [(binding.get_uid(), "Tag bot")]
    assert (avatar.get(actor), avatar.get(actor, project.get_uid())) == before
    assert not published and not (folder / "files").exists()
    with DbSession.atomic() as db:
        role.actions = []
        db.update(role)
    with pytest.raises(AvatarError) as denied:
        avatar.profiles(actor, bot_draft=True)
    assert denied.value.status == 403


@pytest.mark.parametrize("change", ["role", "admin"])
@pytest.mark.asyncio
async def test_bot_draft_preview_rechecks_creator_authority_after_model_await(bot_creator, monkeypatch, change):
    avatar, _, actor, _, binding, role, folder, published = bot_creator
    calls = []

    async def generate(profile, prompt):
        calls.append(prompt)
        with DbSession.atomic() as db:
            if change == "role":
                role.actions = []
                db.update(role)
            else:
                actor.is_admin = False
                db.update(actor)
        return "data:image/png;base64,fixture"

    monkeypatch.setattr(avatar_module, "generate_preview", generate)
    with pytest.raises(AvatarError) as denied:
        await avatar.preview(actor, binding.get_uid(), "Tag bot", True, bot_draft=True)
    assert denied.value.status == 403 and calls == ["Tag bot"]
    assert not published and not (folder / "files").exists()


def test_bot_draft_avatar_http_routes_enforce_creator_role(avatar_http, monkeypatch):
    client, identity, _, avatars = avatar_http
    avatar, _, actor, _, _, binding, _, published = avatars
    with DbSession.atomic() as db:
        actor.is_admin = True
        db.update(actor)
    generated = []

    async def generate(profile, prompt):
        generated.append(prompt)
        return "data:image/png;base64,fixture"

    monkeypatch.setattr(avatar_module, "generate_preview", generate)
    base = "/settings/bot/avatar"
    assert client.get(base + "/image-profiles").status_code == 200
    payload = {"profile_uid": binding.get_uid(), "prompt": "Tag bot", "confirmed": False}
    assert client.post(base + "/preview", json=payload).status_code == 400
    payload["confirmed"] = True
    assert client.post(base + "/preview", json=payload).status_code == 200
    assert generated == ["Tag bot"] and not published
    identity["allowed"] = False
    assert client.get(base + "/image-profiles").status_code == 403
    assert client.post(base + "/preview", json=payload).status_code == 403
    assert generated == ["Tag bot"]


def test_generated_bot_preview_is_explicitly_uploaded_and_persisted(avatar_http, monkeypatch):
    """Exercise preview bytes through native multipart, storage and a fresh bot read."""
    from base64 import b64decode, b64encode
    from langboard_shared.core.types.BotRelatedTypes import AVAILABLE_BOT_TARGET_TABLES
    from langboard_shared.domain.models import Bot, BotDefaultScopeBranch
    from langboard_shared.helpers import BotHelper

    client, _, _, avatars = avatar_http
    _, service, actor, _, _, binding, folder, _ = avatars
    for model in (Bot, BotDefaultScopeBranch):
        model.__table__.create(DbEngine.get_main_engine(), checkfirst=True)
    for target in AVAILABLE_BOT_TARGET_TABLES:
        model = BotHelper.get_default_scope_model_class(target)
        if model is not None:
            model.__table__.create(DbEngine.get_main_engine(), checkfirst=True)
    with DbSession.atomic() as db:
        actor.is_admin = True
        db.update(actor)
    bot_service_module = importlib.import_module("langboard_shared.domain.services.factory.BotService")
    events = []
    monkeypatch.setattr(bot_service_module.BotPublisher, "bot_created", lambda bot: events.append(bot.get_uid()))
    monkeypatch.setattr(bot_service_module.BotDefaultTask, "bot_created", lambda bot: None)
    data = raster().file.read()

    async def generate(profile, prompt):
        return "data:image/png;base64," + b64encode(data).decode()

    monkeypatch.setattr(avatar_module, "generate_preview", generate)
    preview = client.post("/settings/bot/avatar/preview", json={
        "profile_uid": binding.get_uid(), "prompt": "A public tag bot symbol", "confirmed": True,
    })
    assert preview.status_code == 200
    assert not events and not list((folder / "files").glob("**/*"))
    response = client.post("/settings/bot", data={
        "bot_name": "Tag bot", "bot_uname": "bot-avatar-persisted", "platform": "default",
        "platform_running_type": "default", "value": '{"model_name":"fixture"}',
    }, files={"avatar": ("generated.png", b64decode(preview.json()["preview_data_url"].split(",", 1)[1]), "image/png")})
    assert response.status_code == 201, response.text
    created = response.json()["bot"]
    assert created["avatar"] and events == [created["uid"]]
    with DbSession.use(readonly=False) as db:
        stored = db.exec(SqlBuilder.select.table(Bot).where(Bot.bot_uname == "bot-avatar-persisted")).first()
        assert stored is not None
        assert stored.avatar.path == created["avatar"]
        asset = stored.avatar
    files = list((folder / "files").glob("**/" + asset.filename))
    assert len(files) == 1 and files[0].read_bytes() == data
    listed = client.get("/settings/bots")
    assert listed.status_code == 200, listed.text
    reread = next(bot for bot in listed.json()["bots"] if bot["uid"] == created["uid"])
    assert reread["avatar"] == created["avatar"]
    assert service.bot.get_by_id_like(created["uid"]).avatar.path == created["avatar"]


@pytest.fixture
def semantic_avatar(avatars):
    from langboard_shared.domain.models import CardAssignedProjectLabel, ProjectLabel
    from langboard_shared.domain.services.factory.AvatarSummaryService import AvatarSummaryService

    avatar, service, actor, project, role, _, folder, published = avatars
    for model in (ProjectLabel, CardAssignedProjectLabel):
        model.__table__.create(DbEngine.get_main_engine(), checkfirst=True)
    with DbSession.atomic() as db:
        binding = InternalBot(bot_type=InternalBotType.AvatarSummary, display_name="Semantic",
            platform=BotPlatform.Default, platform_running_type=BotPlatformRunningType.Default,
            value=json.dumps({"agent_llm": "OpenAI", "model_name": "semantic-fixture", "api_key": "secret-fixture",
                              "api_names": ["delete_board"], "system_prompt": "unsafe custom prompt"}))
        db.insert(binding)
        card = Card(project_id=project.id, project_column_id=40, visibility="SHARED", title="Robot navigation https://private.test api_key=hidden")
        db.insert(card)
        for options in ({"visibility": "PRIVATE", "owner_user_id": actor.id}, {"visibility": "INTERNAL"},
                        {"archived_at": actor.created_at}, {"source_type": "project_wiki", "source_uid": "opaque"}):
            db.insert(Card(project_id=project.id, project_column_id=40, created_by_user_id=actor.id, title="excluded-source", **options))
        label = ProjectLabel(project_id=project.id, name="Robotics", color="#123456", description="Excluded body")
        db.insert(label)
        db.insert(CardAssignedProjectLabel(card_id=card.id, project_label_id=label.id))
    return AvatarSummaryService(service), actor, project, role, binding, card


def test_semantic_context_is_bounded_readable_sanitized_and_dedicated(semantic_avatar):
    summary, actor, project, _, binding, _ = semantic_avatar
    context = summary.context(actor, project.get_uid())
    assert context["external_request"] is False
    assert context["profiles"] == [{"uid": binding.get_uid(), "display_name": "Semantic", "model_name": "semantic-fixture"}]
    serialized = json.dumps(context)
    assert all(value not in serialized for value in ("excluded-source", "private.test", "hidden", "secret-fixture", "delete_board", "Excluded body"))
    assert any(source["text"] == "Robotics" for source in context["sources"])
    assert sum(len(source["text"]) for source in context["sources"]) <= 5000
    assert context == summary.context(actor, project.get_uid())
    assert not InternalBotType.AvatarSummary.is_executable


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["none", "source", "profile", "permission", "deactivation", "base"])
async def test_semantic_selected_sources_and_final_fences(semantic_avatar, monkeypatch, change):
    from langboard_shared.ai.avatar_summary import AvatarSummaryDraft
    module = importlib.import_module("langboard_shared.domain.services.factory.AvatarSummaryService")
    summary, actor, project, role, binding, card = semantic_avatar
    context = summary.context(actor, project.get_uid())
    selected = [source["id"] for source in context["sources"] if source["kind"] == "card_title"]
    calls = []

    async def generate(settings, snapshot, revision):
        calls.append(snapshot)
        assert set(settings) == {"agent_llm", "model_name", "api_key"}
        assert [source.id for source in snapshot.sources] == selected
        with DbSession.atomic() as db:
            if change == "source":
                card.title = "Changed title"
                db.update(card)
            elif change == "profile":
                binding.value = binding.value.replace("semantic-fixture", "changed-model")
                db.update(binding)
            elif change == "permission":
                role.actions = ["read"]
                db.update(role)
            elif change == "deactivation":
                actor.activated_at = None
                db.update(actor)
            elif change == "base":
                project.avatar_revision += 1
                db.update(project)
        return AvatarSummaryDraft(prompt="A navigation robot symbol", source_refs=selected)

    monkeypatch.setattr(module, "request_avatar_summary", generate)
    for ids, confirmed in ((selected, False), (selected * 2, True), (["foreign-source"], True), ([], True)):
        with pytest.raises(AvatarError):
            await summary.preview(actor, project.get_uid(), context["source_revision"], binding.get_uid(), ids, confirmed)
    assert calls == []
    if change == "none":
        result = await summary.preview(actor, project.get_uid(), context["source_revision"], binding.get_uid(), selected, True)
        assert result["external_request"] and result["prompt"] == "A navigation robot symbol"
    else:
        with pytest.raises(AvatarError) as rejected:
            await summary.preview(actor, project.get_uid(), context["source_revision"], binding.get_uid(), selected, True)
        assert rejected.value.status in (403, 409)
    assert len(calls) == 1


def test_semantic_http_context_and_consent(avatar_http, monkeypatch):
    from langboard_shared.ai.avatar_summary import AvatarSummaryDraft
    from langboard_shared.domain.models import CardAssignedProjectLabel, ProjectLabel
    module = importlib.import_module("langboard_shared.domain.services.factory.AvatarSummaryService")
    client, identity, _, avatars = avatar_http
    _, _, actor, project, _, _, _, _ = avatars
    for model in (ProjectLabel, CardAssignedProjectLabel):
        model.__table__.create(DbEngine.get_main_engine(), checkfirst=True)
    with DbSession.atomic() as db:
        binding = InternalBot(bot_type=InternalBotType.AvatarSummary, display_name="Summary",
                              platform=BotPlatform.Default, platform_running_type=BotPlatformRunningType.Default,
                              value='{"agent_llm":"OpenAI","model_name":"fixture"}')
        db.insert(binding)
    calls = []
    revoked = [False]
    async def generate(settings, snapshot, revision):
        calls.append(snapshot)
        if revoked[0]:
            identity["actor"] = 401
        return AvatarSummaryDraft(prompt="A project symbol", source_refs=[source.id for source in snapshot.sources])
    monkeypatch.setattr(module, "request_avatar_summary", generate)
    base = f"/board/{project.get_uid()}/avatar"
    context = client.post(base + "/context").json()
    payload = {"source_revision": context["source_revision"], "profile_uid": binding.get_uid(),
               "source_ids": [source["id"] for source in context["sources"]], "confirmed": False}
    assert client.post(base + "/semantic-preview", json=payload).status_code == 400 and not calls
    payload["confirmed"] = True
    assert client.post(base + "/semantic-preview", json={**payload, "text": "client source"}).status_code == 400
    response = client.post(base + "/semantic-preview", json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["external_request"] is True and len(calls) == 1
    revoked[0] = True
    assert client.post(base + "/semantic-preview", json=payload).status_code == 403
    assert len(calls) == 2
    identity["actor"] = actor
    identity["allowed"] = False
    assert client.post(base + "/semantic-preview", json=payload).status_code == 403
    assert len(calls) == 2


def test_semantic_context_caps_titles_and_labels_and_never_uses_bodies(semantic_avatar):
    summary, actor, project, _, binding, card = semantic_avatar
    with DbSession.atomic() as db:
        project.description = "d" * 2000
        project.ai_description = "hidden-ai-body"
        db.update(project)
        for index in range(30):
            db.insert(Card(project_id=project.id, project_column_id=40, visibility="SHARED", title="t" * 200 + str(index)))
        for index in range(15):
            label = ProjectLabel(project_id=project.id, name="l" * 80 + str(index), color="#123456", description="hidden-label-body")
            db.insert(label)
            db.insert(CardAssignedProjectLabel(card_id=card.id, project_label_id=label.id))
    context = summary.context(actor, project.get_uid())
    assert len([source for source in context["sources"] if source["kind"] == "card_title"]) == 24
    assert len([source for source in context["sources"] if source["kind"] == "label"]) <= 12
    assert sum(len(source["text"]) for source in context["sources"]) <= 5000
    for source in context["sources"]:
        assert len(source["text"]) <= {"project_description": 1000, "label": 60}.get(source["kind"], 120)
    assert "hidden-ai-body" not in json.dumps(context) and "hidden-label-body" not in json.dumps(context)


def test_semantic_native_profile_validation_and_generic_execution_rejection(semantic_avatar):
    from langboard_shared.ai.InternalBotGraphRequest import build_internal_bot_graph_request
    from langboard_shared.domain.services.factory.InternalBotService import InternalBotService
    summary, actor, project, _, binding, _ = semantic_avatar
    service = InternalBotService(None, None, None)
    with pytest.raises(ValueError):
        service.create(InternalBotType.AvatarSummary, "Invalid", BotPlatform.Langflow, BotPlatformRunningType.Endpoint,
                       value='{"agent_llm":"OpenAI","model_name":"fixture"}')
    with pytest.raises(ValueError):
        service.create(InternalBotType.AvatarSummary, "Missing model", BotPlatform.Default, BotPlatformRunningType.Default)
    with pytest.raises(ValueError, match="not executable"):
        build_internal_bot_graph_request(binding, actor, "run tools", "session", project.get_uid(), {})


@pytest.mark.asyncio
async def test_semantic_producer_authenticates_and_sends_only_selected_native_text(semantic_avatar, monkeypatch):
    import httpx
    from langboard_shared.ai.avatar_summary import AvatarSummarySnapshot
    module = importlib.import_module("langboard_shared.domain.services.factory.AvatarSummaryService")
    summary, actor, project, _, binding, _ = semantic_avatar
    context = summary.context(actor, project.get_uid())
    snapshot = AvatarSummarySnapshot(sources=[source for source in context["sources"] if source["kind"] == "card_title"])
    seen = []
    def handler(request):
        seen.append(request)
        payload = json.loads(request.content)
        assert request.headers["Authorization"] == "Bearer local-service-fixture"
        assert request.url.path == "/api/v1/graph/avatar/semantic-draft"
        assert payload["sources"] == snapshot.model_dump()["sources"]
        assert set(payload) == {"model_settings", "sources", "source_revision"}
        assert "app_api_token" not in request.content.decode() and "delete_board" not in request.content.decode()
        return httpx.Response(200, json={"generated": True, "source_revision": context["source_revision"],
            "draft": {"prompt": "A navigation robot icon", "source_refs": [source.id for source in snapshot.sources]}})
    original = httpx.AsyncClient
    monkeypatch.setattr(module, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    monkeypatch.setattr(module, "graph_service_headers", lambda: {"Authorization": "Bearer local-service-fixture"})
    monkeypatch.setattr(type(Env), "DEFAULT_GRAPH_URL", property(lambda _: "http://internal-fixture"))
    draft = await module.request_avatar_summary({"agent_llm": "OpenAI", "model_name": "fixture"}, snapshot, context["source_revision"])
    assert draft.prompt == "A navigation robot icon" and len(seen) == 1


def test_local_semantic_context_rechecks_authority_before_return(semantic_avatar, monkeypatch):
    module = importlib.import_module("langboard_shared.domain.services.factory.AvatarSummaryService")
    summary, actor, project, role, _, _ = semantic_avatar
    original = module.profile_settings
    def revoked(profile):
        result = original(profile)
        with DbSession.atomic() as db:
            role.actions = ["read"]
            db.update(role)
        return result
    monkeypatch.setattr(module, "profile_settings", revoked)
    with pytest.raises(AvatarError) as denied:
        summary.context(actor, project.get_uid())
    assert denied.value.status == 403


@pytest.mark.asyncio
async def test_profile_change_between_review_rebuild_and_binding_read_never_transmits(semantic_avatar, monkeypatch):
    from langboard_shared.domain.services.factory.InternalBotService import InternalBotService
    module = importlib.import_module("langboard_shared.domain.services.factory.AvatarSummaryService")
    summary, actor, project, _, binding, _ = semantic_avatar
    context = summary.context(actor, project.get_uid())
    original = InternalBotService.get_current_by_id_like
    def changed(service, uid):
        with DbSession.atomic() as db:
            binding.value = binding.value.replace("semantic-fixture", "unreviewed-model")
            db.update(binding)
        return original(service, uid)
    monkeypatch.setattr(InternalBotService, "get_current_by_id_like", changed)
    calls = []
    async def forbidden(*args):
        calls.append(args)
    monkeypatch.setattr(module, "request_avatar_summary", forbidden)
    with pytest.raises(AvatarError) as rejected:
        await summary.preview(actor, project.get_uid(), context["source_revision"], binding.get_uid(),
                              [source["id"] for source in context["sources"]], True)
    assert rejected.value.status == 409 and not calls
