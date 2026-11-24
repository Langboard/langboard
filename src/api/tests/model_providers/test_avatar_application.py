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
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.routing import AppRouter
from langboard_shared.core.security import KeyVault
from langboard_shared.core.security.vault.LocalDevVaultProvider import LocalDevVaultProvider
from langboard_shared.core.storage import Storage
from langboard_shared.core.storage.LocalStorage import LocalStorage
from langboard_shared.domain.models import Card, InternalBot, UserProfile
from langboard_shared.domain.models.BaseBotModel import BotPlatform, BotPlatformRunningType
from langboard_shared.domain.models.InternalBot import InternalBotType
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
    for model in (InternalBot, Card, UserProfile):
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
    assert "active=1" in prepared["prompt"]
    serialized = json.dumps([profiles, prepared])
    for sensitive in ("secret-title", "Private client", "never-expose", "private-vault", "images.example", "PRIVATE"):
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
        boards.preview_board_avatar,
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
    assert scripts.get_heads() == ["a28196e32054"]
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
