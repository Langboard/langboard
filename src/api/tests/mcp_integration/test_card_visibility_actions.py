# ruff: noqa: F811
"""Visibility commands preserve native disclosure, role and audit boundaries."""

from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from langboard.mcp_integration.Receipts import MutationReceiptMiddleware
from langboard.mcp_integration.RoleFilter import McpRoleFilter
from langboard.mcp_integration.Server import McpServer
from langboard.mcp_integration.Tool import McpTool
from langboard.mcp_tools.CardManagementMcp import change_card_visibility
from langboard.middlewares.McpAuthMiddleware import mcp_auth_context
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.domain.models import (
    Bot,
    Card,
    CardVisibilityChange,
    NativeMcpBoardConsent,
    NativeMcpConnection,
    ProjectColumn,
    ProjectRole,
)
from langboard_shared.domain.models.ProjectRole import ProjectRoleAction
from langboard_shared.domain.services.NativeMcpConsent import connect_native_client, save_native_board_consent
from langboard_shared.Env import Env
from langboard_shared.publishers import ProjectPublisher
from tests.card_visibility.conftest import current_card  # noqa: F401 - shared pytest fixture
from tests.mcp_integration.test_card_actions import registered_card_commands  # noqa: F401 - shared pytest fixture


@pytest.fixture
def native_card_context(current_card, monkeypatch):
    actor, project, card, _ = current_card
    engine = DbEngine.get_main_engine()
    for model in (NativeMcpConnection, NativeMcpBoardConsent):
        model.__table__.create(engine)
    ProjectColumn.__table__.create(engine, checkfirst=True)
    with DbSession.atomic() as db:
        column = ProjectColumn(project_id=project.id, name="Current")
        db.insert(column)
        card.project_column_id = column.id
        db.update(card)
    monkeypatch.setattr(type(Env), "CARD_INTERNAL_ACCESS_MODE", property(lambda _: "project_members"))
    monkeypatch.setattr(type(Env), "MCP_EMPLOYEE_GROUP_IDS", property(lambda _: []))
    connection = connect_native_client(actor, "visibility-fixture")
    save_native_board_consent(actor, connection.id, project.id, ["read", "write"], None)
    token = mcp_auth_context.set({"transport": "oauth", "user_or_bot": actor, "native_connection_id": connection.id})
    try:
        yield SimpleNamespace(actor=actor, project=project, project_uid=project.get_uid(), connection=connection)
    finally:
        mcp_auth_context.reset(token)


@pytest.fixture
def visibility_card(current_card, monkeypatch):
    user, project, card, owner = current_card
    CardVisibilityChange.__table__.create(DbEngine.get_main_engine())
    monkeypatch.setattr(type(Env), "CARD_INTERNAL_ACCESS_MODE", property(lambda _: "project_members"))
    with DbSession.use(readonly=False) as db:
        card.visibility = "SHARED"
        card.owner_user_id = None
        db.update(card)
        db.insert(ProjectRole(project_id=project.id, user_id=user.id, actions=["card_update"]))
    monkeypatch.setattr(ProjectPublisher, "updated", Mock())
    return SimpleNamespace(user=user, project=project, card=card, owner=owner, service=SimpleNamespace(card=owner))


def invoke(b, target="INTERNAL", expected="SHARED"):
    return change_card_visibility(b.project.get_uid(), b.card.get_uid(), target, expected, b.user, b.service)


def persisted(b):
    with DbSession.use(readonly=False) as db:
        return (
            db.exec(SqlBuilder.select.table(Card).where(Card.id == b.card.id)).first().visibility,
            db.exec(SqlBuilder.select.table(CardVisibilityChange)).all(),
        )


def test_native_reduction_persists_with_mcp_actor_audit(visibility_card):
    b = visibility_card
    assert invoke(b)["visibility"] == "INTERNAL"
    visibility, audits = persisted(b)
    assert visibility == "INTERNAL"
    assert [(a.changed_by_user_id, a.channel, a.previous_visibility, a.next_visibility) for a in audits] == [
        (b.user.id, CollaborationChannel.Mcp.value, "SHARED", "INTERNAL")
    ]


