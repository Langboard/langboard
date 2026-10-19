"""Native profile persistence, authenticated routes and controlled image transports."""

import asyncio
import importlib
import json
from base64 import b64decode, b64encode
from io import BytesIO
from types import SimpleNamespace
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langboard.middlewares import ApiAuthMiddleware, RoleMiddleware
from langboard.routes.settings import ImageGenerationApi as routes
from langboard.routes.settings import InternalBotSettingsApi as settings
from langboard.routes.settings.Form import CreateInternalBotForm, UpdateInternalBotForm
from langboard_shared.ai import image_generation as images
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.filter import AuthFilter
from langboard_shared.core.routing import AppRouter
from langboard_shared.core.security import KeyVault
from langboard_shared.core.security.vault.LocalDevVaultProvider import LocalDevVaultProvider
from langboard_shared.domain.models import InternalBot, SettingRole, User
from langboard_shared.domain.models.BaseBotModel import BotPlatform, BotPlatformRunningType
from langboard_shared.domain.models.InternalBot import InternalBotType
from langboard_shared.domain.models.SettingRole import SettingRoleAction
from langboard_shared.domain.services.factory.InternalBotService import InternalBotService
from langboard_shared.filter import RoleFilter
from langboard_shared.helpers import MiddlewareHelper
from langboard_shared.infrastructure.repositories import Repository
from langboard_shared.publishers import InternalBotPublisher
from langboard_shared.security import RoleSecurity
from PIL import Image, PngImagePlugin


CONFIG = {"agent_llm": "OpenAI", "base_url": "https://images.example/v1", "model_name": "gpt-image-1", "enabled": True}


def raster(format="PNG", size=(128, 96)):
    output = BytesIO()
    info = PngImagePlugin.PngInfo()
    info.add_text("secret", "metadata-must-be-stripped")
    Image.new("RGB", size, "blue").save(output, format, **({"pnginfo": info} if format == "PNG" else {}))
    return output.getvalue()


@pytest.fixture
def native(board, monkeypatch, tmp_path):
    engine = DbEngine.get_main_engine()
    InternalBot.__table__.create(engine)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    monkeypatch.setattr(KeyVault, "provider", LocalDevVaultProvider(tmp_path / "vault"))
    monkeypatch.setattr(images, "allowed_bases", lambda: set())
    published = []
    monkeypatch.setattr(InternalBotPublisher, "put_dispather", lambda data, model: published.append(data))
    bots = InternalBotService(lambda _: None, lambda _: None, Repository())
    service = SimpleNamespace(internal_bot=bots, close=lambda: None)
    form = CreateInternalBotForm(
        bot_type=InternalBotType.ImageGeneration,
        display_name="Image profile",
        platform=BotPlatform.Default,
        platform_running_type=BotPlatformRunningType.Default,
        value=json.dumps(CONFIG),
        api_key="fixture-secret",
    )
    response = settings.create_internal_bot(form, None, service)
    public = json.loads(response.body)["internal_bot"]
    binding = bots.get_current_by_id_like(public["uid"])
    return service, binding, published


