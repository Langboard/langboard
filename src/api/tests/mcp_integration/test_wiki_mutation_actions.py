"""Native wiki mutations keep revision gates and canonical authorization."""

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from langboard.mcp_integration import Dependencies
from langboard.mcp_integration.Providers import create_native_agent_provider
from langboard.mcp_integration.Receipts import MutationReceiptMiddleware
from langboard.mcp_integration.Server import McpServer
from langboard.mcp_integration.Tool import McpTool
from langboard.mcp_tools import WikiActionsMcp as actions
from langboard.mcp_tools.RoleChecker import McpRoleChecker
from langboard.mcp_tools.WikiActionsMcp import WIKI_ACTION_COMMANDS, WikiMutation, update_wiki
from langboard.middlewares.McpAuthMiddleware import mcp_auth_context
from langboard_shared.core.exceptions.WikiContentConflict import WikiContentConflict
from pydantic import TypeAdapter, ValidationError
from src.api.tests.mcp_integration.test_native_board_consent import native, native_engine  # noqa: F401
from src.api.tests.secrets.test_wiki_selection_secret import conversion  # noqa: F401


@pytest.mark.parametrize("denied", [False, True])
@pytest.mark.parametrize(
    "change",
    [
        {"action": "append", "text": "  한글\n"},
        {"action": "patch", "edits": [{"old_text": "  원문", "new_text": "  수정"}]},
        {"action": "replace", "content": ""},
        {"action": "delete"},
    ],
)
async def test_native_wiki_mutation_dispatch_receipt_and_conflict(monkeypatch, internal_mcp_actor, denied, change):
    original = McpTool.get_tool("update_wiki")
    calls = []
    conflict = False
    output = {"deleted": True} if change["action"] == "delete" else {"wiki_uid": "wiki", "revision": "b" * 64}

    service = object()
    repository = object()
    monkeypatch.setattr(Dependencies, "_factory_dependency", lambda factory: lambda: service)
    monkeypatch.setattr(McpRoleChecker, "check_permission", lambda self, handler, actor, args: not denied)

    def native_repository(actor, current_service):
        assert actor is internal_mcp_actor and current_service is service
        return repository

    monkeypatch.setattr(actions, "NativeWikiRepository", native_repository)

    def native_owner(repo, project_uid, wiki_uid, expected_revision, *values):
        assert repo is repository
        kwargs = {"project_uid": project_uid, "wiki_uid": wiki_uid, "expected_revision": expected_revision}
        if values:
            key = {"append": "text", "patch": "edits", "replace": "content"}[change["action"]]
            kwargs[key] = (
                [{"old_text": old, "new_text": new} for old, new in values[0]] if key == "edits" else values[0]
            )
        calls.append(kwargs)
        if conflict:
            raise WikiContentConflict("stale reviewed revision")
        return output

    for name in ("append_wiki", "patch_wiki", "replace_wiki", "delete_wiki"):
        monkeypatch.setattr(actions, name, native_owner)

    def command(**kwargs):
        raise AssertionError("Primitive wiki mutation handlers must not execute")

    metadata = {"handler": command, "exclude": [], "accessible_type": "user"}
    target = WIKI_ACTION_COMMANDS[change["action"]]
    monkeypatch.setattr(McpTool, "get_tools", lambda: {"update_wiki": original})
    monkeypatch.setattr(
        McpTool, "get_tool", lambda name: original if name == "update_wiki" else metadata if name == target else None
    )
    monkeypatch.setattr(
        McpServer, "_validate_role", lambda actor, handler, **kwargs: not denied or handler is update_wiki
    )
    server = FastMCP("Native wiki mutation fixture", middleware=[MutationReceiptMiddleware()])
    server.add_provider(create_native_agent_provider(McpServer._wrap_tool))
    token = mcp_auth_context.set({"transport": "oauth", "user_or_bot": internal_mcp_actor})
    try:
        async with Client(server) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
            assert not tools["update_wiki"].annotations.read_only_hint
            action_schemas = tools["update_wiki"].input_schema["properties"]["change"]["oneOf"]
            assert {item["properties"]["action"]["const"] for item in action_schemas} == {
                "append",
                "patch",
                "replace",
                "delete",
                "secret_selection",
            }
            args = {"project_uid": "project", "wiki_uid": "wiki", "expected_revision": "a" * 64, "change": change}
            if denied:
                with pytest.raises(ToolError, match="Insufficient permissions"):
                    await client.call_tool("update_wiki", args)
                assert not calls
            else:
                result = await client.call_tool("update_wiki", args)
                assert result.structured_content == output
                assert result.meta["mutation_receipt"]["outcome"] == "applied"
                assert calls[-1] == {
                    "project_uid": "project",
                    "wiki_uid": "wiki",
                    "expected_revision": "a" * 64,
                    **{k: v for k, v in change.items() if k != "action"},
                }
                conflict = True
                result = await client.call_tool("update_wiki", args, raise_on_error=False)
                assert result.is_error
                assert result.meta["mutation_receipt"]["outcome"] == "not_applied"
                assert result.meta["mutation_receipt"]["revision_conflict"] is True
                assert result.meta["mutation_receipt"]["retryable"] is False
    finally:
        mcp_auth_context.reset(token)


