"""Registered next-action facade with current native actor and board consent."""

import json
from functools import wraps
import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from langboard.mcp_integration.Providers import create_agent_core_provider
from langboard.mcp_integration.RoleFilter import McpRoleFilter
from langboard.mcp_integration.Server import McpServer
from langboard.mcp_integration.Tool import McpTool
from langboard.mcp_tools import CardActionsMcp  # noqa: F401 - register native action
from langboard.middlewares.McpAuthMiddleware import mcp_auth_context
from langboard_shared.core.types import SnowflakeID
from langboard_shared.domain.services.factory.CardService import CardService
from langboard_shared.domain.services.NativeMcpConsent import connect_native_client, save_native_board_consent
from src.api.tests.mcp_integration.test_native_board_consent import native as native
from src.api.tests.mcp_integration.test_native_board_consent import native_engine as native_engine


@pytest.mark.parametrize("value", ["Confirm deployment", ""])
async def test_registered_facade_next_action_uses_existing_command_and_consent(native, monkeypatch, value):  # noqa: F811
    _, user, _, boards = native
    connection = connect_native_client(user, "next-action-client")
    save_native_board_consent(user, connection.id, 10, ["read", "write"], None)
    original = McpTool.get_tool("update_card")
    calls = []

    primitive = McpTool.get_tool("change_card_details")

    primitive_calls = []

    @wraps(primitive["handler"])
    def handler(*args, **kwargs):
        primitive_calls.append((args, kwargs))
        raise AssertionError("Primitive business dispatch")

    # Retain the real primitive role metadata while trapping business dispatch.
    metadata = {**primitive, "handler": handler}
    monkeypatch.setitem(McpRoleFilter._filtered, handler, McpRoleFilter.get_filtered(primitive["handler"]))
    card_uid = SnowflakeID(210).to_short_code()

    def native_update(service, actor, project_uid, target_card_uid, fields):
        assert actor is user
        assert isinstance(service, CardService)
        calls.append((project_uid, target_card_uid, fields))
        return fields

    monkeypatch.setattr(CardService, "update", native_update)
    monkeypatch.setattr(McpTool, "get_tools", lambda: {"update_card": original})
    monkeypatch.setattr(
        McpTool,
        "get_tool",
        lambda name: original if name == "update_card" else metadata if name == "change_card_details" else None,
    )
    server = FastMCP("Next action")
    server.add_provider(create_agent_core_provider(McpServer._wrap_tool))
    token = mcp_auth_context.set({"transport": "oauth", "user_or_bot": user, "native_connection_id": connection.id})
    try:
        async with Client(server) as client:
            catalog = await client.list_tools()
            assert "next_action" in json.dumps(catalog[0].input_schema)
            result = await client.call_tool(
                "update_card",
                {
                    "project_uid": boards[0].get_uid(),
                    "card_uid": card_uid,
                    "change": {"action": "next_action", "next_action": value},
                },
            )
            assert result.structured_content == {"next_action": value or None}
            with pytest.raises(ToolError, match="consent"):
                await client.call_tool(
                    "update_card",
                    {
                        "project_uid": boards[1].get_uid(),
                        "card_uid": card_uid,
                        "change": {"action": "next_action", "next_action": "denied"},
                    },
                )
        assert primitive_calls == []
        assert calls == [(boards[0].get_uid(), card_uid, {"next_action": value or None})]
    finally:
        mcp_auth_context.reset(token)
