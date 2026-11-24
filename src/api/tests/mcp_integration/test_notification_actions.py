"""Native notification scope rejects ambiguity before dispatch."""

from types import SimpleNamespace
import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from langboard.mcp_integration import Dependencies
from langboard.mcp_integration.Providers import create_agent_core_provider
from langboard.mcp_integration.Server import McpServer
from langboard.mcp_integration.Tool import McpTool
from langboard.mcp_tools.NotificationActionsMcp import NOTIFICATION_READ_COMMANDS, NotificationReadChange
from langboard.mcp_tools.RoleChecker import McpRoleChecker
from langboard.middlewares.McpAuthMiddleware import mcp_auth_context
from pydantic import TypeAdapter, ValidationError


@pytest.mark.parametrize("change", [{"scope": "one", "notification_uid": " owned "}, {"scope": "all"}])
@pytest.mark.parametrize("denied", [False, True])
async def test_native_notification_scope_dispatch(monkeypatch, internal_mcp_actor, change, denied):
    original = McpTool.get_tool("mark_notifications_read")
    calls = []

    def command(**kwargs):
        raise AssertionError("Primitive notification handlers must not execute")

    service = SimpleNamespace(
        notification=SimpleNamespace(
            read=lambda actor, uid: calls.append((actor, uid)) or True,
            read_all=lambda actor: calls.append((actor,)),
        ),
        close=lambda: None,
    )
    monkeypatch.setattr(Dependencies, "_factory_dependency", lambda factory: lambda: service)
    monkeypatch.setattr(McpRoleChecker, "check_permission", lambda self, handler, actor, args: not denied)

    target = NOTIFICATION_READ_COMMANDS[change["scope"]]
    metadata = {"handler": command, "exclude": [], "accessible_type": "user"}
    monkeypatch.setattr(McpTool, "get_tools", lambda: {"mark_notifications_read": original})
    monkeypatch.setattr(
        McpTool,
        "get_tool",
        lambda name: original if name == "mark_notifications_read" else metadata if name == target else None,
    )
    monkeypatch.setattr(
        McpServer, "_validate_role", lambda actor, handler, **kwargs: not denied or handler is original["handler"]
    )
    server = FastMCP("Notification scope fixture")
    server.add_provider(create_agent_core_provider(McpServer._wrap_tool))
    token = mcp_auth_context.set({"transport": "oauth", "user_or_bot": internal_mcp_actor})
    try:
        async with Client(server) as client:
            tool = (await client.list_tools())[0]
            assert tool.name == "mark_notifications_read"
            assert len(tool.input_schema["properties"]["change"]["oneOf"]) == 2
            assert tool.output_schema["properties"]["read"]["const"] is True
            if denied:
                with pytest.raises(ToolError, match="Insufficient permissions"):
                    await client.call_tool(tool.name, {"change": change})
                assert calls == []
            else:
                result = await client.call_tool(tool.name, {"change": change})
                assert result.structured_content == {"read": True}
                assert calls == (
                    [(internal_mcp_actor, "owned")] if change["scope"] == "one" else [(internal_mcp_actor,)]
                )
    finally:
        mcp_auth_context.reset(token)


@pytest.mark.parametrize(
    "change",
    [
        {},
        {"scope": "one"},
        {"scope": "one", "notification_uid": " "},
        {"scope": "all", "notification_uid": "owned"},
        {"scope": "all", "project_uid": "project"},
        {"scope": "both", "notification_uid": "owned"},
    ],
)
def test_invalid_notification_scope_never_dispatches(change):
    with pytest.raises(ValidationError):
        TypeAdapter(NotificationReadChange).validate_python(change)


@pytest.mark.parametrize("scope", ["one", "all"])
async def test_notification_native_owner_error_and_identity(monkeypatch, scope):
    from langboard.mcp_tools import NotificationActionsMcp as actions

    actor = object()
    calls = []

    def read(user, *args):
        calls.append((user, args))
        return False

    service = SimpleNamespace(notification=SimpleNamespace(read=read, read_all=read))
    monkeypatch.setattr(McpServer, "_validate_auth", lambda user, command: user is actor)
    monkeypatch.setattr(McpRoleChecker, "check_permission", lambda self, handler, user, args: self.service is service)
    monkeypatch.setattr(McpServer, "_wrap_tool", lambda *args: pytest.fail("Nested MCP invocation"))
    change = TypeAdapter(NotificationReadChange).validate_python(
        {"scope": scope, **({"notification_uid": "owned"} if scope == "one" else {})}
    )
    if scope == "one":
        with pytest.raises(ValueError, match="^Notification not found$"):
            actions.mark_notifications_read(change, actor, service)
    else:
        assert (actions.mark_notifications_read(change, actor, service)).read is True
    assert calls == [(actor, ("owned",) if scope == "one" else ())]


