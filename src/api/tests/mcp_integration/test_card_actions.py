"""Native facade validation and dispatch retain canonical authorization."""

from uuid import UUID
import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import AuthorizationError
from langboard.mcp_integration.Providers import create_agent_core_provider
from langboard.mcp_integration.Server import McpServer
from langboard.mcp_integration.Tool import McpTool
from langboard.mcp_tools.CardActionsMcp import CARD_ACTION_COMMANDS
from langboard.middlewares.McpAuthMiddleware import mcp_auth_context


@pytest.mark.parametrize(
    "change,command,expected",
    [
        ({"action": "assign", "assign_user_uids": []}, "set_card_people_and_labels", {"assign_user_uids": []}),
        ({"action": "label", "label_uids": ["label"]}, "set_card_people_and_labels", {"label_uids": ["label"]}),
        ({"action": "title", "title": " New title "}, "change_card_details", {"title": "New title"}),
        ({"action": "deadline", "deadline_at": ""}, "change_card_details", {"deadline_at": ""}),
        ({"action": "completion", "completed": True}, "set_card_completed", {"completed": True}),
        ({"action": "completion", "completed": False}, "set_card_completed", {"completed": False}),
        (
            {"action": "move", "column_uid": "column", "order": 0},
            "change_card_order_or_move_column",
            {"column_uid": "column", "order": 0},
        ),
        (
            {"action": "description_replace", "description": "    code\n", "expected_revision": "a" * 64},
            "replace_card_description",
            {"description": "    code\n", "expected_revision": "a" * 64},
        ),
        (
            {"action": "visibility", "visibility": "INTERNAL", "expected_visibility": "SHARED"},
            "change_card_visibility",
            {"visibility": "INTERNAL", "expected_visibility": "SHARED"},
        ),
        ({"action": "archive"}, "archive_card", {}),
        ({"action": "delete"}, "delete_card", {}),
        (
            {"action": "attachment_update", "attachment_uid": "file", "order": 0},
            "update_card_attachment",
            {"attachment_uid": "file", "order": 0},
        ),
        (
            {"action": "attachment_delete", "attachment_uid": "file"},
            "delete_card_attachment",
            {"attachment_uid": "file"},
        ),
        (
            {
                "action": "attachment_upload",
                "filename": "a.png",
                "file_data_base64": "AA==",
                "request_uid": "12345678-1234-5678-1234-567812345678",
            },
            "upload_card_attachment",
            {
                "filename": "a.png",
                "file_data_base64": "AA==",
                "request_uid": UUID("12345678-1234-5678-1234-567812345678"),
            },
        ),
    ],
)
async def test_native_card_action_catalog_and_dispatch_without_tool_group(monkeypatch, change, command, expected):
    calls = []
    original = McpTool.get_tool("update_card")

    def handler(**kwargs):
        calls.append(kwargs)
        return {"applied": True}

    metadata = {"handler": handler, "exclude": [], "accessible_type": "all"}
    monkeypatch.setattr(McpTool, "get_tools", lambda: {"update_card": original})
    monkeypatch.setattr(
        McpTool, "get_tool", lambda name: original if name == "update_card" else metadata if name == command else None
    )
    monkeypatch.setattr(McpServer, "_validate_auth", lambda actor, name: actor == "fixture-user")
    checked = []
    monkeypatch.setattr(McpServer, "_validate_role", lambda actor, fn, **kwargs: checked.append(fn) or True)
    server = FastMCP("Native card actions")
    server.add_provider(create_agent_core_provider(McpServer._wrap_tool))
    token = mcp_auth_context.set({"transport": "oauth", "user_or_bot": "fixture-user"})
    try:
        async with Client(server) as client:
            catalog = await client.list_tools()
            assert [tool.name for tool in catalog] == ["update_card"]
            assert len(catalog[0].input_schema["properties"]["change"]["oneOf"]) == len(CARD_ACTION_COMMANDS)
            result = await client.call_tool(
                "update_card", {"project_uid": "project", "card_uid": "card", "change": change}
            )
            assert result.structured_content == {"applied": True}
        assert calls == [{"project_uid": "project", "card_uid": "card", **expected}]
        assert checked[-1] is handler
    finally:
        mcp_auth_context.reset(token)


