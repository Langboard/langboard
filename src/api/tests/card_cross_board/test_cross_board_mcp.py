"""Native MCP consumer delegates persisted cross-board operations and current ACLs."""
# ruff: noqa: F811

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from langboard.mcp_integration import McpTool
from langboard.mcp_tools import CardManagementMcp as commands
from langboard_shared.core.db import DbSession
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.domain.models import Card, CardAppOwnership, ProjectActivity, ProjectRole
from sqlalchemy import update
from test_card_clones import add_reader, clone_board, rows  # noqa: F401
from test_card_mirrors import mirror_board  # noqa: F401
from test_card_moves import move_board  # noqa: F401


def invoke(b, operation="move", **kwargs):
    return commands.cross_board_card(
        b.source_board.get_uid(),
        b.source.get_uid(),
        b.destination.get_uid(),
        b.column.get_uid(),
        operation,
        b.request_uid,
        b.actor,
        SimpleNamespace(card=b.service),
        **kwargs,
    )


def test_registration_schema_excludes_injection_and_exposes_typed_confirmation():
    metadata = McpTool.get_tool("cross_board_card")
    assert metadata["handler"] is commands.cross_board_card and metadata["accessible_type"] == "user"
    fields = metadata["input_schema"]["properties"]
    assert not {"user", "service"}.intersection(fields)
    assert fields["operation"]["enum"] == ["link", "clone", "move"]
    assert fields["request_uid"]["format"] == "uuid"
    assert fields["confirmed"]["type"] == "boolean" and fields["confirmed"]["default"] is False
    assert "explicit user" in metadata["description"] and "Never confirm automatically" in metadata["description"]


@pytest.mark.parametrize("operation", ["link", "clone", "move"])
def test_native_persistent_operations_and_replay_have_compact_authorized_results(move_board, operation):
    b = move_board
    result = invoke(b, operation)
    assert result.status == "applied" and result.changed
    assert (result.card_uid == b.source.get_uid()) is (operation == "move")
    assert result.card_link_markdown == f"[Open card in Langboard]({result.card_url})"
    assert f"/board/{b.destination.get_uid()}/{result.card_uid}" in result.card_url
    assert not {"title", "description", "activity_history", "source", "new_reader_uids"}.intersection(
        result.model_dump()
    )
    assert "Original private body" not in result.model_dump_json()
    replay = invoke(b, operation)
    assert replay.status == "unchanged" and not replay.changed and replay.card_uid == result.card_uid
    assert len(rows(Card)) == (1 if operation == "move" else 2)
    assert len(rows(ProjectActivity)) == (2 if operation == "move" else 1)


@pytest.mark.parametrize("operation", ["clone", "move"])
def test_expanded_reader_preview_needs_explicit_current_token_and_keeps_request(move_board, operation):
    b = move_board
    reader = add_reader(b)
    preview = invoke(b, operation)
    assert preview.status == "confirmation_required" and not preview.changed and preview.new_reader_count == 1
    assert reader.get_uid() not in preview.model_dump_json() and preview.card_uid is None
    assert len(rows(Card)) == 1 and rows(ProjectActivity) == []
    for invalid in ({"confirmation_token": preview.confirmation_token}, {"confirmed": True}, {"confirmed": "true"}):
        with pytest.raises(ToolError, match="Explicit confirmation"):
            invoke(b, operation, **invalid)
    with DbSession.use(readonly=False) as db:
        db.exec(update(Card).where(Card.id == b.source.id).values(title="Changed after preview"))
    refreshed = invoke(b, operation, confirmed=True, confirmation_token=preview.confirmation_token)
    assert refreshed.status == "confirmation_required" and refreshed.confirmation_token != preview.confirmation_token
    result = invoke(b, operation, confirmed=True, confirmation_token=refreshed.confirmation_token)
    assert result.status == "applied" and result.request_uid == preview.request_uid


@pytest.mark.parametrize("operation", ["link", "clone", "move"])
@pytest.mark.parametrize("denial", ["source", "target", "app"])
def test_current_native_acl_and_app_ownership_deny_without_disclosure(move_board, operation, denial):
    b = move_board
    with DbSession.use(readonly=False) as db:
        if denial == "source":
            db.exec(update(ProjectRole).where(ProjectRole.project_id == b.source_board.id).values(actions=[]))
        elif denial == "target":
            db.exec(
                update(type(b.destination))
                .where(type(b.destination).id == b.destination.id)
                .values(owner_id=b.outsider.id)
            )
        else:
            db.insert(CardAppOwnership(card_id=b.source.id, app_key="private-app"))
    with pytest.raises(ToolError) as denied:
        invoke(b, operation)
    assert "secret" not in str(denied.value).lower() and "private-app" not in str(denied.value)
    assert rows(Card)[0].project_id == b.source_board.id and len(rows(Card)) == 1 and rows(ProjectActivity) == []


