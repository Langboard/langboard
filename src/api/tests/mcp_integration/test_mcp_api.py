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


@pytest.mark.parametrize("busy", [False, True])
async def test_preview_http_distinguishes_busy_from_invalid_without_exposing_database_details(monkeypatch, busy):
    import importlib
    from fastapi import FastAPI, Request
    from fastmcp import FastMCP
    from fastmcp.exceptions import ValidationError
    from httpx import ASGITransport, AsyncClient
    from langboard.card_workspace.application.work_plan import WorkPlanRejected
    from langboard_shared.core.security import AuthSecurity

    route = importlib.import_module("langboard.routes.mcp.McpApi")
    group = SimpleNamespace(activated_at=True, tools=["preview_card_work_plan"], user_id=None)
    monkeypatch.setattr(
        route,
        "DomainService",
        lambda: SimpleNamespace(mcp_tool_group=SimpleNamespace(get_by_id_like=lambda _: group), close=lambda: None),
    )
    monkeypatch.setattr(route, "User", SimpleNamespace)
    monkeypatch.setattr(route.McpTool, "get_tool", lambda _: {"accessible_type": "user"})
    server = FastMCP("preview-rejection-http")
    rejection = WorkPlanRejected(
        "WORK_PLAN_BUSY" if busy else "WORK_PLAN_INVALID",
        "Safe guidance",
        "retry_preview" if busy else "revise_plan",
        retryable=busy,
    )

    @server.tool(name="preview_card_work_plan")
    def preview() -> dict:
        raise ValidationError(str(rejection)) from rejection

    monkeypatch.setattr(route.McpServer, "mcp", server)
    app = FastAPI()

    @app.post("/preview")
    async def dispatch(request: Request):
        request.scope["auth"] = SimpleNamespace(id=1)
        return await route.execute_mcp_tool("preview_card_work_plan", request)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/preview", json={}, headers={AuthSecurity.MCP_TOOL_GROUP_UID_HEADER: "group"})
    assert response.status_code == (503 if busy else 400)
    assert response.json() == dict(
        code=rejection.code,
        message="Safe guidance",
        request_id=rejection.request_id,
        outcome="not_applied",
        retryable=busy,
        next_action=rejection.next_action,
    )
    assert response.headers["X-Request-ID"] == rejection.request_id


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
@pytest.mark.parametrize("name", ["apply_card_work_plan", "preview_card_work_plan"])
async def test_native_http_dispatch_distinguishes_review_conflict_from_unknown_write(monkeypatch, conflict, name):
    import importlib
    from fastapi import FastAPI, Request
    from fastmcp import FastMCP
    from httpx import ASGITransport, AsyncClient
    from langboard.card_workspace.domain import WorkPlanConflict
    from langboard_shared.core.security import AuthSecurity

    route = importlib.import_module("langboard.routes.mcp.McpApi")
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


@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"request_uid": "00000000-0000-4000-8000-000000000001"},
        {
            "request_uid": "00000000-0000-4000-8000-000000000001",
            "duplicate_action": "add",
            "confirmation_token": "current",
        },
        {
            "request_uid": "00000000-0000-4000-8000-000000000001",
            "duplicate_action": "replace",
            "confirmation_token": "current",
            "replacement_uid": "file",
        },
        {"request_uid": "00000000-0000-4000-8000-000000000001", "duplicate_action": "cancel"},
        {"request_uid": "invalid"},
    ],
)
async def test_http_versioned_fields_use_modern_validation_without_changing_legacy(monkeypatch, extra):
    import importlib
    from typing import Annotated, Literal
    from uuid import UUID
    from fastapi import FastAPI, Request
    from fastmcp import FastMCP
    from fastmcp.tools import Tool
    from httpx import ASGITransport, AsyncClient
    from langboard.mcp_integration.Providers import without_modern_parameters
    from langboard.mcp_tools.CardMcp import _as_upload_request_uid
    from langboard_shared.core.security import AuthSecurity
    from pydantic import BeforeValidator

    route = importlib.import_module("langboard.routes.mcp.McpApi")
    modern_fields = ("request_uid", "duplicate_action", "confirmation_token", "replacement_uid")
    effects, closed, modern_calls = [], [], []

    async def upload(
        filename: str,
        file_data_base64: str,
        request_uid: Annotated[UUID, BeforeValidator(_as_upload_request_uid)] | None = None,
        duplicate_action: Literal["add", "replace", "cancel"] | None = None,
        confirmation_token: str | None = None,
        replacement_uid: str | None = None,
    ) -> dict:
        payload = {"filename": filename, "file_data_base64": file_data_base64}
        payload.update(
            {
                key: value
                for key, value in {
                    "request_uid": str(request_uid) if request_uid is not None else None,
                    "duplicate_action": duplicate_action,
                    "confirmation_token": confirmation_token,
                    "replacement_uid": replacement_uid,
                }.items()
                if value is not None
            }
        )
        effects.append(payload)
        return {"confirmation_required": True, "confirmation_token": "next", "request_uid": str(request_uid)}

    legacy, modern = FastMCP("legacy", strict_input_validation=True), FastMCP("modern", strict_input_validation=True)
    legacy.add_tool(Tool.from_function(without_modern_parameters(upload, modern_fields), name="upload_card_attachment"))
    modern.add_tool(Tool.from_function(upload, name="upload_card_attachment"))
    group = SimpleNamespace(activated_at=True, tools=["upload_card_attachment"], user_id=None)
    service = SimpleNamespace(
        mcp_tool_group=SimpleNamespace(get_by_id_like=lambda _: group), close=lambda: closed.append(True)
    )
    monkeypatch.setattr(route, "DomainService", lambda: service)
    monkeypatch.setattr(route, "User", SimpleNamespace)
    monkeypatch.setattr(route.McpTool, "get_tool", lambda _: {"accessible_type": "user", "modern_only": modern_fields})
    monkeypatch.setattr(route.McpServer, "mcp", legacy)
    monkeypatch.setattr(route.McpServer, "get_modern_command_server", lambda: modern_calls.append(True) or modern)
    app = FastAPI()

    @app.post("/mcp/tools/{tool_name}")
    async def dispatch(tool_name: str, request: Request):
        request.scope["auth"] = SimpleNamespace(id=1)
        return await route.execute_mcp_tool(tool_name, request)

    payload = {"filename": "proof.txt", "file_data_base64": "eA==", **extra}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/mcp/tools/upload_card_attachment", json=payload, headers={AuthSecurity.MCP_TOOL_GROUP_UID_HEADER: "group"}
        )
    invalid = extra.get("request_uid") == "invalid"
    assert response.status_code == (400 if invalid else 200)
    assert effects == ([] if invalid else [payload])
    if not invalid:
        assert response.json()["result"]["confirmation_required"] is True
        assert response.json()["result"]["confirmation_token"] == "next"
    assert modern_calls == ([True] if extra else [])
    assert closed == [True]
    assert route.mcp_auth_context.get() is None