@pytest.mark.parametrize(
    "change",
    [
        {"action": "archive", "title": "accidental field"},
        {"action": "label", "label_uids": [""]},
        {"action": "move", "column_uid": "column", "order": True},
        {"action": "description_replace", "description": "body"},
        {"action": "attachment_update", "attachment_uid": "file"},
        {"action": "completion", "completed": "true"},
        {"action": "completion", "completed": 1},
        {"action": "attachment_upload", "filename": "a.txt", "file_data_base64": "YQ=="},
        {"action": "attachment_upload", "filename": "a.txt", "file_data_base64": "YQ==", "request_uid": None},
        {"action": "attachment_upload", "filename": "a.txt", "file_data_base64": "YQ==", "request_uid": "invalid"},
        {"action": "move", "column_uid": "column", "order": 0, "move_descendants": "true"},
    ],
)
async def test_invalid_actions_never_dispatch(change):
    from langboard.mcp_tools.CardActionsMcp import CardChange
    from pydantic import TypeAdapter, ValidationError

    with pytest.raises(ValidationError):
        TypeAdapter(CardChange).validate_python(change)


async def test_facade_does_not_bypass_command_role_denial(monkeypatch):
    from langboard.mcp_tools.CardActionsMcp import DeleteCard, update_card

    def forbidden(**kwargs):
        pytest.fail("Denied command must not run")

    monkeypatch.setattr(
        McpTool, "get_tool", lambda name: {"handler": forbidden, "exclude": [], "accessible_type": "all"}
    )
    monkeypatch.setattr(McpServer, "_validate_auth", lambda actor, name: True)
    monkeypatch.setattr(McpServer, "_validate_role", lambda *args, **kwargs: False)
    token = mcp_auth_context.set({"transport": "oauth", "user_or_bot": "fixture-user"})
    try:
        with pytest.raises(AuthorizationError, match="Insufficient permissions"):
            await update_card("project", "card", DeleteCard(action="delete"))
    finally:
        mcp_auth_context.reset(token)


async def test_delete_uses_native_delete_acl_and_never_runs_on_read_permission(monkeypatch):
    from langboard.mcp_integration.RoleFilter import McpRoleFilter
    from langboard.mcp_tools.CardActionsMcp import DeleteCard, update_card
    from langboard.mcp_tools.CardMcp import delete_card
    from langboard_shared.domain.models.ProjectRole import ProjectRoleAction

    metadata = McpTool.get_tool("delete_card")
    assert metadata["handler"] is delete_card
    checked = []

    def check_role(actor, handler, **arguments):
        required = McpRoleFilter.get_filtered(handler)[1]
        checked.append(required)
        return ProjectRoleAction.CardDelete.value not in required

    monkeypatch.setattr(McpServer, "_validate_auth", lambda actor, name: True)
    monkeypatch.setattr(McpServer, "_validate_role", check_role)
    token = mcp_auth_context.set({"transport": "oauth", "user_or_bot": "read-only-fixture"})
    try:
        with pytest.raises(AuthorizationError, match="Insufficient permissions"):
            await update_card("project", "card", DeleteCard(action="delete"))
        assert checked == [[ProjectRoleAction.CardDelete.value]]
    finally:
        mcp_auth_context.reset(token)


@pytest.fixture
def registered_card_commands(monkeypatch):
    """Keep real registry, handler, actor checks and DI; replace only domain I/O."""
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import Mock
    from langboard.mcp_integration import Dependencies
    from langboard.mcp_integration.Providers import create_native_tool
    from langboard_shared.domain.models import User

    actor = User(id=1, email="fixture@example.invalid", firstname="Test", lastname="User", password="test-only")
    service = SimpleNamespace(card_attachment=SimpleNamespace(upload=Mock()), card=SimpleNamespace(change_order=Mock()))
    checked = []
    closed = []

    def factory(_type):
        @asynccontextmanager
        async def resource():
            try:
                yield service
            finally:
                closed.append(True)

        return resource

    monkeypatch.setattr(Dependencies, "_factory_dependency", factory)
    monkeypatch.setattr(McpServer, "_validate_role", lambda actor, handler, **kwargs: checked.append(handler) or True)

    def server(*names, modern=True):
        result = FastMCP("Real registered card commands", strict_input_validation=True)
        for name in names:
            result.add_tool(
                create_native_tool(name, McpTool.get_tool(name), McpServer._wrap_tool, modern_annotations=modern)
            )
        return result

    token = mcp_auth_context.set({"transport": "oauth", "user_or_bot": actor})
    try:
        yield SimpleNamespace(actor=actor, service=service, checked=checked, closed=closed, server=server)
    finally:
        mcp_auth_context.reset(token)


