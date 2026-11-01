"""Project detail reads retain native identity variants without visit writes."""

import json
from types import SimpleNamespace
import pytest
from fastmcp import Client
from langboard.mcp_integration.ProjectOutputs import PROJECT_OUTPUTS
from langboard.mcp_integration.Server import McpServer
from langboard.mcp_integration.Tool import McpTool
from langboard.mcp_tools import ProjectMcp
from langboard.middlewares.McpAuthMiddleware import mcp_auth_context
from langboard_shared.core.storage import FileModel
from langboard_shared.domain.constants.DomainEventType import DomainEventType
from langboard_shared.domain.models import (
    BotSchedule,
    Project,
    ProjectBotSchedule,
    ProjectBotScope,
    ProjectLabel,
    User,
)
from langboard_shared.domain.models.BotSchedule import BotScheduleStatus
from langboard_shared.domain.services.NativeMcpBoundary import native_mcp_boundary
from mcp.shared.exceptions import MCPError
from pydantic import TypeAdapter, ValidationError


def native_result(actor):
    avatar = FileModel(storage_type="local", storage_name="avatar", original_filename="avatar.png",
                       filename="avatar.png", path="/file/avatar.png")
    project = Project(id=123, owner_id=456, title="Board", avatar=avatar, avatar_revision=2)
    owner = User(id=456, firstname="User", lastname="Name", email="fixture@example.invalid", password="fixture", avatar=avatar, avatar_revision=3)
    label = ProjectLabel(
        id=789,
        project_id=project.id,
        name="Contract",
        color="#123456",
        description="Development interface",
        global_label_id=790,
        global_display={"emoji": "📜", "translations": {"ko": {"name": "계약"}}},
    )
    response = {
        **project.api_response(),
        "all_members": [
            owner.api_response(),
            User.create_unknown_user_api_response("deleted"),
            User.create_email_user_api_response(owner.id, "invited@example.invalid"),
        ],
        "invited_member_uids": [owner.get_uid()],
        "labels": [label.api_response()],
    }
    if isinstance(actor, User):
        response["current_auth_role_actions"] = ["*"]
    scope = ProjectBotScope(id=800, project_id=project.id, bot_id=801, conditions=[DomainEventType.CardCreated])
    schedule = BotSchedule(id=802, bot_id=801, status=BotScheduleStatus.Started, interval_str="* * * * *")
    assigned_schedule = ProjectBotSchedule(id=803, project_id=project.id, bot_schedule_id=schedule.id)
    return project, {
        "project": response,
        "project_bot_scopes": [scope.api_response()],
        "project_bot_schedules": [{**schedule.api_response(), **assigned_schedule.api_response()}],
    }


@pytest.mark.parametrize("user", [True, False])
@pytest.mark.parametrize("avatar", [True, False])
def test_native_projection_keeps_member_variants_and_bot_role_omission(user, avatar):
    actor = (
        User(id=456, firstname="User", lastname="Name", email="fixture@example.invalid", password="fixture")
        if user
        else object()
    )
    _, expected = native_result(actor)
    if not avatar:
        expected["project"]["avatar"] = None
        expected["project"]["all_members"][0]["avatar"] = None
    actual = PROJECT_OUTPUTS["get_project"].model_validate(expected).model_dump(mode="json")
    assert actual == TypeAdapter(dict).dump_python(expected, mode="json")
    assert ("current_auth_role_actions" in actual["project"]) is user
    assert "created_at" not in actual["project"]["all_members"][1]
    assert actual["project"]["labels"][0]["global_display"]["translations"]["ko"]["name"] == "계약"


def test_project_contract_rejects_private_or_malformed_nested_fields():
    _, expected = native_result(object())
    expected["project"]["all_members"][0]["password"] = "not-a-real-secret"
    with pytest.raises(ValidationError):
        PROJECT_OUTPUTS["get_project"].model_validate(expected)
    del expected["project"]["all_members"][0]["password"]
    expected["project_bot_scopes"][0]["is_frozen"] = 1
    with pytest.raises(ValidationError):
        PROJECT_OUTPUTS["get_project"].model_validate(expected)


