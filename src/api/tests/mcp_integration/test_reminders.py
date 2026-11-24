"""Real FastMCP calls share reminder persistence and current native consent."""

from types import SimpleNamespace
from uuid import uuid4
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from langboard.Loader import ModuleLoader
from langboard.mcp_integration.Server import McpServer
from langboard.mcp_integration.Tool import McpTool
from langboard.middlewares.McpAuthMiddleware import mcp_auth_context
from langboard_shared.core.types import SnowflakeID
from langboard_shared.domain.models import Card, CardReminderRule, ProjectRole
from langboard_shared.domain.services.NativeMcpConsent import connect_native_client, save_native_board_consent
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session
from test_native_board_consent import native, native_engine  # noqa: F401


def uid(value):
    return SnowflakeID(value).to_short_code()


@pytest.fixture
def reminder_client(native, monkeypatch):  # noqa: F811
    engine, user, other, boards = native
    Card.metadata.create_all(engine)
    ModuleLoader.load("mcp_tools", "Mcp", log=False)
    registry = {name: McpTool.get_tool(name) for name in ("list_card_reminders", "change_card_reminder")}
    monkeypatch.setattr(McpTool, "get_tools", lambda: registry)
    monkeypatch.setattr(McpTool, "get_tool", lambda name: registry.get(name))
    connection = connect_native_client(user, "reminder-mcp-fixture")
    save_native_board_consent(user, connection.id, 10, ["read", "write"], None)
    auth = {
        "transport": "oauth",
        "user_or_bot": user,
        "native_connection_id": connection.id,
        "tool_group": SimpleNamespace(activated_at=object(), tools=list(registry)),
    }
    return engine, user, boards, connection, auth


async def test_registered_reminder_replay_cas_occurrence_and_current_consent(reminder_client):
    engine, user, boards, connection, auth = reminder_client
    arguments = {"project_uid": uid(10), "card_uid": uid(210)}
    schedule = {"timezone": "UTC", "anchor": "2090-01-05T09:00:00Z", "frequency": "daily"}
    change = {"action": "create", "request_uid": str(uuid4()), "schedule": schedule}
    token = mcp_auth_context.set(auth)
    try:
        _, server = McpServer.get_http_app("raw")
        async with Client(server) as client:
            result = await client.call_tool(
                "call_raw_tool", {"name": "change_card_reminder", "arguments": {**arguments, "change": change}}
            )
            first = result.structured_content
            assert first["replayed"] is False
            again = (
                await client.call_tool(
                    "call_raw_tool", {"name": "change_card_reminder", "arguments": {**arguments, "change": change}}
                )
            ).structured_content
            assert again == {**first, "replayed": True}
            with pytest.raises(ToolError):
                await client.call_tool(
                    "call_raw_tool",
                    {"name": "change_card_reminder", "arguments": {**arguments, "change": {**change, "shared": True}}},
                )
            item = first["item"]
            for invalid in (
                {**change, "shared": "false"},
                {
                    "action": "update",
                    "rule_uid": item["uid"],
                    "schedule": schedule,
                    "expected_revision": True,
                    "enabled": False,
                },
                {
                    "action": "change_occurrence",
                    "rule_uid": item["uid"],
                    "expected_revision": item["revision"],
                    "expected_due_at": "2090-01-05T09:00:00",
                },
            ):
                with pytest.raises(ToolError):
                    await client.call_tool(
                        "call_raw_tool", {"name": "change_card_reminder", "arguments": {**arguments, "change": invalid}}
                    )
            for limit in (True, 0, 51):
                with pytest.raises(ToolError):
                    await client.call_tool(
                        "call_raw_tool", {"name": "list_card_reminders", "arguments": {**arguments, "limit": limit}}
                    )
            skip = {
                "action": "change_occurrence",
                "rule_uid": item["uid"],
                "expected_revision": item["revision"],
                "expected_due_at": item["next_due_at"],
            }
            receipt = (
                await client.call_tool(
                    "call_raw_tool", {"name": "change_card_reminder", "arguments": {**arguments, "change": skip}}
                )
            ).structured_content
            assert receipt["item"]["status"] == "skipped"
            receipt_again = (
                await client.call_tool(
                    "call_raw_tool", {"name": "change_card_reminder", "arguments": {**arguments, "change": skip}}
                )
            ).structured_content
            assert receipt_again == receipt
            rules = (
                await client.call_tool("call_raw_tool", {"name": "list_card_reminders", "arguments": arguments})
            ).structured_content
            current = rules["items"][0]
            assert current["uid"] == item["uid"] and current["next_due_at"] != item["next_due_at"]
            move = {
                "action": "change_occurrence",
                "rule_uid": current["uid"],
                "expected_revision": current["revision"],
                "expected_due_at": current["next_due_at"],
                "moved_to": "2090-01-08T12:00:00Z",
            }
            moved = (
                await client.call_tool(
                    "call_raw_tool", {"name": "change_card_reminder", "arguments": {**arguments, "change": move}}
                )
            ).structured_content
            assert moved["item"]["status"] == "moved"
            assert moved["item"]["moved_to"] == "2090-01-08T12:00:00+00:00"
            current = (
                await client.call_tool("call_raw_tool", {"name": "list_card_reminders", "arguments": arguments})
            ).structured_content["items"][0]
            pause = {
                "action": "update",
                "rule_uid": current["uid"],
                "schedule": schedule,
                "expected_revision": current["revision"],
                "enabled": False,
            }
            paused = (
                await client.call_tool(
                    "call_raw_tool", {"name": "change_card_reminder", "arguments": {**arguments, "change": pause}}
                )
            ).structured_content["item"]
            assert paused["enabled"] is False and paused["next_due_at"] is None
            with pytest.raises(ToolError):
                await client.call_tool(
                    "call_raw_tool", {"name": "change_card_reminder", "arguments": {**arguments, "change": pause}}
                )
            with pytest.raises(ToolError):
                await client.call_tool(
                    "call_raw_tool",
                    {"name": "list_card_reminders", "arguments": {"project_uid": uid(20), "card_uid": uid(220)}},
                )
            save_native_board_consent(user, connection.id, 10, ["read"], 1)
            with pytest.raises(ToolError):
                await client.call_tool(
                    "call_raw_tool", {"name": "change_card_reminder", "arguments": {**arguments, "change": change}}
                )
            save_native_board_consent(user, connection.id, 10, [], 2)
            with pytest.raises(ToolError):
                await client.call_tool("call_raw_tool", {"name": "list_card_reminders", "arguments": arguments})
        with Session(engine) as db:
            assert db.scalar(select(func.count()).select_from(CardReminderRule)) == 1
    finally:
        mcp_auth_context.reset(token)


