"""Graph HTTP must reject untrusted callers before route or checkpoint work."""

from time import time
import httpx
import pytest
from fastapi import FastAPI
from jwt import encode
from langboard_shared.core.routing import AppRouter
from langboard_shared.core.security.GraphServiceAuth import graph_service_headers
from langboard_shared.Env import Env
from langboard_graph.middlewares.GraphServiceAuthMiddleware import GraphServiceAuthMiddleware
from langboard_graph.routes import RunApi


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(
        type(Env), "JWT_SECRET_KEY", property(lambda self: "test-only-graph-service-signing-key-32bytes")
    )
    monkeypatch.setattr(
        type(Env), "SOCKET_PHOENIX_INTERNAL_SECRET", property(lambda self: "fixture-socket-secret-only-32bytes")
    )
    api = FastAPI()
    api.include_router(AppRouter.api)
    api.add_middleware(GraphServiceAuthMiddleware)
    touched = []

    async def checkpoint(thread):
        touched.append(thread)
        return {"values": {}, "interrupts": [], "next": []}

    monkeypatch.setattr(RunApi, "get_default_graph_status", checkpoint)
    return api, touched


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "anonymous",
        "invalid",
        "expired",
        "ordinary",
        "wrong_kind",
        "wrong_audience",
        "wrong_issuer",
        "missing_exp",
        "excess_lifetime",
    ],
)
@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/api/v1/graph/run/session"),
        ("POST", "/api/v1/graph/webhook/session"),
        ("POST", "/api/v1/graph/resume/thread"),
        ("GET", "/api/v1/graph/status/thread"),
        ("DELETE", "/api/v1/graph/status/thread"),
    ],
)
async def test_untrusted_http_never_reaches_graph(app, case, method, path):
    api, touched = app
    now = int(time())
    payload = {
        "sub": "langboard-service",
        "iss": Env.PROJECT_NAME,
        "aud": "langboard-graph",
        "internal": "graph_service",
        "iat": now,
        "exp": now + 300,
    }
    if case == "expired":
        payload.update(iat=now - 600, exp=now - 300)
    elif case == "ordinary":
        payload["sub"] = "1"
        payload.pop("internal")
    elif case == "wrong_kind":
        payload["internal"] = "bot"
    elif case == "wrong_audience":
        payload["aud"] = "another-service"
    elif case == "wrong_issuer":
        payload["iss"] = "another-instance"
    elif case == "missing_exp":
        payload.pop("exp")
    elif case == "excess_lifetime":
        payload["exp"] = now + 3600
    token = "invalid" if case == "invalid" else encode(payload, Env.JWT_SECRET_KEY, algorithm=Env.JWT_ALGORITHM)
    headers = {} if case == "anonymous" else {"Authorization": "Bearer " + token}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as client:
        response = await client.request(method, path, headers=headers, json={})
    assert response.status_code == 401
    assert touched == []


@pytest.mark.asyncio
async def test_backend_service_token_reaches_actual_status_route(app):
    api, touched = app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as client:
        response = await client.get("/api/v1/graph/status/thread", headers=graph_service_headers())
    assert response.status_code == 200
    assert touched == ["thread"]


@pytest.mark.asyncio
@pytest.mark.parametrize("secret,expected", [("fixture-socket-secret-only-32bytes", 200), ("wrong", 401), ("", 401)])
async def test_phoenix_native_service_secret(app, secret, expected):
    api, touched = app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as client:
        response = await client.get("/api/v1/graph/status/thread", headers={"x-socket-internal-secret": secret})
    assert response.status_code == expected
    assert touched == (["thread"] if expected == 200 else [])


def test_bot_draft_caller_attaches_service_credentials(monkeypatch):
    """Execute the actual API helper without loading unrelated API routes."""
    import ast
    from pathlib import Path
    from types import SimpleNamespace
    from langboard_shared.core.security.GraphServiceAuth import verify_graph_service_token

    monkeypatch.setattr(
        type(Env), "JWT_SECRET_KEY", property(lambda self: "test-only-graph-service-signing-key-32bytes")
    )
    source = Path(__file__).parents[2] / "api/langboard/routes/settings/BotSettingsApi.py"
    function = next(
        node
        for node in ast.parse(source.read_text()).body
        if isinstance(node, ast.FunctionDef) and node.name == "_request_graph_bot_draft"
    )
    module = ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), function],
        type_ignores=[],
    )
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(200, json={"draft": {"name": "Generated"}}, request=httpx.Request("POST", url))

    namespace = {
        "httpx": SimpleNamespace(post=post, HTTPError=httpx.HTTPError),
        "Env": Env,
        "graph_service_headers": graph_service_headers,
        "_has_graph_draft_model_settings": lambda value: True,
    }
    exec(compile(ast.fix_missing_locations(module), str(source), "exec"), namespace)
    assert namespace["_request_graph_bot_draft"]("Draft a bot", {}, []) == {"name": "Generated"}
    assert len(calls) == 1
    assert calls[0][0].endswith("/api/v1/graph/bot/draft")
    assert verify_graph_service_token(calls[0][1]["headers"]["Authorization"].removeprefix("Bearer "))