async def test_registered_legacy_upload_omits_uuid_and_reaches_native_fallback(registered_card_commands):
    b = registered_card_commands
    b.service.card_attachment.upload.return_value = {"cancelled": True, "request_uid": "generated-by-native-service"}
    async with Client(b.server("upload_card_attachment", modern=False)) as client:
        schema = (await client.list_tools())[0].input_schema
        assert set(schema["properties"]) == {"project_uid", "card_uid", "filename", "file_data_base64"}
        result = await client.call_tool(
            "upload_card_attachment",
            dict(
                project_uid="project",
                card_uid="card",
                filename="a.txt",
                file_data_base64="YQ==",
            ),
        )
    assert result.structured_content == {"cancelled": True, "request_uid": "generated-by-native-service"}
    args, kwargs = b.service.card_attachment.upload.call_args
    assert args[:3] == (b.actor, "project", "card")
    assert args[3].filename == "a.txt"
    assert kwargs["request_uid"] is None
    assert kwargs["duplicate_action"] is None
    assert b.checked == [McpTool.get_tool("upload_card_attachment")["handler"]]
    assert len(b.closed) == 1


@pytest.mark.parametrize("action", [None, "add", "replace", "cancel"])
async def test_registered_facade_upload_reaches_native_handler_with_complete_decision(registered_card_commands, action):
    b = registered_card_commands
    request_uid = "12345678-1234-5678-1234-567812345678"
    receipt = {"cancelled": True, "request_uid": request_uid}
    b.service.card_attachment.upload.return_value = receipt
    change = dict(action="attachment_upload", filename="a.txt", file_data_base64="YQ==", request_uid=request_uid)
    if action is not None:
        change.update(duplicate_action=action, confirmation_token="current-token", replacement_uid="target")
    async with Client(b.server("update_card")) as client:
        for _ in range(2):
            result = await client.call_tool("update_card", dict(project_uid="project", card_uid="card", change=change))
            assert result.structured_content == receipt
    assert b.service.card_attachment.upload.call_count == 2
    args, kwargs = b.service.card_attachment.upload.call_args
    assert args[:3] == (b.actor, "project", "card")
    assert kwargs["request_uid"] == request_uid
    assert kwargs["duplicate_action"] == action
    assert kwargs["confirmation_token"] == ("current-token" if action is not None else None)
    assert kwargs["replacement_uid"] == ("target" if action is not None else None)
    assert b.checked == [McpTool.get_tool(name)["handler"] for name in ("update_card", "upload_card_attachment") * 2]


@pytest.mark.parametrize("invalid", [None, "invalid", 1])
async def test_registered_facade_upload_rejects_invalid_uuid_before_native_service(registered_card_commands, invalid):
    from fastmcp.exceptions import ToolError

    b = registered_card_commands
    change = dict(action="attachment_upload", filename="a.txt", file_data_base64="YQ==", request_uid=invalid)
    async with Client(b.server("update_card")) as client:
        with pytest.raises(ToolError):
            await client.call_tool("update_card", dict(project_uid="project", card_uid="card", change=change))
    b.service.card_attachment.upload.assert_not_called()


