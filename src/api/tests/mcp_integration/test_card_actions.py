"""Native facade validation and dispatch retain canonical authorization."""

from types import SimpleNamespace
from uuid import UUID
import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import AuthorizationError
from langboard.mcp_integration.Server import McpServer
from langboard.mcp_integration.Tool import McpTool
from langboard.mcp_tools.CardActionsMcp import CARD_ACTION_COMMANDS
from langboard.mcp_tools.RoleChecker import McpRoleChecker
from langboard.middlewares.McpAuthMiddleware import mcp_auth_context
from langboard_shared.domain.services.NativeMcpConsent import connect_native_client, save_native_board_consent
from src.api.tests.mcp_integration.test_native_board_consent import native as native
from src.api.tests.mcp_integration.test_native_board_consent import native_engine as native_engine


@pytest.fixture
def native_card_context(native):  # noqa: F811 - imported pytest fixture
    """Use the primary actor, current board and explicitly saved native grant."""
    from types import SimpleNamespace

    _, actor, _, boards = native
    connection = connect_native_client(actor, "card-action-fixture")
    save_native_board_consent(actor, connection.id, boards[0].id, ["read", "write"], None)
    token = mcp_auth_context.set({"transport": "oauth", "user_or_bot": actor, "native_connection_id": connection.id})
    try:
        yield SimpleNamespace(actor=actor, connection=connection, project=boards[0], project_uid=boards[0].get_uid())
    finally:
        mcp_auth_context.reset(token)


