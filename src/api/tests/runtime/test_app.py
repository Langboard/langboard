import asyncio
import json
import logging
import os
import re
import subprocess
import sys
from threading import Event
from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from httpx import ASGITransport, AsyncClient
from pytest import MonkeyPatch
from starlette.middleware import Middleware


os.environ.setdefault("PROJECT_NAME", "langboard")

from langboard.App import App  # noqa: E402
from langboard.Loader import ModuleLoader  # noqa: E402
from langboard.middlewares import (  # noqa: E402
    ApiAuthMiddleware,
    ChatUploadConcurrencyMiddleware,
    ReadTelemetryMiddleware,
    RoleMiddleware,
)
from langboard_shared.core.routing import AppRouter  # noqa: E402
from langboard_shared.FastAPIAppConfig import FastAPIAppConfigModel  # noqa: E402


def _create_test_app() -> App:
    app = App.__new__(App)
    app.api = FastAPI()
    app.config = FastAPIAppConfigModel(
        host="localhost",
        port=5381,
        lifespan="auto",
        workers=1,
        is_restarting=True,
    )
    return app


def _find_middleware(app: App, middleware_class: object) -> Middleware:
    return next(middleware for middleware in app.api.user_middleware if middleware.cls is middleware_class)


@pytest.mark.parametrize("boundary", ["authentication", "authorization"])
@pytest.mark.parametrize("allowed", [True, False])
async def test_slow_identity_checks_leave_event_loop_responsive(monkeypatch, boundary, allowed) -> None:
    from langboard_shared.core.filter import AuthFilter
    from langboard_shared.domain.models import User
    from langboard_shared.filter import RoleFilter
    from langboard_shared.helpers import MiddlewareHelper
    from langboard_shared.security import RoleSecurity

    loop = asyncio.get_running_loop()
    observed = []
    actor = User(id=1, firstname="Test", lastname="Actor", email="actor@example.invalid", password="test-only")
    scope = {"type": "http", "method": "GET", "path": "/board/test", "headers": [], "auth": actor}

    def slow_check():
        responsive = Event()
        loop.call_soon_threadsafe(responsive.set)
        assert responsive.wait(0.5), "Synchronous identity check blocked the API event loop"
        observed.append(boundary)

    async def downstream(scope, receive, send):
        assert scope["auth"] is actor
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    if boundary == "authentication":

        def validate(scope, **kwargs):
            slow_check()
            return actor if allowed else 401

        monkeypatch.setattr(MiddlewareHelper, "validate_auth", validate)
        monkeypatch.setattr(AuthFilter, "get_filtered", lambda _: "user")
        middleware = ApiAuthMiddleware(downstream, [])
    else:

        def authorize(*args, **kwargs):
            slow_check()
            return allowed

        monkeypatch.setattr(RoleSecurity, "is_authorized", authorize)
        monkeypatch.setattr(RoleFilter, "get_filtered", lambda _: (object, [], None, False))
        monkeypatch.setitem(RoleMiddleware.__call__.__globals__, "Env", SimpleNamespace(FULL_ADMIN_ACCESS_EMAILS=[]))
        middleware = RoleMiddleware(downstream, [])
    monkeypatch.setattr(middleware, "should_filter", lambda _: (True, {"endpoint": object(), "path_params": {}}))
    messages = []

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        messages.append(message)

    await middleware(scope, receive, send)
    assert observed == [boundary]
    expected = 200 if allowed else (401 if boundary == "authentication" else 403)
    assert messages[0]["status"] == expected


@pytest.mark.parametrize("response_status,logging_fails", [(200, False), (403, False), (503, True)])
async def test_api_key_usage_preserves_stream_status_without_blocking_requests(response_status, logging_fails):
    from langboard_shared.helpers import MiddlewareHelper

    loop = asyncio.get_running_loop()
    api_key = object()
    messages = []
    logs = []
    responsiveness = []
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/board/test/cards",
        "client": ("192.0.2.4", 43210),
        "headers": [(b"x-api-key", b"test-only"), (b"x-forwarded-for", b"198.51.100.9")],
        "api_key": api_key,
    }

    async def downstream(scope, receive, send):
        await send({"type": "http.response.start", "status": response_status, "headers": []})
        await send({"type": "http.response.body", "body": b"first", "more_body": True})
        await send({"type": "http.response.body", "body": b"last", "more_body": False})

    def log_usage(**kwargs):
        assert len(messages) == 3, "Usage logging must follow the complete response"
        logs.append(kwargs)
        responsive = Event()
        loop.call_soon_threadsafe(responsive.set)
        responsiveness.append(responsive.wait(0.5))
        if logging_fails:
            raise RuntimeError("Usage storage unavailable")

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        messages.append(message)

    service = SimpleNamespace(api_key=SimpleNamespace(log_usage=log_usage))
    await MiddlewareHelper.log_api_key_usage(downstream, scope, receive, send, service)
    assert messages[0]["status"] == response_status
    assert b"".join(message.get("body", b"") for message in messages) == b"firstlast"
    assert logs == [
        {
            "api_key": api_key,
            "endpoint": "/board/test/cards",
            "method": "GET",
            "ip_address": "192.0.2.4",
            "status_code": response_status,
            "is_success": response_status < 400,
        }
    ]
    assert responsiveness == [True], "Usage storage blocked concurrent API requests"


