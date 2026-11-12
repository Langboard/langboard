"""Card workspace MCP transport contract tests."""

import pytest
from langboard.card_workspace.domain import CardBundleInclude, ChecklistProjectionItem
from langboard.mcp_tools.CardMcp import JsonCardBundleInclude, JsonChecklistProjectionItem
from pydantic import TypeAdapter, ValidationError


def test_card_bundle_include_accepts_json_strings_and_rejects_unknown_values() -> None:
    """Keep the advertised JSON schema aligned with runtime validation."""

    adapter = TypeAdapter(list[JsonCardBundleInclude])
    assert adapter.validate_python(["description", "metadata"], strict=True) == [
        CardBundleInclude.Description,
        CardBundleInclude.Metadata,
    ]
    with pytest.raises(ValidationError):
        adapter.validate_python(["unknown"], strict=True)


def test_checklist_projection_item_accepts_json_objects() -> None:
    """Accept the JSON object shape advertised by the MCP tool schema."""

    adapter = TypeAdapter(list[JsonChecklistProjectionItem])
    assert adapter.validate_python(
        [{"key": "invoice:2026-08", "title": "August invoice"}],
        strict=True,
    ) == [ChecklistProjectionItem("invoice:2026-08", "August invoice")]

    with pytest.raises(ValidationError):
        adapter.validate_python([{"key": "invalid key", "title": "August invoice"}], strict=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["agent", "compatibility"])
@pytest.mark.parametrize("allowed", [False, True])
@pytest.mark.parametrize("organization_uid", [None, "0ABCdef1234"])
async def test_project_identity_organization_projection_keeps_transport_role_gate(monkeypatch, profile, allowed, organization_uid):
    from types import SimpleNamespace
    from fastmcp import Client
    from langboard.card_workspace.application.dtos import ProjectIdentityResponse
    from langboard.mcp_integration.Server import McpServer
    from langboard.mcp_integration.Tool import McpTool
    from langboard.middlewares.McpAuthMiddleware import mcp_auth_context

    calls = []
    def handler(project_uid: str) -> ProjectIdentityResponse:
        calls.append(project_uid)
        return ProjectIdentityResponse(
            uid=project_uid, title="Visible board", project_type="Other", organization_uid=organization_uid,
            url="https://host.example/board/p1", columns={"items": [], "total_count": 0, "next_cursor": None, "limit": 100},
        )

    name = "get_project_identity"
    metadata = {"handler": handler, "description": "Read board identity", "exclude": [], "accessible_type": "all"}
    monkeypatch.setattr(McpTool, "get_tools", lambda: {name: metadata})
    monkeypatch.setattr(McpTool, "get_tool", lambda key: metadata if key == name else None)
    monkeypatch.setattr(McpServer, "_validate_auth", lambda *args: True)
    monkeypatch.setattr(McpServer, "_validate_role", lambda *args, **kwargs: allowed)
    _, server = McpServer.get_http_app(profile)
    token = mcp_auth_context.set({"user_or_bot": object(), "tool_group": SimpleNamespace(activated_at=object(), tools=[name])})
    try:
        async with Client(server) as client:
            schema = (await client.list_tools())[0].output_schema
            assert ("organization_uid" in schema["properties"]) is (profile != "compatibility")
            result = await client.call_tool(name, {"project_uid": "p1"}, raise_on_error=False)
            assert result.is_error is (not allowed)
            if allowed:
                assert calls == ["p1"]
                if profile == "compatibility":
                    assert "organization_uid" not in result.structured_content
                else:
                    assert "organization_uid" in result.structured_content
                    assert result.structured_content["organization_uid"] == organization_uid
            else:
                assert calls == []
                assert "organization_uid" not in str(result.content)
                assert "0ABCdef1234" not in str(result.content)
    finally:
        mcp_auth_context.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("organization_uid", [None, "0ABCdef1234"])
async def test_project_identity_rest_uses_modern_projection_without_changing_tool_group(monkeypatch, organization_uid):
    import importlib
    from types import SimpleNamespace
    from fastapi import FastAPI, Request
    from fastmcp import FastMCP
    from httpx import ASGITransport, AsyncClient
    from langboard.card_workspace.application.dtos import ProjectIdentityResponse
    from langboard_shared.core.security import AuthSecurity

    route = importlib.import_module("langboard.routes.mcp.McpApi")
    name = "get_project_identity"
    group = SimpleNamespace(activated_at=True, tools=[name], user_id=None)
    closed = []
    monkeypatch.setattr(route, "DomainService", lambda: SimpleNamespace(
        mcp_tool_group=SimpleNamespace(get_by_id_like=lambda _: group), close=lambda: closed.append(True)))
    monkeypatch.setattr(route, "User", SimpleNamespace)
    monkeypatch.setattr(route.McpTool, "get_tool", lambda _: {"accessible_type": "user"})
    modern = FastMCP("project-organization-rest")
    legacy = FastMCP("frozen-identity")

    @modern.tool(name=name)
    def read(project_uid: str) -> ProjectIdentityResponse:
        return ProjectIdentityResponse(uid=project_uid, title="Board", project_type="Other", organization_uid=organization_uid,
            url="https://host.example/board/p1", columns={"items": [], "total_count": 0, "next_cursor": None, "limit": 100})

    @legacy.tool(name=name)
    def wrong_transport(project_uid: str) -> dict:
        raise AssertionError("REST identity must use the current native projection")

    monkeypatch.setattr(route.McpServer, "agent_mcp", modern)
    monkeypatch.setattr(route.McpServer, "mcp", legacy)
    app = FastAPI()

    @app.post("/identity")
    async def dispatch(request: Request):
        request.scope["auth"] = SimpleNamespace(id=1)
        return await route.execute_mcp_tool(name, request)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/identity", json={"project_uid": "p1"},
                                     headers={AuthSecurity.MCP_TOOL_GROUP_UID_HEADER: "group"})
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert "organization_uid" in result
    assert result["organization_uid"] == organization_uid
    assert group.tools == [name] and closed == [True]
    assert route.mcp_auth_context.get() is None