@pytest.fixture
def transport(monkeypatch):
    requests = []
    state = {"status": 200, "body": {"data": [{"b64_json": b64encode(raster()).decode()}]}, "callback": None}

    async def target(url):
        return images.ResolvedWebhookTarget(
            url.replace("images.example", "93.184.216.34"), "images.example", "images.example"
        )

    def handler(request):
        requests.append(request)
        if state["callback"]:
            state["callback"]()
        if state.get("timeout"):
            raise httpx.ReadTimeout("fixture-secret upstream detail")
        if request.method == "GET" and str(request.url).endswith("models"):
            return httpx.Response(200, json={"data": [{"id": "z"}, {"id": "a"}]})
        if request.method == "GET":
            return httpx.Response(200, content=state.get("download", raster()))
        return httpx.Response(state["status"], json=state["body"])

    client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: client(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(images, "AsyncClient", httpx.AsyncClient)
    monkeypatch.setattr(images, "ensure_public_webhook_url", target)
    return requests, state


def preview(native, confirmed=True):
    service, binding, _ = native
    response = asyncio.run(
        routes.preview_image(
            binding.get_uid(), routes.ImagePreviewForm(prompt="A simple moon", confirmed=confirmed), service
        )
    )
    return response.status_code, json.loads(response.body)


def test_native_create_update_copy_list_and_publish_redact_keys(native):
    service, binding, published = native
    assert binding.api_key != "fixture-secret"
    assert KeyVault.get_key(binding.api_key) == "fixture-secret"
    old_locator = binding.api_key
    response = settings.update_internal_bot(
        binding.get_uid(), UpdateInternalBotForm(api_key="", display_name="Renamed"), None, service
    )
    assert response.status_code == 200
    assert service.internal_bot.get_current_by_id_like(binding).api_key == old_locator
    copy = service.internal_bot.copy(binding)
    assert copy.api_key != old_locator
    assert KeyVault.get_key(copy.api_key) == "fixture-secret"
    listed = service.internal_bot.get_api_list(True)
    for row in listed:
        assert "api_key" not in row
        assert row["api_key_configured"] is True
    serialized = json.dumps([listed, published], default=str)
    assert "fixture-secret" not in serialized and old_locator not in serialized and copy.api_key not in serialized
    rotated = service.internal_bot.update(binding, {"api_key": "rotated-secret"})
    assert KeyVault.get_key(rotated.api_key) == "rotated-secret"
    assert KeyVault.get_key(copy.api_key) == "fixture-secret"


def test_non_image_profile_retains_existing_response_contract(native):
    service, _, _ = native
    bot = service.internal_bot.create(
        InternalBotType.ProjectChat,
        "Chat",
        BotPlatform.Default,
        BotPlatformRunningType.Default,
        value="existing",
        api_key="legacy-key",
    )
    assert bot.api_response(is_setting=True)["api_key"] == "legacy-key"
    assert "api_key_configured" not in bot.api_response(is_setting=True)


@pytest.mark.parametrize("extra", [{"api_key": "leak"}, {"secret_ref": "locator"}, {"agent_llm": "Anthropic"}])
def test_secret_injection_and_unsupported_provider_rejected(native, extra):
    service, binding, _ = native
    with pytest.raises(ValueError):
        service.internal_bot.update(binding, {"value": json.dumps({**CONFIG, **extra})})


def test_endpoint_change_requires_explicit_credentials(native):
    service, binding, _ = native
    changed = json.dumps({**CONFIG, "base_url": "https://other.example/v1"})
    with pytest.raises(ValueError):
        service.internal_bot.update(binding, {"value": changed, "api_key": ""})
    result = service.internal_bot.update(binding, {"value": changed, "api_key": "new-explicit-key"})
    assert KeyVault.get_key(result.api_key) == "new-explicit-key"


def test_generation_requires_confirmation_and_enablement(native, transport):
    requests, _ = transport
    assert preview(native, False) == (400, {"error": "image_generation_confirmation_required"})
    native[0].internal_bot.update(native[1], {"value": json.dumps({**CONFIG, "enabled": False})})
    assert preview(native)[0] == 409
    assert requests == []


def test_generation_pins_host_sni_no_retries_and_sanitizes_raster(native, transport):
    requests, _ = transport
    code, result = preview(native)
    assert code == 200 and result["width"] == result["height"] == 512
    with Image.open(BytesIO(b64decode(result["preview_data_url"].split(",")[1]))) as image:
        assert image.size == (512, 512) and image.format == "PNG" and image.info == {}
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == "https://93.184.216.34/v1/images/generations"
    assert request.headers["host"] == "images.example"
    assert request.headers["authorization"] == "Bearer fixture-secret"
    assert request.extensions["sni_hostname"] == "images.example"
    payload = json.loads(request.content)
    assert payload["n"] == 1 and "response_format" not in payload


@pytest.mark.parametrize("status,expected", [(301, 502), (401, 502), (429, 429), (500, 502)])
def test_error_redaction_and_no_billable_retry(native, transport, status, expected):
    requests, state = transport
    state.update(status=status, body={"error": "fixture-secret"})
    code, body = preview(native)
    assert code == expected and "fixture-secret" not in json.dumps(body)
    assert len(requests) == 1


def test_timeout_is_redacted_and_not_retried(native, transport):
    requests, state = transport
    state["timeout"] = True
    assert preview(native) == (504, {"error": "image_generation_timeout"})
    assert len(requests) == 1


@pytest.mark.parametrize("change", ["disable", "rotate", "revoke", "delete"])
def test_current_binding_revocation_blocks_returning_content(native, transport, change):
    service, binding, _ = native
    _, state = transport

    def changed():
        if change == "disable":
            service.internal_bot.update(binding, {"value": json.dumps({**CONFIG, "enabled": False})})
        elif change == "rotate":
            service.internal_bot.update(binding, {"api_key": "rotated"})
        elif change == "revoke":
            KeyVault.delete_key(binding.api_key)
        else:
            service.internal_bot.repo.internal_bot.delete(binding)

    state["callback"] = changed
    code, body = preview(native)
    assert code in (404, 409, 502) and "preview_data_url" not in body


@pytest.mark.parametrize(
    "body",
    [
        {"data": []},
        {"data": [{"b64_json": "!invalid"}]},
        {"data": [{"b64_json": b64encode(b"<svg>danger</svg>").decode()}]},
        {"data": [{"b64_json": b64encode(raster(size=(8, 8))).decode()}]},
        {"data": [{"b64_json": b64encode(raster(size=(4097, 32))).decode()}]},
        {"data": [{"b64_json": "A" * (images.MAX_IMAGE_BYTES * 2)}]},
    ],
)
def test_provider_format_and_size_failures_are_redacted(native, transport, body):
    transport[1]["body"] = body
    assert preview(native)[0] == 502


@pytest.mark.parametrize("format", ["PNG", "JPEG", "WEBP"])
def test_supported_rasters(format):
    assert images.safe_preview(raster(format)).startswith("data:image/png;base64,")


def test_returned_url_is_separately_validated_without_authorization(native, transport, monkeypatch):
    requests, state = transport
    state["body"] = {"data": [{"url": "https://cdn.example/image.png?signed=allowed"}]}
    seen = []

    async def target(url):
        seen.append(url)
        return images.ResolvedWebhookTarget(
            url.replace("images.example", "93.184.216.34").replace("cdn.example", "93.184.216.35"),
            "cdn.example" if "cdn.example" in url else "images.example",
            "cdn.example",
        )

    monkeypatch.setattr(images, "ensure_public_webhook_url", target)
    assert preview(native)[0] == 200
    assert len(seen) == 2 and len(requests) == 2
    assert "authorization" not in requests[1].headers
    assert requests[1].headers["host"] == "cdn.example"


@pytest.mark.parametrize("url", ["https://127.0.0.1/a", "http://private.example/a", "https://user:pass@example.com/a"])
def test_returned_unsafe_url_never_fetched(native, transport, monkeypatch, url):
    from langboard_shared.tasks.webhooks.utils import ensure_public_webhook_url

    requests, state = transport
    state["body"] = {"data": [{"url": url}]}
    original = images.ensure_public_webhook_url

    async def target(candidate):
        return (
            await original(candidate)
            if candidate.endswith("images/generations")
            else await ensure_public_webhook_url(candidate)
        )

    monkeypatch.setattr(images, "ensure_public_webhook_url", target)
    assert preview(native)[0] == 502 and len(requests) == 1


def test_allowlisted_private_base_is_exact(native, transport, monkeypatch):
    service, binding, _ = native
    monkeypatch.setattr(images, "allowed_bases", lambda: {"http://litellm:4000/v1"})
    service.internal_bot.update(
        binding, {"value": json.dumps({**CONFIG, "base_url": "http://litellm:4000/v1"}), "api_key": "explicit"}
    )
    assert preview(native)[0] == 200
    assert str(transport[0][0].url) == "http://litellm:4000/v1/images/generations"
    with pytest.raises(ValueError):
        images.validate_image_config(json.dumps({**CONFIG, "base_url": "http://litellm:4000/other"}))


def test_saved_models_use_vault_key_and_readback(native, transport):
    service, binding, _ = native
    response = asyncio.run(routes.get_image_models(binding.get_uid(), service))
    assert response.status_code == 200 and json.loads(response.body) == {"models": ["a", "z"]}
    assert transport[0][0].headers["authorization"] == "Bearer fixture-secret"
    assert transport[0][0].method == "GET"


def test_image_profiles_excluded_from_executable_defaults(native):
    service, _, _ = native
    for kind in (
        InternalBotType.DocumentVision,
        InternalBotType.DocumentEmbedding,
        InternalBotType.ImageGeneration,
        InternalBotType.ProjectChat,
    ):
        with DbSession.use(readonly=False) as db:
            db.insert(
                InternalBot(
                    bot_type=kind,
                    display_name=kind.value,
                    platform=BotPlatform.Default,
                    platform_running_type=BotPlatformRunningType.Default,
                    is_default=True,
                )
            )
    assert [row.bot_type for row in service.internal_bot.repo.internal_bot.get_default_list()] == [
        InternalBotType.ProjectChat
    ]


@pytest.fixture
def native_http(native, monkeypatch):
    service = native[0]
    actor = User.model_construct(id=1, is_admin=True, email="image-admin@example.invalid")
    identity = {"actor": actor, "allowed": True}

    def validate(scope, **kwargs):
        scope["auth"] = identity["actor"]
        return identity["actor"]

    monkeypatch.setattr(MiddlewareHelper, "validate_auth", validate)
    middleware = importlib.import_module("langboard.middlewares.ApiAuthMiddleware")
    monkeypatch.setattr(middleware, "DomainService", lambda: service)
    monkeypatch.setattr(RoleSecurity, "is_authorized", lambda *args, **kwargs: identity["allowed"])
    app = FastAPI()
    app.include_router(AppRouter.api)
    for route in AppRouter.api.routes:
        if getattr(route, "endpoint", None) in (routes.preview_image, routes.get_image_models):
            for dependency in route.dependant.dependencies:
                if dependency.call is not None and "create_factory" in str(dependency.call):
                    app.dependency_overrides[dependency.call] = lambda: service
    app.add_middleware(RoleMiddleware, routes=AppRouter.api.routes)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    with TestClient(app) as client:
        yield client, identity, native[1]


@pytest.mark.parametrize("handler", [routes.get_image_capabilities, routes.preview_image, routes.get_image_models])
def test_route_native_auth_role_metadata(handler):
    assert AuthFilter.get_filtered(handler) == "admin"
    model, actions, _, all_admin = RoleFilter.get_filtered(handler)
    assert model is SettingRole and actions == [SettingRoleAction.InternalBotUpdate.value] and all_admin is False


def test_http_auth_role_and_explicit_generation(native_http, transport):
    client, identity, binding = native_http
    path = f"/settings/image-generation/{binding.get_uid()}/preview"
    body = {"prompt": "A moon", "confirmed": True}
    identity["actor"] = 401
    assert client.post(path, data=body).status_code == 401
    identity["actor"] = User.model_construct(id=1, is_admin=False)
    assert client.post(path, data=body).status_code == 403
    identity["actor"] = User.model_construct(id=1, is_admin=True, email="image-admin@example.invalid")
    identity["allowed"] = False
    assert client.post(path, data=body).status_code == 403
    identity["allowed"] = True
    assert client.get("/settings/image-generation/capabilities").status_code == 200
    assert transport[0] == []
    assert client.post(path, data={"prompt": "A moon"}).status_code == 400
    response = client.post(path, data=body)
    assert response.status_code == 200, response.text
    assert response.json()["preview_data_url"].startswith("data:image/png;base64,")


def test_native_image_delete_does_not_create_empty_default(native):
    service, binding, _ = native
    assert service.internal_bot.delete(binding) is True
    assert service.internal_bot.get_current_by_id_like(binding) is None


def test_native_board_template_and_selector_exclude_image(native, board):
    from langboard.routes.settings.ProjectTemplateSettingsApi import get_project_template_bots
    from langboard_shared.domain.models import ProjectTemplate
    from langboard_shared.domain.services.factory.ProjectService import ProjectService
    from langboard_shared.domain.services.factory.ProjectTemplateService import ProjectTemplateService

    service, binding, _ = native
    project_service = ProjectService(None, None, service.internal_bot.repo)
    assert project_service.change_internal_bot(board[2], binding) is False
    engine = DbEngine.get_main_engine()
    ProjectTemplate.__table__.create(engine)
    template_service = ProjectTemplateService(None, None, service.internal_bot.repo)
    with pytest.raises(ValueError, match="template bot role"):
        template_service.save_columns("Image excluded", ["Queue"], internal_bot_uids=[binding.get_uid()])
    response = get_project_template_bots(service)
    assert json.loads(response.body) == {"bots": []}


@pytest.mark.asyncio
async def test_nonwaiting_capacity_offloads_decode_and_survives_failure_cancel(monkeypatch):
    from threading import BoundedSemaphore, Event
    from anyio import to_thread

    monkeypatch.setattr(images, "_IMAGE_CAPACITY", BoundedSemaphore(2))
    entered = [Event(), Event()]
    release = Event()

    async def generated(binding, prompt):
        return prompt.encode()

    def blocked_decode(raw):
        number = 0 if raw == b"first" else 1
        entered[number].set()
        assert release.wait(5), "test worker release timed out"
        if number == 1:
            raise ValueError("decode failed")
        return "preview"

    monkeypatch.setattr(images, "_generate_preview_bytes", generated)
    monkeypatch.setattr(images, "safe_preview", blocked_decode)
    first = asyncio.create_task(images.generate_preview(None, "first"))
    second = asyncio.create_task(images.generate_preview(None, "second"))
    try:
        # These awaits and the heartbeat require a responsive event loop while
        # both real AnyIO raster workers are blocked.
        assert await to_thread.run_sync(entered[0].wait, 2)
        assert await to_thread.run_sync(entered[1].wait, 2)
        heartbeat = asyncio.create_task(asyncio.sleep(0, result="alive"))
        assert await asyncio.wait_for(heartbeat, 0.2) == "alive"
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        with pytest.raises(images.ImageGenerationError) as rejected:
            await asyncio.wait_for(images.generate_preview(None, "third"), 0.2)
        assert rejected.value.status == 429
    finally:
        release.set()
    with pytest.raises(ValueError, match="decode failed"):
        await second
    # The cancelled request's worker still owns its permit until completion.
    for _ in range(100):
        if images._IMAGE_CAPACITY.acquire(blocking=False):
            if images._IMAGE_CAPACITY.acquire(blocking=False):
                images._IMAGE_CAPACITY.release()
                images._IMAGE_CAPACITY.release()
                break
            images._IMAGE_CAPACITY.release()
        await asyncio.sleep(0.01)
    else:
        pytest.fail("capacity did not return after cancellation and decode failure")
    monkeypatch.setattr(images, "safe_preview", lambda raw: "recovered")
    assert await images.generate_preview(None, "later") == "recovered"


def test_owned_image_secrets_cleanup_only_after_successful_commit(native, monkeypatch):
    from sqlalchemy import event

    service, binding, published = native
    provider = KeyVault.provider
    original_locator = binding.api_key
    before = {path.name for path in provider.base_dir.iterdir()}
    original_update = service.internal_bot.repo.internal_bot.update

    def update_then_fail(record):
        original_update(record)
        # Old material must survive until the write transaction commits.
        assert provider.get_key(original_locator) == "fixture-secret"
        raise RuntimeError("database write failed")

    monkeypatch.setattr(service.internal_bot.repo.internal_bot, "update", update_then_fail)
    published_before = len(published)
    with pytest.raises(RuntimeError, match="database write failed"):
        service.internal_bot.update(binding, {"api_key": "new material"})
    assert service.internal_bot.get_current_by_id_like(binding).api_key == original_locator
    assert {path.name for path in provider.base_dir.iterdir()} == before
    assert len(published) == published_before
    monkeypatch.setattr(service.internal_bot.repo.internal_bot, "update", original_update)
    engine = DbEngine.get_main_engine()

    def commit_failure(connection):
        assert provider.get_key(original_locator) == "fixture-secret"
        raise RuntimeError("database commit failed")

    event.listen(engine, "commit", commit_failure)
    try:
        with pytest.raises(RuntimeError, match="database commit failed"):
            service.internal_bot.update(binding, {"api_key": "uncommitted material"})
    finally:
        event.remove(engine, "commit", commit_failure)
    assert {path.name for path in provider.base_dir.iterdir()} == before
    changed = service.internal_bot.update(binding, {"api_key": "committed material"})
    with pytest.raises(KeyError):
        provider.get_key(original_locator)
    new_locator = changed.api_key
    assert provider.get_key(new_locator) == "committed material"
    assert service.internal_bot.delete(changed)
    with pytest.raises(KeyError):
        provider.get_key(new_locator)


@pytest.mark.parametrize("operation", ["create", "copy"])
def test_create_copy_db_failure_compensates_only_new_owned_secret(native, monkeypatch, operation):
    service, binding, _ = native
    provider = KeyVault.provider
    before = {path.name for path in provider.base_dir.iterdir()}

    def failure(record):
        raise RuntimeError("insert failed")

    monkeypatch.setattr(service.internal_bot.repo.internal_bot, "insert", failure)
    with pytest.raises(RuntimeError, match="insert failed"):
        if operation == "copy":
            service.internal_bot.copy(binding)
        else:
            service.internal_bot.create(
                InternalBotType.ImageGeneration,
                "Failing",
                BotPlatform.Default,
                BotPlatformRunningType.Default,
                value=json.dumps(CONFIG),
                api_key="new material",
            )
    assert {path.name for path in provider.base_dir.iterdir()} == before
    assert provider.get_key(binding.api_key) == "fixture-secret"


def test_cleanup_preserves_shared_and_legacy_locators_and_rejects_host_transaction(native):
    service, binding, _ = native
    old_locator = binding.api_key
    with DbSession.use(readonly=False) as db:
        shared = InternalBot(
            bot_type=InternalBotType.ProjectChat,
            display_name="Legacy reference",
            platform=BotPlatform.Default,
            platform_running_type=BotPlatformRunningType.Default,
            api_key=old_locator,
        )
        db.insert(shared)
    service.internal_bot.update(binding, {"api_key": "rotated"})
    assert KeyVault.get_key(old_locator) == "fixture-secret"
    KeyVault.store_secret("legacy-locator", "legacy material")
    with DbSession.use(readonly=False) as db:
        current = service.internal_bot.get_current_by_id_like(binding)
        current.api_key = "legacy-locator"
        db.update(current)
    service.internal_bot.update(current, {"api_key": "new owned material"})
    assert KeyVault.get_key("legacy-locator") == "legacy material"
    provider = KeyVault.provider
    before = {path.name for path in provider.base_dir.iterdir()}
    with DbSession.atomic():
        with pytest.raises(RuntimeError, match="must own its transaction"):
            service.internal_bot.update(current, {"api_key": "disallowed material"})
    assert {path.name for path in provider.base_dir.iterdir()} == before


def test_executable_policy_is_explicit_and_unknown_selector_fails_closed():
    from langboard.routes.settings.ProjectTemplateSettingsApi import get_project_template_bots

    allowed = {InternalBotType.ProjectChat, InternalBotType.EditorChat, InternalBotType.EditorCopilot}
    assert set(InternalBotType.executable_types()) == allowed
    assert all(kind.is_executable == (kind in allowed) for kind in InternalBotType)
    service = SimpleNamespace(
        internal_bot=SimpleNamespace(
            get_api_list=lambda **kwargs: [
                {"uid": kind.value, "bot_type": kind.value, "display_name": kind.value} for kind in InternalBotType
            ]
            + [{"uid": "future", "bot_type": "future_unknown", "display_name": "Future"}]
        )
    )
    response = get_project_template_bots(service)
    assert {row["bot_type"] for row in json.loads(response.body)["bots"]} == {kind.value for kind in allowed}
