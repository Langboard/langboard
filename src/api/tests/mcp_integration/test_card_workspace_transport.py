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
            result = await client.call_tool(name, {"project_uid": "p1"}, raise_on_error=False)
            assert result.is_error is (not allowed)
            if allowed:
                assert calls == ["p1"]
                assert "organization_uid" in result.structured_content
                assert result.structured_content["organization_uid"] == organization_uid
            else:
                assert calls == []
                assert "organization_uid" not in str(result.content)
                assert "0ABCdef1234" not in str(result.content)
    finally:
        mcp_auth_context.reset(token)
