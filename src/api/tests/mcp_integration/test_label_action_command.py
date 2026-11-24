"""Registered canonical labels retain primitive contracts and current DB authority."""

from contextlib import asynccontextmanager
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastmcp import Client, FastMCP
from langboard.mcp_integration import Dependencies
from langboard.mcp_integration.Providers import AGENT_CORE_TOOLS, create_agent_core_provider, create_native_tool
from langboard.mcp_integration.RoleFilter import McpRoleFilter
from langboard.mcp_integration.Server import McpServer
from langboard.mcp_integration.Tool import McpTool
from langboard.mcp_integration.ToolGroupMiddleware import ToolGroupMiddleware
from langboard.mcp_tools import LabelMcp
from langboard.middlewares.McpAuthMiddleware import mcp_auth_context
from langboard_shared.core.db import DbSession
from langboard_shared.domain.models import Card, GlobalLabel, ProjectAssignedUser, ProjectLabel, ProjectRole, User
from langboard_shared.domain.models.ProjectRole import ProjectRoleAction
from langboard_shared.domain.services.factory.ProjectService import ProjectService
from langboard_shared.domain.services.NativeMcpConsent import connect_native_client, save_native_board_consent
from langboard_shared.Env import Env
from sqlalchemy import delete, func, select, update
from src.api.tests.card_workspace.test_card_change_counts import counts, receipt_count  # noqa: F401
from src.api.tests.project_labels.test_mcp_label_actions import native_labels  # noqa: F401


@pytest.fixture
def registered(native_labels, monkeypatch):  # noqa: F811
    n = native_labels
    f = n.f
    with f.engine.begin() as db:
        for order, label in enumerate(n.labels):
            db.execute(update(ProjectLabel).where(ProjectLabel.id == label.id).values(order=order))
    n.service.project = f.get(ProjectService)
    monkeypatch.setattr(n.service.project_label, "dispatch_created", Mock())
    card_module = import_module(type(f.cards).__module__)
    for publisher in (card_module.CardPublisher, card_module.CardActivityTask, card_module.CardBotTask):
        method = "labels_updated" if publisher is card_module.CardPublisher else "card_labels_updated"
        monkeypatch.setattr(publisher, method, Mock())
    migration = import_module("langboard.migrations.versions.20261025010000-efbc567d8901")
    with f.engine.begin() as db:
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(db)))
        migration.upgrade()
    with DbSession.atomic() as db:
        db.insert(ProjectRole(project_id=f.project.id, user_id=f.user.id, actions=["*"]))
        global_label = GlobalLabel(name="Existing", color="#445566", description="Global", emoji="🏷️")
        db.insert(global_label)
        unique_global = GlobalLabel(name="Global unique", color="#445566", description="Global", emoji="🏷️")
        db.insert(unique_global)
    monkeypatch.setattr(type(Env), "MCP_EMPLOYEE_GROUP_IDS", property(lambda _: []))
    connection = connect_native_client(f.user, "label-action-fixture")
    save_native_board_consent(f.user, connection.id, f.project.id, ["read", "write"], None)
    closed = []
    constructed = []

    def factory(_type):
        @asynccontextmanager
        async def resource():
            constructed.append(_type)
            try:
                yield n.service
            finally:
                closed.append(True)

        return resource

    monkeypatch.setattr(Dependencies, "_factory_dependency", factory)
    server = FastMCP("Registered label command", strict_input_validation=True)
    server.add_tool(create_native_tool("update_label", McpTool.get_tool("update_label"), McpServer._wrap_tool))
    token = mcp_auth_context.set({"user_or_bot": f.user, "transport": "oauth", "native_connection_id": connection.id})
    try:
        yield SimpleNamespace(
            n=n,
            f=f,
            server=server,
            closed=closed,
            constructed=constructed,
            global_label=global_label,
            unique_global=unique_global,
            connection=connection,
        )
    finally:
        mcp_auth_context.reset(token)


