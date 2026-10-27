"""Independent clients use native comment actions with canonical ACL dispatch."""

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from langboard.mcp_integration.Providers import create_agent_core_provider
from langboard.mcp_integration.Server import McpServer
from langboard.mcp_integration.Tool import McpTool
from langboard.mcp_tools.CommentActionsMcp import COMMENT_ACTION_COMMANDS, CommentChange
from langboard.middlewares.McpAuthMiddleware import mcp_auth_context
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.domain.models import EmployeeMembershipPolicy, NativeMcpBoardConsent, NativeMcpConnection
from langboard_shared.domain.services.NativeMcpConsent import connect_native_client, save_native_board_consent
from langboard_shared.Env import Env
from pydantic import TypeAdapter, ValidationError


@pytest.mark.parametrize(
    "change,expected",
    [
        ({"action": "add", "content": " 한글 댓글 "}, {"content": "한글 댓글"}),
        (
            {"action": "edit", "comment_uid": "comment", "content": "수정"},
            {"comment_uid": "comment", "content": "수정"},
        ),
        ({"action": "delete", "comment_uid": "comment"}, {"comment_uid": "comment"}),
        (
            {"action": "react", "comment_uid": "comment", "reaction": "eyes"},
            {"comment_uid": "comment", "reaction": "eyes"},
        ),
    ],
)
@pytest.mark.parametrize("denied", [False, True])
async def test_comment_facade_dispatches_authorized_command(board, monkeypatch, change, expected, denied):
    _, actor, project, _, role, *_ = board
    # Dispatch stubs still cross the real persisted-user/native-consent boundary.
    for model in (EmployeeMembershipPolicy, NativeMcpConnection, NativeMcpBoardConsent):
        model.__table__.create(DbEngine.get_main_engine())
    monkeypatch.setattr(type(Env), "CARD_INTERNAL_ACCESS_MODE", property(lambda _: "project_members"))
    monkeypatch.setattr(type(Env), "MCP_EMPLOYEE_GROUP_IDS", property(lambda _: []))
    with DbSession.atomic() as db:
        role.actions = ["read", "card_update"]
        db.update(role)
    connection = connect_native_client(actor, "comment-dispatch-fixture")
    save_native_board_consent(actor, connection.id, project.id, ["read", "write"], None)
    original = McpTool.get_tool("change_card_comment")
    calls = []
    output = {
        "comment": {
            "uid": "comment",
            "content": "수정",
            "content_format": "text",
            "content_total_chars": 2,
            "content_truncated": False,
        }
    }
    if change["action"] == "delete":
        output = {"deleted": True}
    elif change["action"] == "react":
        output = {"is_reacted": False}

    def command(**kwargs):
        calls.append(kwargs)
        return output

    metadata = {"handler": command, "exclude": [], "accessible_type": "all"}
    target = COMMENT_ACTION_COMMANDS[change["action"]]
    monkeypatch.setattr(McpTool, "get_tools", lambda: {"change_card_comment": original})
    monkeypatch.setattr(
        McpTool,
        "get_tool",
        lambda name: original if name == "change_card_comment" else metadata if name == target else None,
    )
    monkeypatch.setattr(
        McpServer, "_validate_role", lambda actor, handler, **kwargs: not denied or handler is original["handler"]
    )
    server = FastMCP("Native comment action fixture")
    server.add_provider(create_agent_core_provider(McpServer._wrap_tool))
    token = mcp_auth_context.set(
        {"transport": "oauth", "user_or_bot": actor, "native_connection_id": connection.id}
    )
    try:
        async with Client(server) as client:
            tool = (await client.list_tools())[0]
            assert tool.name == "change_card_comment"
            assert len(tool.input_schema["properties"]["change"]["oneOf"]) == 4
            assert tool.output_schema
            args = {"project_uid": project.get_uid(), "card_uid": "card", "change": change}
            if denied:
                with pytest.raises(ToolError, match="Insufficient permissions"):
                    await client.call_tool("change_card_comment", args)
                assert calls == []
            else:
                result = await client.call_tool("change_card_comment", args)
                assert result.structured_content == output
                assert calls == [{"project_uid": project.get_uid(), "card_uid": "card", **expected}]
    finally:
        mcp_auth_context.reset(token)


@pytest.mark.parametrize(
    "change",
    [
        {"action": "add", "content": " "},
        {"action": "add", "content": "text", "comment_uid": "unexpected"},
        {"action": "edit", "content": "text"},
        {"action": "delete", "comment_uid": ""},
        {"action": "react", "comment_uid": "comment", "reaction": "approved"},
        {"action": "delete", "comment_uid": "comment", "content": "text"},
    ],
)
def test_invalid_comment_actions_never_dispatch(change):
    with pytest.raises(ValidationError):
        TypeAdapter(CommentChange).validate_python(change)


@pytest.mark.parametrize("action", ["add", "edit"])
def test_anchor_and_request_uuid_are_add_only(action):
    anchor = dict(
        type="ImageRegionSelector",
        version=1,
        shape="point",
        image_width=100,
        image_height=100,
        x=10,
        y=20,
        width=0,
        height=0,
        source=dict(type="card_attachment", attachment_uid="image", revision="a" * 64),
    )
    data = dict(action=action, content="Review", anchor=anchor, request_uid="12345678-1234-5678-1234-567812345678")
    if action == "edit":
        data["comment_uid"] = "comment"
        with pytest.raises(ValidationError):
            TypeAdapter(CommentChange).validate_python(data)
    else:
        model = TypeAdapter(CommentChange).validate_python(data, strict=True)
        assert model.anchor.model_dump() == anchor
        assert str(model.request_uid) == data["request_uid"]
        for invalid in (None, "invalid", 1):
            with pytest.raises(ValidationError):
                TypeAdapter(CommentChange).validate_python({**data, "request_uid": invalid}, strict=True)