@pytest.mark.parametrize(
    "change,command,expected",
    [
        ({"action": "assign", "assign_user_uids": []}, "set_card_people_and_labels", {"assign_user_uids": []}),
        ({"action": "label", "label_uids": ["label"]}, "set_card_people_and_labels", {"label_uids": ["label"]}),
        ({"action": "title", "title": " New title "}, "change_card_details", {"title": "New title"}),
        ({"action": "deadline", "deadline_at": ""}, "change_card_details", {"deadline_at": ""}),
        ({"action": "priority", "priority": "P1"}, "change_card_details", {"priority": "P1"}),
        ({"action": "next_action", "next_action": "Next"}, "change_card_details", {"next_action": "Next"}),
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
async def test_native_card_action_catalog_and_dispatch_without_tool_group(
    monkeypatch, registered_card_commands, change, command, expected
):
    from unittest.mock import Mock
    from langboard.mcp_tools import CardActionsMcp

    b = registered_card_commands
    project, card = SimpleNamespace(id=1), SimpleNamespace(id=2, is_linked_resource=False)
    b.service.card = Mock()
    b.service.card.resolve_readable_card.return_value = (project, card, None)
    b.service.card.replace_people_and_labels.return_value = {"member_uids": []}
    b.service.card.update.return_value = True
    b.service.card.set_card_completed.return_value = True
    b.service.card.change_order.return_value = True
    b.service.card.review_visibility_change.return_value = {"visibility": "INTERNAL"}
    b.service.card.archive.return_value = True
    b.service.card.delete.return_value = True
    b.service.card_attachment = Mock()
    b.service.card_attachment.get_by_id_like.return_value = SimpleNamespace(card_id=card.id)
    b.service.card_attachment.get_api_list_by_card.return_value = []
    b.service.card_attachment.upload.return_value = {"uid": "attachment", "internal": "hidden"}
    monkeypatch.setattr(CardActionsMcp, "_get_card_in_project", lambda *args: (project, card))
    description = Mock(return_value={"changed": True, "description_revision": "b" * 64, "description_chars": 9})
    monkeypatch.setattr(CardActionsMcp, "replace_card_description", description)
    original_get = McpTool.get_tool
    primitive = original_get(command)
    forbidden = Mock(side_effect=AssertionError("A primitive MCP business handler must never run"))
    monkeypatch.setattr(
        McpTool, "get_tool", lambda name: {**primitive, "handler": forbidden} if name == command else original_get(name)
    )
    async with Client(b.server("update_card")) as client:
        catalog = await client.list_tools()
        assert [tool.name for tool in catalog] == ["update_card"]
        assert len(catalog[0].input_schema["properties"]["change"]["oneOf"]) == len(CARD_ACTION_COMMANDS)
        result = await client.call_tool(
            "update_card", {"project_uid": b.project_uid, "card_uid": "card", "change": change}
        )
    forbidden.assert_not_called()
    assert b.closed == [True]
    assert b.checked[-1] is forbidden  # Existing primitive policy is still checked.
    action = change["action"]
    if action in {"assign", "label"}:
        b.service.card.replace_people_and_labels.assert_called_once_with(b.actor, project, card, **expected)
        assert result.structured_content == {"member_uids": []}
    elif action in {"title", "deadline", "priority", "next_action"}:
        assert b.service.card.update.call_args.args[:3] == (b.actor, b.project_uid, "card")
        assert result.structured_content == expected
    elif action == "description_replace":
        assert description.call_args.args[1:] == (
            b.project_uid,
            "card",
            change["description"],
            change["expected_revision"],
        )
        assert result.structured_content["changed"] is True
    elif action == "attachment_upload":
        assert b.service.card_attachment.upload.call_args.args[:3] == (b.actor, b.project_uid, "card")
        assert b.service.card_attachment.upload.call_args.kwargs["request_uid"] == str(expected["request_uid"])
        assert result.structured_content == {"uid": "attachment"}
    elif action == "visibility":
        b.service.card.review_visibility_change.assert_called_once_with(
            b.actor, b.project_uid, "card", "INTERNAL", "SHARED", False, None
        )
        assert result.structured_content == {"visibility": "INTERNAL"}
    elif action == "completion":
        b.service.card.set_card_completed.assert_called_once_with(b.actor, b.project_uid, "card", change["completed"])
        assert result.structured_content == {"completed": change["completed"]}
    elif action == "move":
        assert b.service.card.change_order.call_args.args == (b.actor, b.project_uid, "card", 0, "column")
        assert result.structured_content == {"message": "Order changed or card moved"}
    elif action in {"archive", "delete"}:
        getattr(b.service.card, action).assert_called_once()
        assert result.structured_content == {"message": "Archived" if action == "archive" else "Deleted"}
    elif action == "attachment_delete":
        b.service.card_attachment.delete.assert_called_once()
        assert result.structured_content == {"deleted": True}
    else:
        b.service.card_attachment.change_order.assert_called_once()
        assert result.structured_content["attachments"]["items"] == []


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


async def test_facade_does_not_bypass_command_role_denial(monkeypatch, native_card_context):
    project_uid = native_card_context.project_uid
    from langboard.mcp_tools.CardActionsMcp import DeleteCard, update_card

    def forbidden(**kwargs):
        pytest.fail("Denied command must not run")

    monkeypatch.setattr(
        McpTool, "get_tool", lambda name: {"handler": forbidden, "exclude": [], "accessible_type": "all"}
    )
    monkeypatch.setattr(McpRoleChecker, "check_permission", lambda *args, **kwargs: False)
    token = mcp_auth_context.set(mcp_auth_context.get().copy())
    try:
        with pytest.raises(AuthorizationError, match="Insufficient permissions"):
            update_card(project_uid, "card", DeleteCard(action="delete"), native_card_context.actor, SimpleNamespace())
    finally:
        mcp_auth_context.reset(token)


async def test_delete_uses_native_delete_acl_and_never_runs_on_read_permission(monkeypatch, native_card_context):
    project_uid = native_card_context.project_uid
    from langboard.mcp_integration.RoleFilter import McpRoleFilter
    from langboard.mcp_tools.CardActionsMcp import DeleteCard, update_card
    from langboard.mcp_tools.CardMcp import delete_card
    from langboard_shared.domain.models.ProjectRole import ProjectRoleAction

    metadata = McpTool.get_tool("delete_card")
    assert metadata["handler"] is delete_card
    checked = []

    def check_role(self, handler, actor, arguments):
        required = McpRoleFilter.get_filtered(handler)[1]
        checked.append(required)
        return ProjectRoleAction.CardDelete.value not in required

    monkeypatch.setattr(McpRoleChecker, "check_permission", check_role)
    token = mcp_auth_context.set(mcp_auth_context.get().copy())
    try:
        with pytest.raises(AuthorizationError, match="Insufficient permissions"):
            update_card(project_uid, "card", DeleteCard(action="delete"), native_card_context.actor, SimpleNamespace())
        assert checked == [[ProjectRoleAction.CardDelete.value]]
    finally:
        mcp_auth_context.reset(token)


@pytest.fixture
def registered_card_commands(monkeypatch, native_card_context):
    """Keep real registry, handler, actor checks and DI; replace only domain I/O."""
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import Mock
    from langboard.mcp_integration import Dependencies
    from langboard.mcp_integration.Providers import create_native_tool
    from langboard.mcp_tools import CommentActionsMcp  # noqa: F401 - register canonical comment command

    actor = native_card_context.actor
    service = SimpleNamespace(card_attachment=SimpleNamespace(upload=Mock()), card=SimpleNamespace(change_order=Mock()))
    from types import MethodType
    from langboard_shared.domain.services.factory.CardService import CardService

    service.card.review_visibility_change = MethodType(CardService.review_visibility_change, service.card)
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
    monkeypatch.setattr(
        McpRoleChecker, "check_permission", lambda self, handler, actor, kwargs: checked.append(handler) or True
    )

    def server(*names, modern=True):
        result = FastMCP("Real registered card commands", strict_input_validation=True)
        for name in names:
            result.add_tool(
                create_native_tool(name, McpTool.get_tool(name), McpServer._wrap_tool, modern_annotations=modern)
            )
        return result

    token = mcp_auth_context.set(mcp_auth_context.get().copy())
    try:
        yield SimpleNamespace(
            actor=actor,
            service=service,
            checked=checked,
            closed=closed,
            server=server,
            project_uid=native_card_context.project_uid,
            connection=native_card_context.connection,
            project=native_card_context.project,
        )
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
                project_uid=b.project_uid,
                card_uid="card",
                filename="a.txt",
                file_data_base64="YQ==",
            ),
        )
    assert result.structured_content == {"cancelled": True, "request_uid": "generated-by-native-service"}
    args, kwargs = b.service.card_attachment.upload.call_args
    assert args[:3] == (b.actor, b.project_uid, "card")
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
            result = await client.call_tool(
                "update_card", dict(project_uid=b.project_uid, card_uid="card", change=change)
            )
            assert result.structured_content == receipt
    assert b.service.card_attachment.upload.call_count == 2
    args, kwargs = b.service.card_attachment.upload.call_args
    assert args[:3] == (b.actor, b.project_uid, "card")
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
            await client.call_tool("update_card", dict(project_uid=b.project_uid, card_uid="card", change=change))
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
            await client.call_tool("update_card", dict(project_uid=b.project_uid, card_uid="card", change=change))
        ).structured_content
        assert preview["confirmation_required"] is True
        assert b.service.card.change_order.call_args.kwargs["move_descendants"] is None
        change.update(move_descendants=move_descendants, confirmation_revision=preview["confirmation_revision"])
        confirmed = await client.call_tool(
            "update_card", dict(project_uid=b.project_uid, card_uid="card", change=change)
        )
    assert confirmed.structured_content == {"message": "Order changed or card moved"}
    args, kwargs = b.service.card.change_order.call_args
    assert args == (b.actor, b.project_uid, "card", 0, "column")
    assert kwargs["move_descendants"] is move_descendants
    assert kwargs["confirmation_revision"] == "current-tree"