@pytest.mark.parametrize(
    "route",
    [
        "/board/{project_uid}/cards",
        "/dashboard/work/my",
        "/dashboard/work/assigned",
        "/dashboard/user/projects/starred",
        "/notifications",
        "/settings/notification-schedule",
        "/metadata/project/{project_uid}/cards",
        "/metadata/project/{project_uid}/card/{card_uid}",
        "/metadata/project/{project_uid}/wiki/{wiki_uid}",
    ],
)
async def test_read_telemetry_preserves_stream_and_exposes_safe_correlation(caplog, route) -> None:
    api = FastAPI()
    api.add_middleware(
        CORSMiddleware,
        allow_origins=["https://ui.example.test"],
        expose_headers=["X-Request-ID", "Server-Timing"],
    )
    api.add_middleware(ReadTelemetryMiddleware)

    @api.get(route)
    async def cards(project_uid: str = ""):
        async def chunks():
            yield b"PRIVATE_BODY"
            yield b"_END"

        return StreamingResponse(chunks(), headers={"Server-Timing": "db;dur=2"})

    caplog.set_level(logging.INFO, logger="langboard.http.telemetry")
    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://test") as client:
        response = await client.get(
            route.replace("{project_uid}", "PRIVATE_UID")
            .replace("{card_uid}", "PRIVATE_CARD_UID")
            .replace("{wiki_uid}", "PRIVATE_WIKI_UID")
            + "?token=PRIVATE_QUERY",
            headers={"origin": "https://ui.example.test", "x-request-id": "PRIVATE_HEADER"},
        )
    assert response.content == b"PRIVATE_BODY_END"
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://ui.example.test"
    assert "X-Request-ID" in response.headers["access-control-expose-headers"]
    assert "db;dur=2" in response.headers["server-timing"]
    assert "app;dur=" in response.headers["server-timing"]
    records = [r.message for r in caplog.records if r.name == "langboard.http.telemetry"]
    assert len(records) == 1
    record = json.loads(records[0])
    assert record["request_id"] == response.headers["x-request-id"]
    assert len(record["request_id"]) == 32
    assert record["route"] == route
    assert record["response_bytes"] == len(response.content)
    assert record["completed"] is True
    assert record["duration_ms"] >= record["headers_ms"] >= 0
    assert "PRIVATE_" not in records[0]


@pytest.mark.parametrize("status", [403, 503])
@pytest.mark.parametrize("path", ["/dashboard/projects", "/dashboard/work/my", "/dashboard/work/assigned", "/dashboard/user/projects/starred"])
async def test_read_telemetry_keeps_error_status(status, caplog, path) -> None:
    from fastapi.responses import Response

    api = FastAPI()
    api.add_middleware(ReadTelemetryMiddleware)

    @api.get(path)
    async def projects():
        return Response(status_code=status)

    caplog.set_level(logging.INFO, logger="langboard.http.telemetry")
    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://test") as client:
        response = await client.get(path)
    record = json.loads([r.message for r in caplog.records if r.name == "langboard.http.telemetry"][-1])
    assert response.status_code == record["status"] == status
    assert record["completed"] is True


async def test_read_telemetry_records_unhandled_failure_without_exception_payload(caplog) -> None:
    api = FastAPI()
    api.add_middleware(ReadTelemetryMiddleware)

    @api.get("/board/{project_uid}")
    async def board(project_uid: str):
        raise RuntimeError("PRIVATE_EXCEPTION")

    caplog.set_level(logging.INFO, logger="langboard.http.telemetry")
    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://test") as client:
        with pytest.raises(RuntimeError, match="PRIVATE_EXCEPTION"):
            await client.get("/board/PRIVATE_UID")
    records = [r.message for r in caplog.records if r.name == "langboard.http.telemetry"]
    assert len(records) == 1
    record = json.loads(records[0])
    assert record["status"] == 500
    assert record["completed"] is False
    assert record["headers_ms"] is None
    assert "PRIVATE_" not in records[0]


@pytest.mark.parametrize(
    "scope",
    [
        {"type": "websocket", "path": "/board/test"},
        {"type": "http", "method": "POST", "path": "/board/test/cards"},
        {"type": "http", "method": "POST", "path": "/notifications"},
        {"type": "http", "method": "POST", "path": "/dashboard/work/my"},
        {"type": "http", "method": "GET", "path": "/dashboard/work/unknown"},
        {"type": "http", "method": "POST", "path": "/settings/notification-schedule"},
        {"type": "http", "method": "GET", "path": "/mcp/agent"},
    ],
)
async def test_read_telemetry_leaves_unrelated_transports_untouched(scope, caplog) -> None:
    observed = []

    async def downstream(actual_scope, receive, send):
        assert actual_scope is scope
        await send({"type": "sentinel"})

    async def receive():
        raise AssertionError("Telemetry must not consume request messages")

    async def send(message):
        observed.append(message)

    caplog.set_level(logging.INFO, logger="langboard.http.telemetry")
    await ReadTelemetryMiddleware(downstream)(scope, receive, send)
    assert observed == [{"type": "sentinel"}]
    assert not [r for r in caplog.records if r.name == "langboard.http.telemetry"]