@pytest.mark.parametrize("scope", [None, {"read": {123}, "write": set()}])
def test_direct_project_read_does_not_record_visit_with_or_without_native_boundary(scope):
    actor = User(id=456, firstname="User", lastname="Name", email="fixture@example.invalid", password="fixture")
    project, expected = native_result(actor)
    visits = []
    service = SimpleNamespace(
        project=SimpleNamespace(
            get_details=lambda *args: (project, expected["project"]),
            get_api_bot_scope_list=lambda value: expected["project_bot_scopes"],
            get_api_bot_schedule_list=lambda value: expected["project_bot_schedules"],
            set_last_view=lambda *args: visits.append(args),
        )
    )
    token = native_mcp_boundary.set(scope)
    try:
        assert ProjectMcp.get_project("board", actor, service) == expected
        assert visits == []
    finally:
        native_mcp_boundary.reset(token)


@pytest.mark.parametrize("field", ["project", "member"])
@pytest.mark.parametrize("revision", [-1, True, "2"])
def test_project_avatar_revision_rejects_invalid_values(field, revision):
    _, expected = native_result(object())
    target = expected["project"] if field == "project" else expected["project"]["all_members"][0]
    target["avatar_revision"] = revision
    with pytest.raises(ValidationError):
        PROJECT_OUTPUTS["get_project"].model_validate(expected)


@pytest.mark.parametrize("profile", ["raw", "compatibility"])
@pytest.mark.parametrize("malformed", [False, True])
async def test_project_read_payload_validation_without_visit_writes(monkeypatch, profile, malformed):
    actor = User(id=456, firstname="User", lastname="Name", email="fixture@example.invalid", password="fixture")
    project, expected = native_result(actor)
    effects = []

    def details(*args):
        assert args == (actor, "board", False)
        effects.append("details")
        if malformed:
            expected["project"]["dock_revision"] = "invalid"
        return project, expected["project"]

    service = SimpleNamespace(
        project=SimpleNamespace(
            get_details=details,
            get_api_bot_scope_list=lambda value: expected["project_bot_scopes"],
            get_api_bot_schedule_list=lambda value: expected["project_bot_schedules"],
            set_last_view=lambda *args: effects.append("last_view"),
        )
    )

    def handler(project_uid: str) -> dict:
        return ProjectMcp.get_project(project_uid, actor, service)

    name = "get_project"
    metadata = {"handler": handler, "description": "Details", "exclude": [], "accessible_type": "all"}
    monkeypatch.setattr(McpTool, "get_tools", lambda: {name: metadata})
    monkeypatch.setattr(McpTool, "get_tool", lambda key: metadata if key == name else None)
    monkeypatch.setattr(McpServer, "_validate_auth", lambda *args: True)
    monkeypatch.setattr(McpServer, "_validate_role", lambda *args, **kwargs: True)
    _, server = McpServer.get_http_app(profile)
    token = mcp_auth_context.set(
        {"user_or_bot": actor, "tool_group": SimpleNamespace(activated_at=object(), tools=[name])}
    )
    try:
        async with Client(server) as client:
            if profile == "raw":
                search = await client.call_tool("search_raw_tools", {"pattern": "^get_project\\b"})
                discovered = json.loads(search.content[0].text)[0]
                assert discovered["outputSchema"]["properties"]["project"]
                assert discovered["annotations"]["readOnlyHint"] is True
            if malformed and profile == "raw":
                # This reviewed read has no mutation receipt. FastMCP maps its
                # rejected typed output to a protocol validation error.
                with pytest.raises(MCPError, match="Invalid request parameters") as rejected:
                    await client.call_tool(
                        "call_raw_tool",
                        {"name": name, "arguments": {"project_uid": "board"}},
                        raise_on_error=False,
                    )
                assert rejected.value.error.code == -32602
                assert effects == ["details"]
                return
            result = await client.call_tool(
                "call_raw_tool" if profile == "raw" else name,
                {"name": name, "arguments": {"project_uid": "board"}} if profile == "raw" else {"project_uid": "board"},
                raise_on_error=False,
            )
            assert effects == ["details"]
            assert not result.is_error
            assert "mutation_receipt" not in (result.meta or {})
            assert result.structured_content == TypeAdapter(dict).dump_python(expected, mode="json")
    finally:
        mcp_auth_context.reset(token)