def test_native_disclosure_only_hands_off_to_human_ui(visibility_card, monkeypatch):
    b = visibility_card
    invoke(b)
    mutation = Mock(wraps=b.owner.change_visibility)
    monkeypatch.setattr(b.owner, "change_visibility", mutation)
    result = invoke(b, "SHARED", "INTERNAL")
    assert result["status"] == "human_action_required"
    assert result["changed"] is False
    assert result["expected_visibility"] == "INTERNAL"
    assert result["card_url"].endswith(f"/board/{b.project.get_uid()}/{b.card.get_uid()}")
    assert "human" in result["message"].lower()
    assert set(result) == {"status", "changed", "visibility", "expected_visibility", "card_url", "message"}
    mutation.assert_not_called()
    assert persisted(b)[0] == "INTERNAL"
    assert len(persisted(b)[1]) == 1


@pytest.mark.parametrize("target", ["INTERNAL", "SHARED"])
@pytest.mark.parametrize("denial", ["stale", "private", "external", "revoked", "unreadable", "mirror"])
def test_native_denials_are_sanitized_and_never_mutate(visibility_card, monkeypatch, target, denial):
    b = visibility_card
    expected = "SHARED"
    with DbSession.use(readonly=False) as db:
        if denial == "private":
            b.card.visibility, b.card.owner_user_id = "PRIVATE", b.user.id
            db.update(b.card)
        if denial == "revoked":
            role = db.exec(SqlBuilder.select.table(ProjectRole)).first()
            role.actions = ["read"]
            db.update(role)
        if denial == "mirror":
            b.card.source_type = Card.LINKED_RESOURCE_PROJECT_CARD
            b.card.source_uid = "source00001"
            db.update(b.card)
    if denial == "stale":
        expected = "INTERNAL"
    if denial == "external":
        monkeypatch.setattr(b.owner, "_resolve_internal_access", lambda _: False)
    if denial == "unreadable":
        monkeypatch.setattr(b.owner, "resolve_readable_card", lambda *a, **kw: None)
    with pytest.raises(ToolError, match="^Card visibility transition unavailable; read current state and verify access$"):
        invoke(b, target, expected)
    assert persisted(b)[1] == []
    assert not ProjectPublisher.updated.called


async def test_modern_facade_and_native_retain_update_role(registered_card_commands, monkeypatch):
    b = registered_card_commands
    b.service.card.change_visibility = Mock(return_value={"visibility": "INTERNAL", "updated_at": "fixture"})
    async with Client(b.server("update_card", "change_card_visibility")) as client:
        result = await client.call_tool("update_card", {
            "project_uid": b.project_uid, "card_uid": "card0000001",
            "change": {"action": "visibility", "visibility": "INTERNAL", "expected_visibility": "SHARED"},
        })
    assert result.structured_content["visibility"] == "INTERNAL"
    args, kwargs = b.service.card.change_visibility.call_args
    assert args[:3] == (b.actor, b.project_uid, "card0000001")
    assert kwargs == {"channel": CollaborationChannel.Mcp, "confirmed": False, "expected_visibility": "SHARED"}
    assert b.checked == [McpTool.get_tool(name)["handler"] for name in ("update_card", "change_card_visibility")]
    assert McpRoleFilter.get_filtered(b.checked[-1])[1] == [ProjectRoleAction.CardUpdate.value]
    monkeypatch.setattr(McpServer, "_validate_role", lambda actor, fn, **kw: fn is not change_card_visibility)
    b.service.card.change_visibility.reset_mock()
    async with Client(b.server("update_card", "change_card_visibility")) as client:
        for name, payload in [
            ("update_card", {"project_uid": b.project_uid, "card_uid": "card0000001", "change": {
                "action": "visibility", "visibility": "INTERNAL", "expected_visibility": "SHARED"}}),
            ("change_card_visibility", {"project_uid": b.project_uid, "card_uid": "card0000001", "visibility": "INTERNAL", "expected_visibility": "SHARED"}),
        ]:
            with pytest.raises(ToolError, match="Insufficient permissions"):
                await client.call_tool(name, payload)
    b.service.card.change_visibility.assert_not_called()


