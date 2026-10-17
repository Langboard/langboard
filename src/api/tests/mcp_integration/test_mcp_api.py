"""MCP REST compatibility adapter tests."""

from types import SimpleNamespace
import pytest
from langboard.mcp_integration import Server
from langboard.routes.mcp.McpApi import serialize_mcp_result
from pydantic import BaseModel


def test_typed_results_are_recursively_dumped_as_json() -> None:
    """Nested typed outputs remain structured JSON in the REST compatibility facade."""

    class Card(BaseModel):
        uid: str

    class Result(BaseModel):
        card: Card

    assert serialize_mcp_result({"page": [Result(card=Card(uid="c1"))]}) == {"page": [{"card": {"uid": "c1"}}]}


def test_accessible_type_rejects_wrong_actor(monkeypatch: pytest.MonkeyPatch) -> None:
    """The shared native wrapper keeps user-only and bot-only tools isolated."""

    class FakeUser:
        pass

    class FakeBot:
        pass

    monkeypatch.setattr(Server, "User", FakeUser)
    monkeypatch.setattr(Server, "Bot", FakeBot)
    monkeypatch.setattr(
        Server.McpTool,
        "get_tool",
        lambda name: {"accessible_type": "bot"} if name == "bot_tool" else None,
    )

    assert Server.McpServer._validate_auth(FakeBot(), "bot_tool") is True
    assert Server.McpServer._validate_auth(FakeUser(), "bot_tool") is False
    assert Server.McpServer._validate_auth(SimpleNamespace(), "bot_tool") is False


@pytest.mark.parametrize("conflict", [True, False])
async def test_native_http_dispatch_distinguishes_review_conflict_from_unknown_write(monkeypatch, conflict):
    import importlib
    from fastapi import FastAPI, Request
    from fastmcp import FastMCP
    from httpx import ASGITransport, AsyncClient
    from langboard.card_workspace.domain import WorkPlanConflict
    from langboard_shared.core.security import AuthSecurity

    route = importlib.import_module("langboard.routes.mcp.McpApi")
    name = "apply_card_work_plan"
    group = SimpleNamespace(activated_at=True, tools=[name], user_id=None)
    closed = []
    service = SimpleNamespace(
        mcp_tool_group=SimpleNamespace(get_by_id_like=lambda _: group), close=lambda: closed.append(True)
    )
    monkeypatch.setattr(route, "DomainService", lambda: service)
    monkeypatch.setattr(route, "User", SimpleNamespace)
    monkeypatch.setattr(route.McpTool, "get_tool", lambda _: {"accessible_type": "user"})
    effects = []
    server = FastMCP("work-plan-conflict-http")

    @server.tool(name=name)
    def apply() -> dict:
        if conflict:
            raise WorkPlanConflict("Work plan changed after review; preview again")
        effects.append("saved")
        # Identical text must not turn an untyped failure after persistence into a safe conflict.
        raise ValueError("Work plan changed after review; preview again")

    monkeypatch.setattr(route.McpServer, "mcp", server)
    app = FastAPI()

    @app.post("/mcp/tools/{tool_name}")
    async def dispatch(tool_name: str, request: Request):
        request.scope["auth"] = SimpleNamespace(id=1)
        return await route.execute_mcp_tool(tool_name, request)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/mcp/tools/{name}", json={}, headers={AuthSecurity.MCP_TOOL_GROUP_UID_HEADER: "group"}
        )
    payload = response.json()
    assert response.status_code == (409 if conflict else 500)
    assert payload["outcome"] == ("not_applied" if conflict else "unknown")
    if conflict:
        assert payload["code"] == "REVISION_CONFLICT"
        assert payload["retryable"] is False
        assert payload["next_action"] == "read_and_review"
    assert effects == ([] if conflict else ["saved"])
    assert closed == [True]
    assert route.mcp_auth_context.get() is None
