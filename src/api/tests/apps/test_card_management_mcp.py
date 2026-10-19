# ruff: noqa: F811
"""Native MCP forwards card management to existing owners and conservative receipts."""

from secrets import token_hex
from types import SimpleNamespace
import pytest
from fastmcp import Client
from langboard.mcp_integration import McpTool
from langboard.mcp_integration.Annotations import tool_annotations
from langboard.mcp_integration.Providers import AGENT_CORE_TOOLS, create_native_domain_provider
from langboard.mcp_integration.Receipts import MutationReceiptMiddleware
from langboard.mcp_integration.Server import McpServer, _create_fastmcp
from langboard.mcp_tools import CardManagementMcp
from langboard.middlewares.McpAuthMiddleware import mcp_auth_context
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.domain.models import (
    AppCardInteraction,
    AppCardInteractionResponse,
    AppSignal,
    EmployeeMembershipPolicy,
)
from langboard_shared.domain.services.AppExecutionLeases import authorize_app_runtime
from mcp.shared.exceptions import MCPError
from test_app_execution_lease import permit_scope
from test_card_signal_projection import scoped  # noqa: F401
from test_github_signal import installation, lifecycle, send, signal_storage  # noqa: F401


READS = {
    "get_card_signals",
    "list_card_signal_resources",
    "list_card_app_requests",
    "list_card_app_executions",
    "list_card_signal_candidates",
}
WRITES = {"attach_card_signal", "unlink_card_signal", "request_card_app_execution_stop"}


def server():
    app = _create_fastmcp()
    app.add_middleware(MutationReceiptMiddleware())
    app.add_provider(create_native_domain_provider(McpServer._wrap_tool, modern_annotations=True))
    return app


def test_management_schema_is_bounded_user_only_and_optional():
    for name in READS | WRITES:
        tool = McpTool.get_tool(name)
        assert tool["accessible_type"] == "user"
        assert not {"user", "service", "actor", "token"}.intersection(tool["input_schema"]["properties"])
        assert tool_annotations(name).read_only_hint is (name in READS)
        assert name not in AGENT_CORE_TOOLS
    request_schema = McpTool.get_tool("list_card_app_requests")["input_schema"]
    assert request_schema["properties"]["limit"]["maximum"] == 25
    assert "expected_revision" in McpTool.get_tool("attach_card_signal")["input_schema"]["required"]
    assert "source_change_seq" in McpTool.get_tool("attach_card_signal")["input_schema"]["required"]
    assert "generation" in McpTool.get_tool("request_card_app_execution_stop")["input_schema"]["required"]
    assert not any(
        McpTool.get_tool(name)
        for name in (
            "approve_card_app_request",
            "respond_to_card_app_request",
            "resume_card_app_execution",
            "start_card_app_execution",
        )
    )


async def test_native_signal_management_permissions_fences_and_revocation(scoped, monkeypatch):
    state, card, binding, _ = scoped
    board = state[1]
    monkeypatch.setattr(DbEngine, "get_readonly_engine", DbEngine.get_main_engine)
    with DbSession.atomic() as db:
        board[4].actions = ["read", "update", "card_update"]
        db.update(board[4])
    send(state)
    with DbSession.use(readonly=False) as db:
        signal = db.exec(SqlBuilder.select.table(AppSignal)).first()
    group = SimpleNamespace(activated_at=True, tools=sorted(READS | WRITES))
    token = mcp_auth_context.set({"user_or_bot": board[1], "tool_group": group})
    scope = {"project_uid": board[2].get_uid(), "card_uid": card.get_uid()}
    attach = {
        **scope,
        "connection_uid": state[2].get_uid(),
        "resource_uid": state[4].get_uid(),
        "signal_uid": signal.get_uid(),
        "source_change_seq": card.last_change_seq,
        "expected_revision": 1,
    }
    try:
        async with Client(server()) as client:
            assert {tool.name for tool in await client.list_tools()} == READS | WRITES
            read = await client.call_tool("get_card_signals", scope)
            assert read.structured_content["items"][0]["signal_uid"] == signal.get_uid()
            assert "mutation_receipt" not in (read.meta or {})
            resources = await client.call_tool("list_card_signal_resources", scope)
            assert resources.structured_content["items"][0]["uid"] == state[4].get_uid()
            unlinked = await client.call_tool(
                "unlink_card_signal", {**scope, "binding_uid": binding.get_uid(), "expected_revision": 0}
            )
            assert unlinked.structured_content["revision"] == 1
            candidates = await client.call_tool("list_card_signal_candidates", {**scope, "limit": 1})
            candidate = candidates.structured_content["items"][0]
            assert candidate["signal_uid"] == signal.get_uid()
            assert candidate["resource_uid"] == state[4].get_uid()
            assert candidate["connection_uid"] == state[2].get_uid()
            assert (
                await client.call_tool("list_card_signal_candidates", {**scope, "limit": 26}, raise_on_error=False)
            ).is_error
            assert unlinked.meta["mutation_receipt"]["outcome"] == "applied"
            stale = await client.call_tool(
                "attach_card_signal", {**attach, "source_change_seq": card.last_change_seq + 1}, raise_on_error=False
            )
            assert stale.is_error and stale.meta["mutation_receipt"]["outcome"] == "unknown"
            saved = await client.call_tool("attach_card_signal", attach)
            assert saved.structured_content["revision"] == 2
            assert (await client.call_tool("get_card_signals", scope)).structured_content["bindings"][0][
                "revision"
            ] == 2
            # The genuine owner rejects missing card_update and current resource consent.
            with DbSession.atomic() as db:
                board[4].actions = ["read", "update"]
                db.update(board[4])
            denied = await client.call_tool(
                "unlink_card_signal",
                {**scope, "binding_uid": binding.get_uid(), "expected_revision": 2},
                raise_on_error=False,
            )
            assert denied.is_error
            with DbSession.atomic() as db:
                board[4].actions = ["read", "update", "card_update"]
                state[4].is_selected = False
                db.update(board[4])
                db.update(state[4])
            assert (
                await client.call_tool("attach_card_signal", {**attach, "expected_revision": 2}, raise_on_error=False)
            ).is_error
            assert (await client.call_tool("list_card_signal_candidates", scope)).structured_content["items"] == []
            with DbSession.atomic() as db:
                card.visibility = "PRIVATE"
                card.owner_user_id = card.created_by_user_id = 2
                db.update(card)
            assert (await client.call_tool("get_card_signals", scope, raise_on_error=False)).is_error
            assert (await client.call_tool("list_card_signal_candidates", scope, raise_on_error=False)).is_error
            group.activated_at = None
            with pytest.raises(MCPError):
                await client.list_tools()
            assert (await client.call_tool("get_card_signals", scope, raise_on_error=False)).is_error
            group.activated_at = True
            group.tools = []
            assert await client.list_tools() == []
            assert (await client.call_tool("get_card_signals", scope, raise_on_error=False)).is_error
    finally:
        mcp_auth_context.reset(token)