@pytest.mark.parametrize(
    "change",
    [
        {"action": "append"},
        {"action": "append", "text": "x", "content": "y"},
        {"action": "patch", "edits": []},
        {"action": "patch", "edits": [{"old_text": "", "new_text": "x"}]},
        {"action": "replace"},
        {"action": "delete", "content": ""},
    ],
)
def test_invalid_wiki_changes_rejected(change):
    with pytest.raises(ValidationError):
        TypeAdapter(WikiMutation).validate_python(change)


@pytest.mark.parametrize("draft_state", ["idle", "active", "unknown"])
def test_facade_secret_selection_uses_native_owner_and_collaboration_guard(conversion, monkeypatch, draft_state):  # noqa: F811
    from types import SimpleNamespace
    from fastmcp.exceptions import ValidationError as NativeValidationError
    from langboard.middlewares.CollaborativeEditMiddleware import CollaborativeEditMiddleware
    from langboard.wiki_workspace.domain import WikiSnapshot
    from src.api.tests.secrets.test_wiki_selection_secret import state

    owner, board, _, wiki, source, selection, material, _ = conversion
    calls = []

    def coordination(headers, names):
        calls.append(names)
        return [] if draft_state == "idle" else [names[0]] if draft_state == "active" else None

    monkeypatch.setattr(CollaborativeEditMiddleware, "_get_active_collaborative_document_names", coordination)
    monkeypatch.setattr(McpServer, "_wrap_tool", lambda *args: pytest.fail("Nested MCP execution"))
    change = TypeAdapter(WikiMutation).validate_python(
        {
            "action": "secret_selection",
            "name": "wiki/facade-selection",
            "selection": selection,
        }
    )
    args = (
        board[2].get_uid(),
        wiki.get_uid(),
        WikiSnapshot("w", "t", source).revision,
        change,
        board[1],
        SimpleNamespace(secret_reference=owner),
    )
    if draft_state == "idle":
        result = update_wiki(*args).model_dump(mode="json")
        assert result["editor_generation"] == 1
        assert result["reference"]["state"] == "active"
        assert "fixture-sensitive-value" not in str(result)
        assert "fixture-sensitive-value" in material.values()
        assert "fixture-sensitive-value" not in state(conversion)[0]
    else:
        with pytest.raises(
            NativeValidationError, match="collaboration_" + ("active" if draft_state == "active" else "unavailable")
        ):
            update_wiki(*args)
        assert not material
        assert state(conversion)[0] == source
    assert len(calls) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"action": "append", "text": "  x\n"},
        {"action": "patch", "edits": [{"old_text": "x", "new_text": "y"}]},
        {"action": "replace", "content": ""},
        {"action": "delete"},
    ],
)
@pytest.mark.parametrize("error_type", [ValueError, WikiContentConflict])
def test_native_wiki_error_translation_preserves_legacy_boundary(monkeypatch, change, error_type):
    from fastmcp.exceptions import ValidationError as NativeValidationError

    error = error_type("native owner rejection")

    def rejected(*args):
        raise error

    monkeypatch.setattr(actions, "_authorize_command", lambda *args: None)
    monkeypatch.setattr(actions, "NativeWikiRepository", lambda *args: object())
    for name in ("append_wiki", "patch_wiki", "replace_wiki", "delete_wiki"):
        monkeypatch.setattr(actions, name, rejected)
    expected_type = ValueError if change["action"] == "append" and error_type is ValueError else NativeValidationError
    with pytest.raises(expected_type, match="native owner rejection") as raised:
        update_wiki("project", "wiki", "a" * 64, TypeAdapter(WikiMutation).validate_python(change), object(), object())
    assert type(raised.value) is expected_type
    if expected_type is NativeValidationError:
        assert raised.value.__cause__ is error