async def test_registered_facade_keeps_upload_user_only(registered_card_commands):
    from fastmcp.exceptions import ToolError
    from langboard_shared.domain.models import Bot

    b = registered_card_commands
    token = mcp_auth_context.set(
        {
            "transport": "api_key",
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
                        project_uid=b.project_uid,
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


async def test_registered_canonical_plain_comment_preserves_native_owner(registered_card_commands):
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
    payload = dict(project_uid=b.project_uid, card_uid="card", change={"action": "add", "content": " hello "})
    async with Client(b.server("change_card_comment", modern=False)) as client:
        assert set((await client.list_tools())[0].input_schema["properties"]) == set(payload)
        result = await client.call_tool("change_card_comment", payload)
    assert result.structured_content["comment"]["uid"] == "comment"
    args, kwargs = b.service.card_comment.create.call_args
    assert args[:3] == (b.actor, b.project_uid, "card")
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
                project_uid=b.project_uid,
                card_uid="card",
                order=0,
            ),
        )
    assert result.structured_content == {"message": "Order changed or card moved"}
    args, kwargs = b.service.card.change_order.call_args
    assert args == (b.actor, b.project_uid, "card", 0, "")
    assert kwargs["move_descendants"] is None and kwargs["confirmation_revision"] is None


async def test_registered_move_reports_confirmed_rejection(registered_card_commands):
    from fastmcp.exceptions import ToolError

    b = registered_card_commands
    b.service.card.change_order.return_value = None
    async with Client(b.server("update_card")) as client:
        with pytest.raises(ToolError, match="CARD_MOVE_PRECONDITION_FAILED: No move was performed"):
            await client.call_tool(
                "update_card",
                {
                    "project_uid": b.project_uid,
                    "card_uid": "card",
                    "change": {"action": "move", "column_uid": "unavailable", "order": 0},
                },
            )
    b.service.card.change_order.assert_called_once()