def test_private_mcp_channel_reaches_native_owner_and_error_details_are_redacted(move_board, monkeypatch):
    b = move_board
    with DbSession.use(readonly=False) as db:
        db.exec(
            update(Card)
            .where(Card.id == b.source.id)
            .values(visibility="PRIVATE", owner_user_id=b.actor.id, created_by_user_id=b.actor.id)
        )
    owner = Mock(wraps=b.service.move_cross_board_card)
    monkeypatch.setattr(b.service, "move_cross_board_card", owner)
    result = invoke(b)
    assert result.changed and owner.call_args.kwargs["channel"] is CollaborationChannel.Mcp
    assert owner.call_args.kwargs["request_uid"] == b.request_uid
    owner.side_effect = RuntimeError("private provider credential and document body")
    with pytest.raises(ToolError) as denied:
        invoke(b)
    assert "credential" not in str(denied.value) and "document body" not in str(denied.value)


def test_exclusion_counts_omit_identity_and_conflicting_retry_cannot_move_again(move_board):
    from langboard_shared.domain.models import CardAssignedProjectLabel, ProjectColumn, ProjectLabel

    b = move_board
    with DbSession.use(readonly=False) as db:
        label = ProjectLabel(
            project_id=b.source_board.id, name="Private local label", color="#000000", description="Private"
        )
        db.insert(label)
        db.insert(CardAssignedProjectLabel(card_id=b.source.id, project_label_id=label.id))
        other_column = ProjectColumn(project_id=b.destination.id, name="Other destination")
        db.insert(other_column)
    result = invoke(b)
    assert result.excluded.labels == 1 and result.excluded.members == 0
    assert label.get_uid() not in result.model_dump_json() and "Private local label" not in result.model_dump_json()
    original_column = b.column
    b.column = other_column
    with pytest.raises(ToolError):
        invoke(b)
    assert rows(Card)[0].project_column_id == original_column.id and len(rows(ProjectActivity)) == 2


async def test_native_fastmcp_dispatch_schema_and_current_revoked_replay(move_board, monkeypatch):
    import importlib
    from langboard.mcp_integration.Server import McpServer
    from langboard.middlewares.McpAuthMiddleware import mcp_auth_context

    dependencies = importlib.import_module("langboard.mcp_integration.Dependencies")
    server_module = importlib.import_module("langboard.mcp_integration.Server")
    b = move_board
    service = SimpleNamespace(card=b.service, close=lambda: None)

    @asynccontextmanager
    async def resource():
        yield service

    monkeypatch.setattr(dependencies, "_factory_dependency", lambda _: resource)
    monkeypatch.setattr(server_module, "DomainService", lambda: service)
    metadata = {"cross_board_card": McpTool.get_tool("cross_board_card")}
    monkeypatch.setattr(McpTool, "get_tools", lambda: metadata)
    monkeypatch.setattr(McpTool, "get_tool", lambda name: metadata.get(name))
    _, server = McpServer.get_http_app("compatibility")
    token = mcp_auth_context.set(
        {"user_or_bot": b.actor, "tool_group": SimpleNamespace(activated_at=object(), tools=["cross_board_card"])}
    )
    args = {
        "source_project_uid": b.source_board.get_uid(),
        "source_card_uid": b.source.get_uid(),
        "destination_project_uid": b.destination.get_uid(),
        "destination_column_uid": b.column.get_uid(),
        "operation": "move",
        "request_uid": b.request_uid,
    }
    try:
        async with Client(server) as client:
            tools = await client.list_tools()
            native = next(tool for tool in tools if tool.name == "cross_board_card")
            assert "user" not in native.inputSchema["properties"]
            assert native.outputSchema is not None
            for invalid in (
                {"operation": "delete"},
                {"request_uid": "invalid"},
                {"confirmed": "true"},
                {"source_card_uid": "../private"},
            ):
                rejected = await client.call_tool("cross_board_card", {**args, **invalid}, raise_on_error=False)
                assert rejected.is_error
            assert len(rows(Card)) == 1 and rows(ProjectActivity) == []
            result = await client.call_tool("cross_board_card", args)
            assert result.structured_content["card_uid"] == b.source.get_uid() and result.structured_content["changed"]
            replay = await client.call_tool("cross_board_card", args)
            assert not replay.structured_content["changed"] and len(rows(ProjectActivity)) == 2
            with DbSession.use(readonly=False) as db:
                db.exec(update(ProjectRole).where(ProjectRole.project_id == b.source_board.id).values(actions=["read"]))
            denied = await client.call_tool("cross_board_card", args, raise_on_error=False)
            assert denied.is_error and "Secret source title" not in str(denied)
            assert len(rows(ProjectActivity)) == 2
    finally:
        mcp_auth_context.reset(token)