@pytest.mark.parametrize("board", ["sqlite-http", "postgresql-test"], indirect=True)
async def test_native_retained_histories_stop_and_unknown_write(board, monkeypatch):
    connection, card, app_token, execution, ack_id = permit_scope(board, monkeypatch)
    engine = DbEngine.get_main_engine()
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    for model in (EmployeeMembershipPolicy, AppCardInteraction, AppCardInteractionResponse):
        model.__table__.create(engine, checkfirst=True)
    authorize_app_runtime(app_token, board[2].id, card.id, execution.id, ack_id, token_hex(32))
    with DbSession.atomic() as db:
        # Retained cancelled request remains readable after connection revocation.
        interaction = AppCardInteraction(
            project_id=board[2].id,
            card_id=card.id,
            connection_id=connection.id,
            app_key=connection.app_key,
            app_request_key="retained",
            state="cancelled",
            request={"prompt": {"en": "Retained question"}},
        )
        db.insert(interaction)
        board[4].actions = ["read"]
        board[2].owner_id = 2
        connection.state = "revoked"
        for row in (board[4], board[2], connection):
            db.update(row)
    group = SimpleNamespace(activated_at=True, tools=sorted(READS | WRITES))
    auth = mcp_auth_context.set({"user_or_bot": board[1], "tool_group": group})
    scope = {"project_uid": board[2].get_uid(), "card_uid": card.get_uid()}
    stop = {**scope, "request_uid": execution.get_uid(), "generation": execution.generation}
    try:
        async with Client(server()) as client:
            for invalid in ({"limit": 26}, {"cursor": "invalid-cursor"}, {"user": "spoofed"}):
                assert (
                    await client.call_tool("list_card_app_requests", {**scope, **invalid}, raise_on_error=False)
                ).is_error
            requests = await client.call_tool("list_card_app_requests", {**scope, "limit": 1})
            assert requests.structured_content["items"][0]["interaction_uid"] == interaction.get_uid()
            assert requests.structured_content["items"][0]["state"] == "cancelled"
            runs = await client.call_tool("list_card_app_executions", scope)
            assert runs.structured_content["items"][0]["request_uid"] == execution.get_uid()
            assert "token_hash" not in str(runs) and "runtime_token" not in str(runs)
            assert (await client.call_tool("request_card_app_execution_stop", stop, raise_on_error=False)).is_error
            with DbSession.atomic() as db:
                board[4].actions = ["read", "card_update"]
                db.update(board[4])
            assert (
                await client.call_tool(
                    "request_card_app_execution_stop",
                    {**stop, "generation": execution.generation + 1},
                    raise_on_error=False,
                )
            ).is_error
            original = CardManagementMcp.request_human_runtime_stop

            def fail_after_save(*args, **kwargs):
                original(*args, **kwargs)
                raise RuntimeError("private downstream failure after saved stop")

            monkeypatch.setattr(CardManagementMcp, "request_human_runtime_stop", fail_after_save)
            unknown = await client.call_tool("request_card_app_execution_stop", stop, raise_on_error=False)
            assert unknown.is_error and unknown.meta["mutation_receipt"]["outcome"] == "unknown"
            assert "private downstream" not in str(unknown)
            readback = await client.call_tool("list_card_app_executions", scope)
            assert readback.structured_content["items"][0]["state"] == "stop_requested"
            monkeypatch.setattr(CardManagementMcp, "request_human_runtime_stop", original)
            replay = await client.call_tool("request_card_app_execution_stop", stop)
            assert replay.structured_content["changed"] is False
            assert replay.structured_content["permit_execution"] is False
            assert replay.structured_content["execution_resumed"] is False
            with DbSession.atomic() as db:
                card.visibility = "PRIVATE"
                card.owner_user_id = card.created_by_user_id = 2
                db.update(card)
            for name in ("list_card_app_requests", "list_card_app_executions", "request_card_app_execution_stop"):
                assert (
                    await client.call_tool(
                        name, stop if name == "request_card_app_execution_stop" else scope, raise_on_error=False
                    )
                ).is_error
    finally:
        mcp_auth_context.reset(auth)