@pytest.mark.parametrize("move_descendants", [True, False])
async def test_registered_facade_move_preserves_preview_and_scope_confirmation(
    registered_card_commands, move_descendants
):
    b = registered_card_commands
    b.service.card.change_order.side_effect = [
        {"confirmation_required": True, "confirmation_revision": "current-tree"},
        True,
    ]
    change = dict(action="move", column_uid="column", order=0)
    async with Client(b.server("update_card")) as client:
        preview = (
            await client.call_tool("update_card", dict(project_uid="project", card_uid="card", change=change))
        ).structured_content
        assert preview["confirmation_required"] is True
        assert b.service.card.change_order.call_args.kwargs["move_descendants"] is None
        change.update(move_descendants=move_descendants, confirmation_revision=preview["confirmation_revision"])
        confirmed = await client.call_tool("update_card", dict(project_uid="project", card_uid="card", change=change))
    assert confirmed.structured_content == {"message": "Order changed or card moved"}
    args, kwargs = b.service.card.change_order.call_args
    assert args == (b.actor, "project", "card", 0, "column")
    assert kwargs["move_descendants"] is move_descendants
    assert kwargs["confirmation_revision"] == "current-tree"


async def test_registered_facade_keeps_upload_user_only(registered_card_commands):
    from fastmcp.exceptions import ToolError
    from langboard_shared.domain.models import Bot

    b = registered_card_commands
    token = mcp_auth_context.set(
        {
            "transport": "oauth",
            "user_or_bot": Bot(
                id=1,
                name="Fixture",
                bot_uname="fixture",
                app_api_token="test",
                platform="default",
                platform_running_type="default",
            ),
        }
    )
    try:
        async with Client(b.server("update_card")) as client:
            with pytest.raises(ToolError, match="Authentication required"):
                await client.call_tool(
                    "update_card",
                    dict(
                        project_uid="project",
                        card_uid="card",
                        change=dict(
                            action="attachment_upload",
                            filename="a.txt",
                            file_data_base64="YQ==",
                            request_uid="12345678-1234-5678-1234-567812345678",
                        ),
                    ),
                )
        b.service.card_attachment.upload.assert_not_called()
    finally:
        mcp_auth_context.reset(token)


async def test_registered_legacy_plain_comment_preserves_old_call_shape(registered_card_commands):
    from types import SimpleNamespace
    from unittest.mock import Mock

    b = registered_card_commands
    b.service.card_comment = SimpleNamespace(
        create=Mock(
            return_value=SimpleNamespace(
                api_response=lambda: {"uid": "comment", "content": "hello"},
            )
        )
    )
    payload = dict(project_uid="project", card_uid="card", content=" hello ")
    async with Client(b.server("add_card_comment", modern=False)) as client:
        assert set((await client.list_tools())[0].input_schema["properties"]) == set(payload)
        result = await client.call_tool("add_card_comment", payload)
    assert result.structured_content["comment"]["uid"] == "comment"
    args, kwargs = b.service.card_comment.create.call_args
    assert args[:3] == (b.actor, "project", "card")
    assert args[3].content == "hello"
    assert kwargs["anchor"] is None and kwargs["request_uid"] is None


async def test_registered_legacy_reorder_keeps_native_default_scope(registered_card_commands):
    b = registered_card_commands
    b.service.card.change_order.return_value = True
    async with Client(b.server("change_card_order_or_move_column", modern=False)) as client:
        schema = (await client.list_tools())[0].input_schema
        assert "move_descendants" not in schema["properties"]
        assert "confirmation_revision" not in schema["properties"]
        result = await client.call_tool(
            "change_card_order_or_move_column",
            dict(
                project_uid="project",
                card_uid="card",
                order=0,
            ),
        )
    assert result.structured_content == {"message": "Order changed or card moved"}
    args, kwargs = b.service.card.change_order.call_args
    assert args == (b.actor, "project", "card", 0, "")
    assert kwargs["move_descendants"] is None and kwargs["confirmation_revision"] is None


@pytest.mark.parametrize("value", ["Confirm deployment", ""])
async def test_next_action_dispatch_uses_existing_card_update_command(monkeypatch, value):
    from langboard.mcp_tools.CardActionsMcp import NextActionCard, update_card

    calls = []

    async def handler(**kwargs):
        calls.append(kwargs)
        return {"next_action": value or None}

    monkeypatch.setattr(McpServer, "_wrap_tool", lambda *args: handler)
    assert await update_card("board", "card", NextActionCard(action="next_action", next_action=value)) == {
        "next_action": value or None
    }
    assert CARD_ACTION_COMMANDS["next_action"] == "change_card_details"
    assert calls == [{"project_uid": "board", "card_uid": "card", "next_action": value}]