def snapshot(b):
    with b.f.engine.connect() as db:
        labels = db.execute(select(ProjectLabel.id, ProjectLabel.name, ProjectLabel.color)).all()
        global_count = db.scalar(select(func.count()).select_from(GlobalLabel))
    return labels, global_count, receipt_count(b.f)


async def invoke(client, b, change, **extra):
    return await client.call_tool(
        "update_label", {"project_uid": b.f.project.get_uid(), "change": change, **extra}, raise_on_error=False
    )


@pytest.mark.parametrize("visibility", ["SHARED", "PRIVATE"])
async def test_registered_four_actions_exact_persistence_idempotence_and_one_service(
    registered, monkeypatch, visibility
):
    b = registered
    if visibility == "PRIVATE":
        with b.f.engine.begin() as db:
            db.execute(
                update(Card).where(Card.id == b.f.card.id).values(visibility="PRIVATE", owner_user_id=b.f.user.id)
            )
    original = McpTool.get_tool
    traps = []
    # Metadata supplies authority, but neither a decorated primitive nor nested DI may run.
    for name in set(LabelMcp.LABEL_ACTION_COMMANDS.values()):
        metadata = original(name)
        trap = Mock(side_effect=AssertionError("Primitive handler invoked"))
        McpRoleFilter.add(*McpRoleFilter.get_filtered(metadata["handler"]))(trap)
        monkeypatch.setitem(metadata, "handler", trap)
        monkeypatch.setattr(LabelMcp, name, trap)
        traps.append(trap)
    actor_calls = []
    for owner, method in [
        (b.n.service.project_label, "create"),
        (b.n.service.project_label, "use_global"),
        (b.n.service.card, "update_labels"),
    ]:
        real = getattr(owner, method)

        def observe(*args, _real=real, **kwargs):
            actor_calls.append(args[0])
            return _real(*args, **kwargs)

        monkeypatch.setattr(owner, method, observe)
    assert b.f.cards.update_labels(b.f.user, b.f.project, b.f.card, [b.n.labels[0].get_uid()], dispatch_effects=False)
    async with Client(b.server) as client:
        tool = (await client.list_tools())[0]
        assert set(tool.input_schema["properties"]) == {"project_uid", "change"}
        assert set(
            McpTool.get_tool("update_label")["input_schema"]["properties"]["change"]["discriminator"]["mapping"]
        ) == set(LabelMcp.LABEL_ACTION_COMMANDS)
        assert len(tool.input_schema["properties"]["change"]["oneOf"]) == 3
        assert tool.input_schema["additionalProperties"] is False
        for definition in tool.input_schema["properties"]["change"]["oneOf"]:
            assert definition["additionalProperties"] is False
        for action, changed, expected in [
            ("attach", True, {"Existing", "Selected"}),
            ("attach", False, {"Existing", "Selected"}),
            ("detach", True, {"Existing"}),
            ("detach", False, {"Existing"}),
        ]:
            result = await invoke(
                client, b, {"action": action, "card_uid": b.f.card.get_uid(), "label_uid": b.n.labels[1].get_uid()}
            )
            assert not result.is_error, result
            assert set(result.structured_content) == {"labels", "changed"}
            assert result.structured_content["changed"] is changed
            assert {label["name"] for label in result.structured_content["labels"]} == expected
            assert {label["name"] for label in b.n.service.project_label.get_api_list_by_card(b.f.card)} == expected
        for global_label, created, expected in [
            (b.global_label, False, "Existing"),
            (b.unique_global, True, "Global unique"),
        ]:
            for expected_created in [created, False]:
                result = await invoke(client, b, {"action": "use_global", "global_label_uid": global_label.get_uid()})
                assert not result.is_error, result
                assert set(result.structured_content) == {"label", "created", "global_label_uid"}
                assert result.structured_content["created"] is expected_created
                assert result.structured_content["label"]["name"] == expected
                assert result.structured_content["global_label_uid"] == global_label.get_uid()
        for name, created in [(" Local new ", True), (" local NEW ", False)]:
            result = await invoke(
                client, b, {"action": "create_local", "name": name, "explicit_user_request": True, "color": "#aabbcc"}
            )
            assert not result.is_error, result
            assert set(result.structured_content) == {"label", "created"}
            assert result.structured_content["created"] is created
            assert result.structured_content["label"]["name"] == "Local new"
            assert result.structured_content["label"]["color"] == "#AABBCC"
    assert len(b.constructed) == len(b.closed) == 10
    assert actor_calls and all(actor is b.f.user for actor in actor_calls)
    for trap in traps:
        trap.assert_not_called()
    assert snapshot(b)[1] == 2