async def test_external_typed_wiki_selection_denied_with_valid_append_consent(native, monkeypatch):  # noqa: F811
    from langboard_shared.domain.models import Project
    from langboard_shared.domain.services import DomainService
    from langboard_shared.domain.services.NativeMcpBoundary import native_mcp_boundary
    from langboard_shared.domain.services.NativeMcpConsent import connect_native_client, save_native_board_consent
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    engine, user, _, boards = native
    connection = connect_native_client(user, "typed-wiki-external-owner")
    save_native_board_consent(user, connection.id, 10, ["read", "write"], None)
    calls = []

    def repository(actor, service):
        assert actor is user and isinstance(service, DomainService)
        return service

    def append(service, project_uid, wiki_uid, expected_revision, text):
        scope = native_mcp_boundary.get()
        assert scope["actor_id"] == user.id
        assert scope["connection_id"] == connection.id
        assert scope["read"] == scope["write"] == frozenset({10})
        with Session(engine) as db:
            assert list(db.scalars(select(Project.id))) == [10]
        calls.append((service, text))
        return {"wiki_uid": wiki_uid, "revision": "b" * 64}

    monkeypatch.setattr(actions, "NativeWikiRepository", repository)
    monkeypatch.setattr(actions, "append_wiki", append)
    monkeypatch.setattr(
        DomainService, "secret_reference", property(lambda self: pytest.fail("External secret owner reached"))
    )
    original = McpTool.get_tool("update_wiki")
    monkeypatch.setattr(McpTool, "get_tools", lambda: {"update_wiki": original})
    server = FastMCP("External typed wiki consent")
    server.add_provider(create_native_agent_provider(McpServer._wrap_tool))
    auth_token = mcp_auth_context.set(
        {"transport": "oauth", "user_or_bot": user, "native_connection_id": connection.id}
    )
    try:
        async with Client(server) as client:
            args = {"project_uid": boards[0].get_uid(), "wiki_uid": "wiki", "expected_revision": "a" * 64}
            result = await client.call_tool(
                "update_wiki", {**args, "change": {"action": "append", "text": "  append\n"}}
            )
            assert result.structured_content == {"wiki_uid": "wiki", "revision": "b" * 64}
            assert len(calls) == 1 and calls[0][1] == "  append\n"
            result = await client.call_tool(
                "update_wiki",
                {
                    **args,
                    "change": {
                        "action": "secret_selection",
                        "name": "wiki/external",
                        "selection": {"start_utf16": 0, "end_utf16": 1},
                    },
                },
                raise_on_error=False,
            )
            assert result.is_error
            assert "cannot currently preserve external native board consent" in result.content[0].text
            assert len(calls) == 1
            assert "fixture-sensitive-value" not in str(result)
        assert native_mcp_boundary.get() is None
    finally:
        mcp_auth_context.reset(auth_token)