async def test_read_telemetry_counts_compressed_bytes_and_native_trace(caplog) -> None:
    from fastapi.middleware.gzip import GZipMiddleware
    from fastapi.responses import Response
    from sentry_sdk import start_span

    api = FastAPI()
    api.add_middleware(GZipMiddleware, minimum_size=1)
    api.add_middleware(ReadTelemetryMiddleware)

    @api.get("/dashboard/projects")
    async def projects():
        return Response(b"x" * 1024)

    caplog.set_level(logging.INFO, logger="langboard.http.telemetry")
    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://test") as client:
        with start_span(op="test") as span:
            response = await client.get("/dashboard/projects", headers={"accept-encoding": "gzip"})
    record = json.loads([r.message for r in caplog.records if r.name == "langboard.http.telemetry"][-1])
    assert response.content == b"x" * 1024
    assert record["response_bytes"] == int(response.headers["content-length"]) < 1024
    assert record["trace_id"] == span.trace_id


def test_activity_tasks_import_without_cycle() -> None:
    """Match the broker's task discovery order in a clean interpreter."""

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from langboard_shared.tasks.activities import "
                "UserActivityTask, ProjectActivityTask, ProjectWikiActivityTask, "
                "CardActivityTask, CardAttachmentActivityTask"
            ),
        ],
        capture_output=True,
        check=False,
        env={**os.environ, "PROJECT_NAME": "langboard"},
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_project_email_task_imports_first_without_cycle() -> None:
    """The notification worker can discover its task before activity modules."""

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import langboard_shared.tasks.notifications.ProjectEmailNotificationTask",
        ],
        capture_output=True,
        check=False,
        env={**os.environ, "PROJECT_NAME": "langboard"},
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_authorization_middlewares_use_original_api_routes(monkeypatch: MonkeyPatch) -> None:
    """Keep authorization compatible with FastAPI's included-router wrapper."""
    monkeypatch.setenv("PROJECT_NAME", "langboard")

    monkeypatch.setattr(ModuleLoader, "load", lambda *args, **kwargs: {})
    app = _create_test_app()

    app._init_api_middlewares()

    assert _find_middleware(app, ApiAuthMiddleware).kwargs["routes"] is AppRouter.api.routes
    assert _find_middleware(app, RoleMiddleware).kwargs["routes"] is AppRouter.api.routes
    telemetry = _find_middleware(app, ReadTelemetryMiddleware)
    cors = _find_middleware(app, CORSMiddleware)
    assert app.api.user_middleware.index(telemetry) < app.api.user_middleware.index(cors)
    assert {"X-Request-ID", "Server-Timing"}.issubset(cors.kwargs["expose_headers"])


def test_cors_wraps_chat_upload_concurrency_rejections(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(ModuleLoader, "load", lambda *args, **kwargs: {})
    app = _create_test_app()

    app._init_api_middlewares()

    cors_index = next(
        index for index, middleware in enumerate(app.api.user_middleware) if middleware.cls is CORSMiddleware
    )
    upload_index = next(
        index
        for index, middleware in enumerate(app.api.user_middleware)
        if middleware.cls is ChatUploadConcurrencyMiddleware
    )
    assert cors_index < upload_index


def test_production_cors_uses_configured_mcp_origins(monkeypatch: MonkeyPatch) -> None:
    """Production accepts only the UI and explicitly configured MCP clients."""

    monkeypatch.setattr(ModuleLoader, "load", lambda *args, **kwargs: {})
    monkeypatch.setitem(
        App._init_api_middlewares.__globals__,
        "Env",
        SimpleNamespace(
            ENVIRONMENT="production",
            MCP_ALLOWED_ORIGINS=["http://localhost:6274", "https://mcp.example.test"],
            PUBLIC_UI_URL="https://board.example.test",
        ),
    )
    app = _create_test_app()

    app._init_api_middlewares()

    cors = _find_middleware(app, CORSMiddleware)
    assert cors.kwargs["allow_origins"] == [
        "https://board.example.test",
        "http://localhost:6274",
        "https://mcp.example.test",
    ]
    assert cors.kwargs["allow_origin_regex"] is None


def test_development_cors_accepts_local_clients_on_any_port(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(ModuleLoader, "load", lambda *args, **kwargs: {})
    monkeypatch.setitem(
        App._init_api_middlewares.__globals__,
        "Env",
        SimpleNamespace(
            ENVIRONMENT="development",
            MCP_ALLOWED_ORIGINS=[],
            PUBLIC_UI_URL="http://localhost:5173",
        ),
    )
    app = _create_test_app()

    app._init_api_middlewares()

    cors = _find_middleware(app, CORSMiddleware)
    origin_regex = cors.kwargs["allow_origin_regex"]
    assert isinstance(origin_regex, str)
    assert re.fullmatch(origin_regex, "http://localhost:49152")
    assert not re.fullmatch(origin_regex, "https://example.test")