@pytest.mark.parametrize("operation", ["clone", "move"])
@pytest.mark.parametrize("profile", ["raw", "oauth"])
async def test_modern_confirmation_receipt_and_applied_replay(move_board, monkeypatch, operation, profile):
    import importlib
    from fastmcp import FastMCP
    from langboard.mcp_integration.Providers import create_native_agent_provider
    from langboard.mcp_integration.Receipts import MutationReceiptMiddleware
    from langboard.mcp_integration.Server import McpServer
    from langboard.middlewares.McpAuthMiddleware import mcp_auth_context

    dependencies = importlib.import_module("langboard.mcp_integration.Dependencies")
    server_module = importlib.import_module("langboard.mcp_integration.Server")
    b = move_board
    add_reader(b)
    service = SimpleNamespace(card=b.service, close=lambda: None)

    @asynccontextmanager
    async def resource():
        yield service

    monkeypatch.setattr(dependencies, "_factory_dependency", lambda _: resource)
    monkeypatch.setattr(server_module, "DomainService", lambda: service)
    metadata = {"cross_board_card": McpTool.get_tool("cross_board_card")}
    monkeypatch.setattr(McpTool, "get_tools", lambda: metadata)
    monkeypatch.setattr(McpTool, "get_tool", lambda name: metadata.get(name))
    if profile == "oauth":
        from langboard_shared.domain.models import EmployeeMembershipPolicy, NativeMcpBoardConsent, NativeMcpConnection
        from langboard_shared.Env import Env

        # External OAuth users require a persisted client connection and current
        # board consent in addition to the ordinary cross-board role checks.
        for model in (EmployeeMembershipPolicy, NativeMcpConnection, NativeMcpBoardConsent):
            model.__table__.create(b.engine)
        monkeypatch.setattr(type(Env), "CARD_INTERNAL_ACCESS_MODE", property(lambda _: "project_members"))
        monkeypatch.setattr(type(Env), "MCP_EMPLOYEE_GROUP_IDS", property(lambda _: []))
        with DbSession.use(readonly=False) as db:
            connection = NativeMcpConnection(user_id=b.actor.id, client_id="cross-board-replay-test")
            db.insert(connection)
            for board in (b.source_board, b.destination):
                db.insert(NativeMcpBoardConsent(connection_id=connection.id, project_id=board.id, scopes=["read", "write"]))
        server = FastMCP("Native OAuth dispatch fixture", middleware=[MutationReceiptMiddleware()])
        server.add_provider(create_native_agent_provider(McpServer._wrap_tool))
    else:
        _, server = McpServer.get_http_app(profile)
    # Exercise the native OAuth principal path without a legacy tool group.
    principal = {"transport": "oauth", "user_or_bot": b.actor, "native_connection_id": connection.id} if profile == "oauth" else {
        "user_or_bot": b.actor,
        "tool_group": SimpleNamespace(activated_at=object(), tools=["cross_board_card"]),
    }
    token = mcp_auth_context.set(principal)
    args = {
        "source_project_uid": b.source_board.get_uid(),
        "source_card_uid": b.source.get_uid(),
        "destination_project_uid": b.destination.get_uid(),
        "destination_column_uid": b.column.get_uid(),
        "operation": operation,
        "request_uid": b.request_uid,
    }

    async def call(client, arguments):
        return await client.call_tool(
            "call_raw_tool" if profile == "raw" else "cross_board_card",
            {"name": "cross_board_card", "arguments": arguments} if profile == "raw" else arguments,
        )

    try:
        async with Client(server) as client:
            preview = await call(client, args)
            data = preview.structured_content
            assert data["status"] == "confirmation_required" and data["changed"] is False
            assert data["operation"] == operation and data["new_reader_count"] == 1
            assert len(rows(Card)) == 1 and rows(Card)[0].project_id == b.source_board.id
            assert rows(ProjectActivity) == []
            receipt = preview.meta["mutation_receipt"]
            assert receipt["tool"] == "cross_board_card"
            assert receipt["outcome"] == "not_applied" and receipt["next_action"] == "read_and_review"
            assert receipt["revision_conflict"] is False and receipt["retryable"] is False
            confirmed = {**args, "confirmed": True, "confirmation_token": data["confirmation_token"]}
            applied = await call(client, confirmed)
            assert applied.structured_content["status"] == "applied" and applied.structured_content["changed"]
            replay = await call(client, confirmed)
            assert replay.structured_content["status"] == "unchanged"
            assert replay.structured_content["changed"] is False
            for result in (applied, replay):
                assert result.meta["mutation_receipt"]["outcome"] == "applied"
                assert result.meta["mutation_receipt"]["next_action"] == "none"
            assert len(rows(Card)) == (1 if operation == "move" else 2)
            assert len(rows(ProjectActivity)) == (2 if operation == "move" else 1)
    finally:
        mcp_auth_context.reset(token)