@pytest.mark.parametrize(
    "change",
    [
        {"action": "attach", "card_uid": "x"},
        {"action": "detach", "card_uid": "x", "label_uid": "x", "global_label_uid": "x"},
        {"action": "attach", "card_uid": 1, "label_uid": "x"},
        {"action": "use_global", "global_label_uid": "x", "name": "mixed"},
        {"action": "create_local", "name": "New", "explicit_user_request": 1},
        {"action": "create_local", "name": "New", "explicit_user_request": "true"},
        {"action": "create_local", "name": "New", "explicit_user_request": None},
        {"action": "create_local", "name": "New", "explicit_user_request": True, "color": "red"},
        {"action": "create_local", "name": "New", "explicit_user_request": True, "description": "x" * 4001},
        {"action": "create_local", "name": " ", "explicit_user_request": True},
        {"action": "create_local", "name": "New", "explicit_user_request": False},
        {"action": "create_local", "name": "New"},
        {"action": "create_global", "name": "New"},
    ],
)
async def test_invalid_action_rejected_without_persistence(registered, change):
    b = registered
    before = snapshot(b)
    async with Client(b.server) as client:
        result = await invoke(client, b, change)
    assert result.is_error
    assert snapshot(b) == before


@pytest.mark.parametrize(
    "denial",
    [
        "role",
        "read_only_consent",
        "missing_connection",
        "private_other_owner",
        "revoked_member",
        "inactive",
        "linked",
        "foreign_card",
    ],
)
async def test_current_authority_and_external_consent_reject_before_writes(registered, denial):
    b = registered
    f = b.f
    if denial == "role":
        with f.engine.begin() as db:
            db.execute(
                update(ProjectRole)
                .where(ProjectRole.user_id == f.user.id)
                .values(actions=[ProjectRoleAction.Read.value])
            )
    elif denial == "read_only_consent":
        save_native_board_consent(f.user, b.connection.id, f.project.id, ["read"], 1)
    elif denial == "missing_connection":
        mcp_auth_context.set({"transport": "oauth", "user_or_bot": f.user})
    elif denial == "private_other_owner":
        with f.engine.begin() as db:
            db.execute(
                update(Card)
                .where(Card.id == f.card.id)
                .values(visibility="PRIVATE", owner_user_id=f.other.id, created_by_user_id=f.other.id)
            )
    elif denial == "revoked_member":
        with f.engine.begin() as db:
            db.execute(delete(ProjectAssignedUser).where(ProjectAssignedUser.id == f.member.id))
    elif denial == "inactive":
        with f.engine.begin() as db:
            db.execute(update(User).where(User.id == f.user.id).values(activated_at=None))
    elif denial == "linked":
        with f.engine.begin() as db:
            db.execute(update(Card).where(Card.id == f.card.id).values(source_type="wiki", source_uid="fixture"))
    before = snapshot(b)
    async with Client(b.server) as client:
        result = await invoke(
            client,
            b,
            {
                "action": "attach",
                "card_uid": f.other.get_uid() if denial == "foreign_card" else f.card.get_uid(),
                "label_uid": b.n.labels[0].get_uid(),
            },
        )
    assert result.is_error, result
    assert snapshot(b) == before