async def test_reminder_private_rule_isolation_and_shared_role(reminder_client):
    engine, user, boards, connection, auth = reminder_client
    arguments = {"project_uid": uid(10), "card_uid": uid(210)}
    schedule = {"timezone": "UTC", "anchor": "2090-01-05T09:00:00Z"}
    token = mcp_auth_context.set(auth)
    try:
        _, server = McpServer.get_http_app("raw")
        async with Client(server) as client:
            first = (
                await client.call_tool(
                    "call_raw_tool",
                    {
                        "name": "change_card_reminder",
                        "arguments": {
                            **arguments,
                            "change": {"action": "create", "request_uid": str(uuid4()), "schedule": schedule},
                        },
                    },
                )
            ).structured_content["item"]
            with Session(engine) as db:
                db.execute(update(CardReminderRule).values(owner_id=2))
                db.execute(
                    update(ProjectRole)
                    .where(ProjectRole.project_id == 10, ProjectRole.user_id == 1)
                    .values(actions=["read"])
                )
                db.commit()
            page = (
                await client.call_tool("call_raw_tool", {"name": "list_card_reminders", "arguments": arguments})
            ).structured_content
            assert page == {"items": [], "next_cursor": None}
            with pytest.raises(ToolError):
                await client.call_tool(
                    "call_raw_tool",
                    {
                        "name": "change_card_reminder",
                        "arguments": {
                            **arguments,
                            "change": {
                                "action": "update",
                                "rule_uid": first["uid"],
                                "expected_revision": first["revision"],
                                "schedule": schedule,
                                "enabled": False,
                            },
                        },
                    },
                )
            with pytest.raises(ToolError):
                await client.call_tool(
                    "call_raw_tool",
                    {
                        "name": "change_card_reminder",
                        "arguments": {
                            **arguments,
                            "change": {
                                "action": "create",
                                "request_uid": str(uuid4()),
                                "schedule": schedule,
                                "shared": True,
                            },
                        },
                    },
                )
            # The existing external native boundary requires current board write
            # consent and role even for a personal mutation. Do not bypass it.
            with pytest.raises(ToolError):
                await client.call_tool(
                    "call_raw_tool",
                    {
                        "name": "change_card_reminder",
                        "arguments": {
                            **arguments,
                            "change": {"action": "create", "request_uid": str(uuid4()), "schedule": schedule},
                        },
                    },
                )
    finally:
        mcp_auth_context.reset(token)
