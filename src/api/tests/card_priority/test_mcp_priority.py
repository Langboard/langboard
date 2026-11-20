"""Registered typed priority dispatch retains native board consent and command ACL."""

import json
import pytest
from fastmcp import Client, FastMCP
from langboard.mcp_integration.Providers import create_agent_core_provider
from langboard.mcp_integration.RoleFilter import McpRoleFilter
from langboard.mcp_integration.Server import McpServer
from langboard.mcp_integration.Tool import McpTool
from langboard.mcp_tools import CardActionsMcp as CardActionsMcp
from langboard.mcp_tools import CardMcp
from langboard.middlewares.McpAuthMiddleware import mcp_auth_context
from langboard_shared.domain.models.ProjectRole import ProjectRoleAction
from langboard_shared.domain.services.NativeMcpConsent import connect_native_client, save_native_board_consent
from src.api.tests.mcp_integration.test_native_board_consent import native as native
from src.api.tests.mcp_integration.test_native_board_consent import native_engine as native_engine


async def test_priority_facade_registered_dispatch_and_consent(native, monkeypatch):  # noqa: F811 - imported pytest fixture
    _, user, _, boards = native
    assert McpRoleFilter.get_filtered(CardMcp.change_card_details)[1] == [ProjectRoleAction.CardUpdate.value]
    connection = connect_native_client(user, "priority-client")
    save_native_board_consent(user, connection.id, 10, ["read", "write"], None)
    original = McpTool.get_tool("update_card")
    calls = []

    def handler(project_uid: str, card_uid: str, priority: str):
        calls.append((project_uid, card_uid, priority))
        return {"priority": priority}

    metadata = {"handler": handler, "exclude": [], "accessible_type": "all"}
    monkeypatch.setattr(McpTool, "get_tools", lambda: {"update_card": original})
    monkeypatch.setattr(McpTool, "get_tool", lambda name: original if name == "update_card" else metadata if name == "change_card_details" else None)
    server = FastMCP("Priority")
    server.add_provider(create_agent_core_provider(McpServer._wrap_tool))
    token = mcp_auth_context.set({"transport": "oauth", "user_or_bot": user, "native_connection_id": connection.id})
    try:
        async with Client(server) as client:
            catalog = await client.list_tools()
            assert '"priority"' in json.dumps(catalog[0].input_schema)
            for value in ["P0", "P1", "P2", "P3"]:
                result = await client.call_tool("update_card", {"project_uid": boards[0].get_uid(), "card_uid": "card", "change": {"action": "priority", "priority": value}})
                assert result.structured_content == {"priority": value}
            for value in [None, "P4"]:
                with pytest.raises(Exception):
                    await client.call_tool("update_card", {"project_uid": boards[0].get_uid(), "card_uid": "card", "change": {"action": "priority", "priority": value}})
            with pytest.raises(Exception, match="consent"):
                await client.call_tool("update_card", {"project_uid": boards[1].get_uid(), "card_uid": "card", "change": {"action": "priority", "priority": "P0"}})
        assert [value for _, _, value in calls] == ["P0", "P1", "P2", "P3"]
    finally:
        mcp_auth_context.reset(token)