@pytest.mark.parametrize("extra", [{"confirmed": True}, {"visibility": "PRIVATE"}, {"expected_visibility": "PRIVATE"}, {"expected_visibility": None}])
async def test_modern_facade_rejects_private_or_confirmation_arguments(registered_card_commands, extra):
    b = registered_card_commands
    b.service.card.change_visibility = Mock()
    change = {"action": "visibility", "visibility": "SHARED", "expected_visibility": "INTERNAL", **extra}
    async with Client(b.server("update_card")) as client:
        with pytest.raises(ToolError):
            await client.call_tool("update_card", {"project_uid": b.project_uid, "card_uid": "card0000001", "change": change})
    b.service.card.change_visibility.assert_not_called()


async def test_modern_visibility_requires_signed_in_user(registered_card_commands):
    b = registered_card_commands
    b.service.card.change_visibility = Mock()
    bot = Bot(id=1, name="Fixture", bot_uname="fixture", app_api_token="test", platform="default", platform_running_type="default")
    # Bots enter through API credentials, not the user-only OAuth callback.
    token = mcp_auth_context.set({"transport": "api_key", "user_or_bot": bot})
    try:
        async with Client(b.server("update_card", "change_card_visibility")) as client:
            for name, payload in [
                ("update_card", {"project_uid": b.project_uid, "card_uid": "card0000001", "change": {
                    "action": "visibility", "visibility": "INTERNAL", "expected_visibility": "SHARED"}}),
                ("change_card_visibility", {"project_uid": b.project_uid, "card_uid": "card0000001", "visibility": "INTERNAL", "expected_visibility": "SHARED"}),
            ]:
                with pytest.raises(ToolError, match="Authentication required"):
                    await client.call_tool(name, payload)
        b.service.card.change_visibility.assert_not_called()
    finally:
        mcp_auth_context.reset(token)


def test_native_shared_noop_is_read_only(visibility_card, monkeypatch):
    b = visibility_card
    mutation = Mock(wraps=b.owner.change_visibility)
    monkeypatch.setattr(b.owner, "change_visibility", mutation)
    assert invoke(b, "SHARED") == {"status": "unchanged", "changed": False, "visibility": "SHARED"}
    mutation.assert_not_called()
    assert persisted(b) == ("SHARED", [])


@pytest.mark.parametrize("current_card", ["sqlite-http"], indirect=True)
async def test_modern_disclosure_handoff_is_read_only(visibility_card, registered_card_commands, monkeypatch):
    b, registered = visibility_card, registered_card_commands
    invoke(b)
    registered.service.card = b.owner
    mutation = Mock(wraps=b.owner.change_visibility)
    monkeypatch.setattr(b.owner, "change_visibility", mutation)
    token = mcp_auth_context.set({"transport": "oauth", "user_or_bot": b.user,
                                  "native_connection_id": registered.connection.id})
    try:
        async with Client(registered.server("update_card", "change_card_visibility")) as client:
            arguments = {"project_uid": b.project.get_uid(), "card_uid": b.card.get_uid(),
                         "visibility": "SHARED", "expected_visibility": "INTERNAL"}
            for name, payload in [
                ("change_card_visibility", arguments),
                ("update_card", {"project_uid": arguments["project_uid"], "card_uid": arguments["card_uid"],
                                 "change": {"action": "visibility", "visibility": "SHARED", "expected_visibility": "INTERNAL"}}),
            ]:
                result = await client.call_tool(name, payload)
                assert result.structured_content["status"] == "human_action_required"
                assert result.structured_content["expected_visibility"] == "INTERNAL"
            with pytest.raises(ToolError):
                await client.call_tool("change_card_visibility", {**arguments, "confirmed": True})
        mutation.assert_not_called()
        assert persisted(b)[0] == "INTERNAL"
        assert len(persisted(b)[1]) == 1
    finally:
        mcp_auth_context.reset(token)