@pytest.mark.parametrize("tool_name", ["mark_notifications_read", "read_wiki", "update_wiki"])
@pytest.mark.parametrize("outcome", ["success", "auth_denied", "role_denied", "owner_error"])
async def test_native_actor_service_lifetime_and_primitive_authority(
    monkeypatch, internal_mcp_actor, tool_name, outcome
):
    from contextlib import asynccontextmanager, nullcontext
    from importlib import import_module
    from langboard.mcp_integration import NativeConsent
    from langboard.mcp_integration.ExecutionHistory import execution_scope
    from langboard.mcp_integration.Providers import create_native_agent_provider
    from langboard.mcp_tools import NotificationActionsMcp, UserMcp, WikiActionsMcp  # noqa: F401
    from langboard_shared.domain.services import DomainService

    # Isolate owner DI from the separately tested consent/history boundaries.
    @asynccontextmanager
    async def boundary(*args):
        yield

    monkeypatch.setattr(NativeConsent, "native_call_boundary", boundary)
    monkeypatch.setattr(import_module("langboard.mcp_integration.Server"), "safe_scope", lambda *args: None)
    metadata = McpTool.get_tool(tool_name)
    primitives = {
        "mark_notifications_read": "mark_notification_read",
        "read_wiki": "read_wiki_content",
        "update_wiki": "append_wiki_content",
    }
    primitive = primitives[tool_name]
    created, closed, permission, owners, scoped = [], [], [], [], []

    def primitive_trap(**kwargs):
        pytest.fail("Primitive business invocation")

    primitive_metadata = {**McpTool.get_tool(primitive), "handler": primitive_trap}
    get_tool = McpTool.get_tool
    monkeypatch.setattr(McpTool, "get_tools", lambda: {tool_name: metadata})
    monkeypatch.setattr(McpTool, "get_tool", lambda name: primitive_metadata if name == primitive else get_tool(name))
    monkeypatch.setattr(
        McpServer,
        "_validate_auth",
        lambda actor, name: actor is internal_mcp_actor and (outcome != "auth_denied" or name == tool_name),
    )
    monkeypatch.setattr(McpServer, "_validate_role", lambda *args, **kwargs: True)
    monkeypatch.setattr(DomainService, "__init__", lambda self: created.append(self) and None)
    monkeypatch.setattr(DomainService, "close", lambda self: closed.append(self))

    def check(self, handler, actor, args):
        permission.append((self.service, actor, handler))
        return outcome != "role_denied"

    monkeypatch.setattr(McpRoleChecker, "check_permission", check)

    def scope(actor, handler, args):
        scoped.append((actor, handler, args))
        return {"native_owner": tool_name}

    module = NotificationActionsMcp if tool_name == "mark_notifications_read" else WikiActionsMcp
    monkeypatch.setattr(module, "safe_scope", scope)
    monkeypatch.setattr(module, "native_owner_boundary", lambda *args: nullcontext())

    def owner(service, actor):
        owners.append((service, actor))
        if outcome == "owner_error":
            raise RuntimeError("native owner failed")

    def notification(self):
        return SimpleNamespace(read=lambda actor, uid: owner(self, actor) or True)

    monkeypatch.setattr(DomainService, "notification", property(notification))

    def repository(actor, service):
        assert service is created[0] and actor is internal_mcp_actor
        return (service, actor)

    monkeypatch.setattr(WikiActionsMcp, "NativeWikiRepository", repository)
    monkeypatch.setattr(WikiActionsMcp, "read_wiki_content", lambda repo, **args: owner(*repo) or {"content": "body"})
    monkeypatch.setattr(
        WikiActionsMcp, "append_wiki", lambda repo, *args: owner(*repo) or {"wiki_uid": "wiki", "revision": "b" * 64}
    )
    arguments = (
        {"change": {"scope": "one", "notification_uid": "owned"}}
        if tool_name == "mark_notifications_read"
        else {"project_uid": "project", "wiki_uid": "wiki"}
    )
    if tool_name == "update_wiki":
        arguments.update(expected_revision="a" * 64, change={"action": "append", "text": "  body\n"})
    server = FastMCP("Native owner lifetime")
    server.add_provider(create_native_agent_provider(McpServer._wrap_tool))
    actor_token = mcp_auth_context.set({"transport": "oauth", "user_or_bot": internal_mcp_actor})
    scope_value = {}
    scope_token = execution_scope.set(scope_value)
    try:
        async with Client(server) as client:
            result = await client.call_tool(tool_name, arguments, raise_on_error=False)
            assert result.is_error is (outcome != "success")
        assert len(created) == 1 and closed == created
        if outcome == "auth_denied":
            assert not permission and not scoped
        else:
            assert permission == [(created[0], internal_mcp_actor, primitive_trap)]
            assert scoped[0][:2] == (internal_mcp_actor, primitive_trap)
            assert scope_value["native_owner"] == tool_name
        assert owners == ([(created[0], internal_mcp_actor)] if outcome in {"success", "owner_error"} else [])
    finally:
        execution_scope.reset(scope_token)
        mcp_auth_context.reset(actor_token)