@pytest.mark.parametrize("value", ["Confirm deployment", ""])
async def test_next_action_dispatch_uses_existing_card_update_command(monkeypatch, native_card_context, value):
    from unittest.mock import Mock
    from langboard.mcp_tools.CardActionsMcp import NextActionCard, update_card

    owner = Mock(return_value=True)
    service = SimpleNamespace(
        card=SimpleNamespace(
            update=owner, resolve_readable_card=lambda *args: (None, SimpleNamespace(is_linked_resource=False), None)
        )
    )
    monkeypatch.setattr(McpRoleChecker, "check_permission", lambda *args: True)
    assert update_card(
        "board", "card", NextActionCard(action="next_action", next_action=value), native_card_context.actor, service
    ) == {"next_action": value or None}
    owner.assert_called_once_with(native_card_context.actor, "board", "card", {"next_action": value or None})
    assert CARD_ACTION_COMMANDS["next_action"] == "change_card_details"


@pytest.mark.parametrize("consent", ["missing", "read-only", "revoked"])
async def test_registered_facade_denies_missing_or_revoked_write_consent(registered_card_commands, consent):
    from fastmcp.exceptions import ToolError
    from langboard_shared.core.types import SnowflakeID

    b = registered_card_commands
    project_uid = b.project_uid
    if consent == "missing":
        # The second current board has membership and role, but no saved grant.
        project_uid = SnowflakeID(20).to_short_code()
    else:
        save_native_board_consent(b.actor, b.connection.id, b.project.id, ["read"] if consent == "read-only" else [], 1)
    async with Client(b.server("update_card")) as client:
        with pytest.raises(ToolError, match="consent"):
            await client.call_tool(
                "update_card",
                dict(
                    project_uid=project_uid,
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
    assert b.checked == []
    assert b.closed == []


async def test_registered_facade_rejects_oauth_bot_without_native_user_connection(registered_card_commands):
    from fastmcp.exceptions import ToolError
    from langboard_shared.domain.models import Bot

    b = registered_card_commands
    token = mcp_auth_context.set(
        {
            "transport": "oauth",
            "user_or_bot": Bot(
                id=2,
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
            with pytest.raises(ToolError, match="Current native OAuth client connection required"):
                await client.call_tool(
                    "update_card",
                    dict(
                        project_uid=b.project_uid,
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
        assert b.checked == []
        assert b.closed == []
    finally:
        mcp_auth_context.reset(token)