@pytest.mark.parametrize("failure", [PermissionError("secret"), ValueError("secret")])
async def test_modern_domain_errors_do_not_disclose_details(registered_card_commands, failure):
    b = registered_card_commands
    b.service.card.change_visibility = Mock(side_effect=failure)
    async with Client(b.server("update_card")) as client:
        with pytest.raises(ToolError, match="Card visibility transition unavailable") as error:
            await client.call_tool("update_card", {"project_uid": b.project_uid, "card_uid": "card0000001", "change": {
                "action": "visibility", "visibility": "INTERNAL", "expected_visibility": "SHARED"}})
        assert "secret" not in str(error.value)


@pytest.mark.parametrize("current_card", ["sqlite-http"], indirect=True)
@pytest.mark.parametrize("route", ["direct", "canonical", "raw", "legacy"])
async def test_visibility_receipts_match_native_persistence(visibility_card, registered_card_commands, monkeypatch, route):
    b, registered = visibility_card, registered_card_commands
    registered.service.card = b.owner
    names = ("change_card_visibility", "update_card")
    tools = {name: McpTool.get_tool(name) for name in names}
    monkeypatch.setattr(McpTool, "get_tools", lambda: tools)
    if route == "direct":
        server = registered.server(*names)
        server.add_middleware(MutationReceiptMiddleware())
    else:
        profile = {"canonical": "agent", "raw": "raw", "legacy": "compatibility"}[route]
        _, server = McpServer.get_http_app(profile)
    token = mcp_auth_context.set({
        "transport": "oauth", "user_or_bot": b.user,
        "tool_group": SimpleNamespace(activated_at=object(), tools=list(names)),
        "native_connection_id": registered.connection.id,
    })
    request_ids = []

    async def call(client, visibility, expected):
        arguments = {"project_uid": b.project.get_uid(), "card_uid": b.card.get_uid(),
                     "visibility": visibility, "expected_visibility": expected}
        name = "update_card" if route == "canonical" else "change_card_visibility"
        payload = ({"project_uid": arguments["project_uid"], "card_uid": arguments["card_uid"],
                    "change": {"action": "visibility", "visibility": visibility, "expected_visibility": expected}}
                   if route == "canonical" else arguments)
        if route == "raw":
            return await client.call_tool("call_raw_tool", {"name": name, "arguments": payload})
        return await client.call_tool(name, payload)

    def receipt(result, outcome):
        assert not result.is_error
        value = (result.meta or {}).get("mutation_receipt")
        if route == "legacy":
            assert value is None
            return
        assert len(value["request_id"]) == 32
        request_ids.append(value["request_id"])
        assert value["tool"] == ("update_card" if route == "canonical" else "change_card_visibility")
        assert value["outcome"] == outcome
        assert value["retryable"] is False
        assert value["revision_conflict"] is False
        assert value["next_action"] == ("read_and_review" if outcome == "not_applied" else "none")

    try:
        async with Client(server) as client:
            # A successful idempotent no-op remains applied despite changed:false.
            unchanged = await call(client, "SHARED", "SHARED")
            assert unchanged.structured_content == {"status": "unchanged", "changed": False, "visibility": "SHARED"}
            receipt(unchanged, "applied")
            assert persisted(b) == ("SHARED", [])
            reduced = await call(client, "INTERNAL", "SHARED")
            receipt(reduced, "applied")
            assert persisted(b)[0] == "INTERNAL"
            assert len(persisted(b)[1]) == 1
            mutation = Mock(wraps=b.owner.change_visibility)
            monkeypatch.setattr(b.owner, "change_visibility", mutation)
            for _ in range(2):
                handoff = await call(client, "SHARED", "INTERNAL")
                assert handoff.structured_content["status"] == "human_action_required"
                assert handoff.structured_content["changed"] is False
                assert set(handoff.structured_content) == {
                    "status", "changed", "visibility", "expected_visibility", "card_url", "message",
                }
                receipt(handoff, "not_applied")
                assert persisted(b)[0] == "INTERNAL"
                assert len(persisted(b)[1]) == 1
            mutation.assert_not_called()
            # A later successful command must not inherit the disclosure declaration.
            receipt(await call(client, "INTERNAL", "INTERNAL"), "applied")
        assert len(set(request_ids)) == len(request_ids)
    finally:
        mcp_auth_context.reset(token)
