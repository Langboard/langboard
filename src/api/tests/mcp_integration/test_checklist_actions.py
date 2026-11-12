"""Native checklist schema dispatch cannot bypass command authorization."""

from types import SimpleNamespace
import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import AuthorizationError, ToolError
from langboard.mcp_integration.Providers import create_agent_core_provider
from langboard.mcp_integration.RoleFilter import McpRoleFilter
from langboard.mcp_integration.Server import McpServer
from langboard.mcp_integration.Tool import McpTool
from langboard.mcp_tools.ChecklistActionsMcp import (
    CHECKLIST_ACTION_COMMANDS,
    PromoteItem,
    change_card_checklist,
)
from langboard.middlewares.McpAuthMiddleware import mcp_auth_context
from langboard_shared.domain.models import Bot
from langboard_shared.domain.models.ProjectRole import ProjectRoleAction


@pytest.fixture
def bot_actor():
    # Dispatch/role unit tests use the native bot transport. External OAuth
    # consent is exercised with persisted users in test_native_board_consent.
    return Bot(
        id=90, name="Scoped bot", bot_uname="scoped", platform="default",
        platform_running_type="default", app_api_token="fixture-token",
    )


@pytest.mark.parametrize(
    "change,expected",
    [
        ({"action": "create_list", "title": " Tasks "}, {"title": "Tasks"}),
        ({"action": "add_item", "checklist_uid": "list", "title": "Task"}, {"checklist_uid": "list", "title": "Task"}),
        (
            {"action": "update_list", "checklist_uid": "list", "is_checked": False},
            {"checklist_uid": "list", "is_checked": False},
        ),
        ({"action": "delete_list", "checklist_uid": "list"}, {"checklist_uid": "list"}),
        (
            {"action": "set_completed", "checkitem_uid": "item", "is_checked": False},
            {"checkitem_uid": "item", "is_checked": False},
        ),
        (
            {"action": "update_item", "checkitem_uid": "item", "deadline_at": ""},
            {"checkitem_uid": "item", "deadline_at": ""},
        ),
        ({"action": "delete_item", "checkitem_uid": "item"}, {"checkitem_uid": "item"}),
        (
            {"action": "promote", "checkitem_uid": "item", "project_column_uid": "column"},
            {"checkitem_uid": "item", "project_column_uid": "column"},
        ),
    ],
)
async def test_native_checklist_catalog_and_dispatch_without_tool_group(monkeypatch, bot_actor, change, expected):
    calls = []
    original = McpTool.get_tool("change_card_checklist")
    command = CHECKLIST_ACTION_COMMANDS[change["action"]]

    checklist = {"uid": "list", "checkitems": [], "checkitems_total_count": 0, "checkitems_next_cursor": None}
    payload = {
        "create_list": {"checklist": checklist},
        "add_item": {"checkitem": {"uid": "item"}},
        "update_list": {"checklists": {"items": [checklist], "total_count": 1, "next_cursor": None, "limit": 25}},
        "set_completed": {"checklists": {"items": [checklist], "total_count": 1, "next_cursor": None, "limit": 25}},
        "update_item": {"checklists": {"items": [checklist], "total_count": 1, "next_cursor": None, "limit": 25}},
        "delete_list": {"deleted": True},
        "delete_item": {"deleted": True},
        "promote": {"card": {"uid": "new-card"}, "source_checkitem_uid": "item"},
    }[change["action"]]

    def handler(**kwargs):
        calls.append(kwargs)
        return payload

    from langboard.mcp_integration import Dependencies
    from langboard.mcp_tools import CardMcp

    lifecycle = []
    service = SimpleNamespace(close=lambda: lifecycle.append("closed"))
    factory = Dependencies._factory_dependency
    monkeypatch.setattr(Dependencies, "_factory_dependency", lambda _: factory(lambda: lifecycle.append("opened") or service))
    monkeypatch.setattr(CardMcp, command, handler)
    monkeypatch.setattr(McpTool, "get_tools", lambda: {"change_card_checklist": original})
    monkeypatch.setattr(
        McpTool,
        "get_tool",
        lambda name: original if name == "change_card_checklist" else None,
    )
    monkeypatch.setattr(McpServer, "_validate_auth", lambda actor, name: actor is bot_actor)
    checked = []
    monkeypatch.setattr(McpServer, "_validate_role", lambda actor, fn, **kwargs: checked.append(fn) or True)
    server = FastMCP("Native checklist actions")
    server.add_provider(create_agent_core_provider(McpServer._wrap_tool))
    token = mcp_auth_context.set({"transport": "api_key", "user_or_bot": bot_actor})
    try:
        async with Client(server) as client:
            catalog = await client.list_tools()
            assert [tool.name for tool in catalog] == ["change_card_checklist"]
            assert len(catalog[0].input_schema["properties"]["change"]["oneOf"]) == 8
            result = await client.call_tool(
                "change_card_checklist", {"project_uid": "project", "card_uid": "card", "change": change}
            )
            assert result.structured_content == payload
            for invalid in (
                {"action": "delete_item", "checkitem_uid": "item", "title": "unrelated"},
                {"action": "set_completed", "checkitem_uid": "item", "is_checked": "false"},
                {"action": "update_list", "checklist_uid": "list"},
                {"action": "update_item", "checkitem_uid": "item", "title": None},
                {"action": "add_item", "checklist_uid": "list", "title": " "},
                {"action": "promote", "checkitem_uid": "item", "project_column_uid": " "},
            ):
                with pytest.raises(ToolError):
                    await client.call_tool(
                        "change_card_checklist", {"project_uid": "project", "card_uid": "card", "change": invalid}
                    )
        assert calls == [{"project_uid": "project", "card_uid": "card", "user_or_bot": bot_actor, "service": service, **expected}]
        assert checked == [change_card_checklist]
        assert lifecycle == ["opened", "closed"]
    finally:
        mcp_auth_context.reset(token)


