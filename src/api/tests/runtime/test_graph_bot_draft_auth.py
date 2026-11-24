"""Native API bot-draft imports and the Graph service-auth HTTP boundary.

Run with both API and Graph dependencies installed and src/graph on PYTHONPATH.
The external model is a fixture; no live provider or deployment is exercised.
"""

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langboard.routes.settings import BotSettingsApi
from langboard_graph.middlewares.GraphServiceAuthMiddleware import GraphServiceAuthMiddleware
from langboard_graph.routes import BotDraftApi
from langboard_shared.core.routing import AppRouter
from langboard_shared.core.security.GraphServiceAuth import verify_graph_service_token
from langboard_shared.Env import Env
from langchain_core.messages import AIMessage


def test_bot_draft_caller_attaches_service_credentials(monkeypatch):
    """Import and execute the production API helper with only its HTTP boundary mocked."""
    monkeypatch.setattr(
        type(Env), "JWT_SECRET_KEY", property(lambda self: "test-only-graph-service-signing-key-32bytes")
    )
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(200, json={"draft": {"bot_name": "Generated"}}, request=httpx.Request("POST", url))

    monkeypatch.setattr(BotSettingsApi.httpx, "post", post)
    assert BotSettingsApi._request_graph_bot_draft("Draft a bot", {"agent_llm": "fixture", "model": "fixture"}, []) == {
        "bot_name": "Generated"
    }
    assert len(calls) == 1
    assert calls[0][0].endswith("/api/v1/graph/bot/draft")
    assert verify_graph_service_token(calls[0][1]["headers"]["Authorization"].removeprefix("Bearer "))


def test_imported_api_bot_draft_reaches_authenticated_graph_route(monkeypatch):
    """Local API helper -> Graph ASGI middleware/route; only the model is a fixture."""
    monkeypatch.setattr(
        type(Env), "JWT_SECRET_KEY", property(lambda self: "test-only-graph-service-signing-key-32bytes")
    )
    model_calls, statuses = [], []

    class DraftModel:
        async def ainvoke(self, messages):
            model_calls.append(messages)
            return AIMessage(content='{"bot_name":"Generated","bot_uname":"generated"}')

    monkeypatch.setattr(BotDraftApi, "create_default_chat_model", lambda *args: DraftModel())
    api = FastAPI()
    api.include_router(AppRouter.api)
    api.add_middleware(GraphServiceAuthMiddleware)
    with TestClient(api) as client:

        def post(url, **kwargs):
            kwargs.pop("timeout", None)
            response = client.post("/api/v1/graph/bot/draft", **kwargs)
            statuses.append(response.status_code)
            # Starlette 1.6 TestClient uses httpx2; the production caller uses httpx.
            return httpx.Response(
                response.status_code,
                content=response.content,
                headers=response.headers,
                request=httpx.Request("POST", url),
            )

        monkeypatch.setattr(BotSettingsApi.httpx, "post", post)
        value = {"agent_llm": "fixture", "model": "fixture"}
        assert BotSettingsApi._request_graph_bot_draft("Draft a bot", value, []) == {
            "bot_name": "Generated",
            "bot_uname": "generated",
        }
        assert statuses == [200] and len(model_calls) == 1
        monkeypatch.setattr(BotSettingsApi, "graph_service_headers", lambda: {})
        assert BotSettingsApi._request_graph_bot_draft("Draft a bot", value, []) is None
        assert statuses == [200, 401] and len(model_calls) == 1
        monkeypatch.setattr(BotSettingsApi, "graph_service_headers", lambda: {"Authorization": "Bearer invalid"})
        assert BotSettingsApi._request_graph_bot_draft("Draft a bot", value, []) is None
        assert statuses == [200, 401, 401] and len(model_calls) == 1