async def test_core_discovery_requires_canonical_grant_and_keeps_primitives_separate(registered, monkeypatch):
    b = registered
    assert "update_label" in AGENT_CORE_TOOLS
    registered_tools = {name: McpTool.get_tool(name) for name in ["update_label", "create_local_project_label"]}
    monkeypatch.setattr(McpTool, "get_tools", lambda: registered_tools)
    server = FastMCP("Label discovery", strict_input_validation=True, middleware=[ToolGroupMiddleware()])
    server.add_provider(create_agent_core_provider(McpServer._wrap_tool))
    group = SimpleNamespace(activated_at=object(), tools=["create_local_project_label"])
    mcp_auth_context.set({"user_or_bot": b.f.user, "tool_group": group})
    before = snapshot(b)
    async with Client(server) as client:
        assert [tool.name for tool in await client.list_tools()] == []
        denied = await invoke(client, b, {"action": "create_local", "name": "Denied", "explicit_user_request": True})
        assert denied.is_error
        assert "not allowed" in denied.content[0].text
        assert snapshot(b) == before
        group.tools = ["update_label"]
        assert [tool.name for tool in await client.list_tools()] == ["update_label"]
        allowed = await invoke(client, b, {"action": "create_local", "name": "Granted", "explicit_user_request": True})
        assert not allowed.is_error, allowed
        before = snapshot(b)
        denied = await client.call_tool(
            "create_local_project_label",
            {"project_uid": b.f.project.get_uid(), "name": "Denied primitive", "explicit_user_request": True},
            raise_on_error=False,
        )
        assert denied.is_error
        assert "not allowed" in denied.content[0].text
        assert snapshot(b) == before


@pytest.mark.parametrize("action", ["attach", "detach", "use_global", "create_local"])
async def test_nested_card_scope_is_attributed_to_native_execution_history(registered, action):
    from langboard.mcp_integration.ExecutionHistory import execution_scope

    b = registered
    change = {"action": action}
    if action in {"attach", "detach"}:
        change.update(card_uid=b.f.card.get_uid(), label_uid=b.n.labels[0].get_uid())
    elif action == "use_global":
        change["global_label_uid"] = b.global_label.get_uid()
    else:
        change.update(name="History", explicit_user_request=True)
    scope = {}
    token = execution_scope.set(scope)
    try:
        async with Client(b.server) as client:
            result = await invoke(client, b, change)
        assert not result.is_error, result
        assert scope["project_id"] == b.f.project.id
        assert scope["card_id"] == (b.f.card.id if action in {"attach", "detach"} else None)
        assert scope["user_id"] == b.f.user.id
    finally:
        execution_scope.reset(token)


@pytest.mark.parametrize("action", ["attach", "detach", "use_global", "create_local"])
@pytest.mark.parametrize("role", ["read", "card", "project"])
async def test_action_specific_current_roles_and_read_only_external_consent(registered, action, role):
    b = registered
    actions = [ProjectRoleAction.Read.value]
    if role == "card":
        actions.append(ProjectRoleAction.CardUpdate.value)
    elif role == "project":
        actions.append(ProjectRoleAction.Update.value)
    with b.f.engine.begin() as db:
        db.execute(update(ProjectRole).where(ProjectRole.user_id == b.f.user.id).values(actions=actions))
    change = {"action": action}
    if action in {"attach", "detach"}:
        change.update(card_uid=b.f.card.get_uid(), label_uid=b.n.labels[0].get_uid())
    elif action == "use_global":
        change["global_label_uid"] = b.unique_global.get_uid()
    else:
        change.update(name="Role local", explicit_user_request=True)
    allowed = role == ("card" if action in {"attach", "detach"} else "project")
    before = snapshot(b)
    async with Client(b.server) as client:
        result = await invoke(client, b, change)
        assert result.is_error is not allowed, result
        if not allowed:
            assert snapshot(b) == before
        save_native_board_consent(b.f.user, b.connection.id, b.f.project.id, ["read"], 1)
        before = snapshot(b)
        denied = await invoke(client, b, change)
        assert denied.is_error
        assert snapshot(b) == before
