"""Registered comment actions use one authorization boundary and one domain owner."""

from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from langboard.mcp_integration.Server import McpServer
from langboard.mcp_integration.Tool import McpTool
from langboard.mcp_tools.CommentActionsMcp import CommentChange
from langboard_shared.domain.services.NativeMcpConsent import save_native_board_consent
from pydantic import TypeAdapter, ValidationError
from src.api.tests.mcp_integration.test_card_actions import native as native  # noqa: F401
from src.api.tests.mcp_integration.test_card_actions import native_card_context as native_card_context  # noqa: F401
from src.api.tests.mcp_integration.test_card_actions import native_engine as native_engine  # noqa: F401
from src.api.tests.mcp_integration.test_card_actions import (
    registered_card_commands as registered_card_commands,  # noqa: F401
)


@pytest.mark.parametrize("change", [
    {"action": "add", "content": " hello "},
    {"action": "edit", "comment_uid": "comment", "content": " hello "},
    {"action": "delete", "comment_uid": "comment"},
    {"action": "react", "comment_uid": "comment", "reaction": "eyes"},
])
@pytest.mark.parametrize("denied", [False, True])
async def test_registered_comment_owner_and_single_authorization(registered_card_commands, monkeypatch, change, denied):  # noqa: F811 - imported pytest fixture
    b = registered_card_commands
    comment = SimpleNamespace(api_response=lambda: {"uid": "comment", "content": "hello"})
    owner = SimpleNamespace(create=Mock(return_value=comment), update=Mock(return_value=comment),
                            delete=Mock(return_value=True), get_by_id_like=Mock(return_value=comment),
                            toggle_reaction=Mock(return_value=False))
    b.service.card_comment = owner
    checked = []
    monkeypatch.setattr(McpServer, "_validate_role", lambda actor, fn, **kw: checked.append(fn) or not denied)
    async with Client(b.server("change_card_comment")) as client:
        tool = (await client.list_tools())[0]
        assert len(tool.input_schema["properties"]["change"]["oneOf"]) == 4
        args = {"project_uid": b.project_uid, "card_uid": "card", "change": change}
        if denied:
            with pytest.raises(ToolError, match="Insufficient permissions"):
                await client.call_tool("change_card_comment", args)
        else:
            result = (await client.call_tool("change_card_comment", args)).structured_content
            if change["action"] == "delete":
                assert result == {"deleted": True}
            elif change["action"] == "react":
                assert result == {"is_reacted": False}
            else:
                assert result["comment"]["uid"] == "comment"
                assert result["comment"]["content"] == "hello"
    assert checked == [McpTool.get_tool("change_card_comment")["handler"]]
    calls = [owner.create, owner.update, owner.delete, owner.toggle_reaction]
    assert sum(mock.call_count for mock in calls) == (0 if denied else 1)
    assert len(b.closed) == (0 if denied else 1)
    if not denied:
        operation = dict(add=owner.create, edit=owner.update, delete=owner.delete, react=owner.toggle_reaction)[change["action"]]
        assert operation.call_args.args[:3] == (b.actor, b.project_uid, "card")
        if change["action"] in {"add", "edit"}:
            assert operation.call_args.args[-1].content == "hello"


async def test_comment_write_requires_current_native_board_grant(registered_card_commands):  # noqa: F811 - imported pytest fixture
    b = registered_card_commands
    save_native_board_consent(b.actor, b.connection.id, b.project.id, ["read"], None)
    b.service.card_comment = SimpleNamespace(create=Mock())
    async with Client(b.server("change_card_comment")) as client:
        with pytest.raises(ToolError):
            await client.call_tool("change_card_comment", {
                "project_uid": b.project_uid, "card_uid": "card", "change": {"action": "add", "content": "hello"}
            })
    b.service.card_comment.create.assert_not_called()


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


@pytest.mark.parametrize("field", ["deleted", "is_reacted"])
@pytest.mark.parametrize("value", [1, 0, "true", None])
def test_comment_receipts_reject_coerced_boolean_outcomes(field, value):
    from langboard.mcp_tools.CommentActionsMcp import CommentActionOutput

    with pytest.raises(ValidationError):
        CommentActionOutput.model_validate({field: value})