async def test_promotion_retains_canonical_update_permission(monkeypatch, bot_actor):
    from langboard.mcp_integration import Dependencies
    from langboard.mcp_tools.CardMcp import cardify_card_checkitem

    metadata = McpTool.get_tool("cardify_card_checkitem")
    assert metadata["handler"] is cardify_card_checkitem
    checked = []

    def check_role(actor, handler, **arguments):
        checked.append(handler)
        actions = McpRoleFilter.get_filtered(handler)[1]
        assert actions == [ProjectRoleAction.CardUpdate.value]
        return False

    monkeypatch.setattr(McpServer, "_validate_auth", lambda *args: True)
    monkeypatch.setattr(McpServer, "_validate_role", check_role)
    native_factory = Dependencies._factory_dependency
    monkeypatch.setattr(Dependencies, "_factory_dependency", lambda _: native_factory(
        lambda: pytest.fail("Denied command must not create services")
    ))
    token = mcp_auth_context.set({"transport": "api_key", "user_or_bot": bot_actor})
    try:
        with pytest.raises(AuthorizationError, match="Insufficient permissions"):
            await McpServer._wrap_tool("change_card_checklist", change_card_checklist)(
                project_uid="project", card_uid="card",
                change=PromoteItem(action="promote", checkitem_uid="item", project_column_uid="column"),
            )
        assert checked == [change_card_checklist]
    finally:
        mcp_auth_context.reset(token)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"deleted": False},
        {"deleted": 1},
        {"deleted": True, "checkitem": {"uid": "item"}},
        {"card": {"uid": "card"}},
        {"source_checkitem_uid": "item", "deleted": True},
    ],
)
def test_checklist_action_output_rejects_missing_mixed_or_incomplete_result(payload):
    from pydantic import ValidationError
    from langboard.mcp_integration.CreationOutputs import ChecklistActionOutput

    with pytest.raises(ValidationError):
        ChecklistActionOutput.model_validate(payload)
