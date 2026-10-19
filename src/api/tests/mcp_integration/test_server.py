import asyncio
from threading import Event, get_ident
from types import SimpleNamespace
import pytest
from fastmcp import Client
from fastmcp.exceptions import AuthorizationError, ToolError
from fastmcp.tools import Tool
from langboard.mcp_integration import Server
from langboard.mcp_integration.Server import (
    _create_fastmcp,
    _get_transport_security_allowlists,
    _reject_global_wildcards,
)
from langboard.middlewares.McpAuthMiddleware import mcp_auth_context


def test_transport_allowlists_remain_bounded() -> None:
    """FastMCP host protection never receives a global wildcard."""

    hosts, origins = _get_transport_security_allowlists()
    assert hosts
    assert origins
    assert not {"*", "http://*", "https://*"}.intersection(hosts + origins)


@pytest.mark.parametrize("value", ["*", "http://*", "https://*"])
def test_transport_global_wildcard_is_rejected(value: str) -> None:
    """Unsafe operator overrides fail closed."""

    with pytest.raises(ValueError, match="global wildcard"):
        _reject_global_wildcards([value], "MCP_ALLOWED_HOSTS")


@pytest.mark.parametrize(("mode", "expected_version"), [("legacy", "2025-11-25"), ("2026-07-28", "2026-07-28")])
async def test_fastmcp_serves_legacy_and_modern_protocols(mode: str, expected_version: str) -> None:
    """One bounded server supports current agents and legacy ContextForge clients."""

    server = _create_fastmcp()

    @server.tool
    def add(a: int, b: int) -> dict[str, int]:
        return {"total": a + b}

    token = mcp_auth_context.set({"tool_group": SimpleNamespace(activated_at=object(), tools=["add"])})
    try:
        async with Client(server, mode=mode) as client:
            assert client.protocol_version == expected_version
            assert [tool.name for tool in await client.list_tools()] == ["add"]
            result = await client.call_tool("add", {"a": 1, "b": 2})
            assert result.structured_content == {"total": 3}
    finally:
        mcp_auth_context.reset(token)


async def test_fastmcp_rejects_invalid_arguments_before_handler() -> None:
    """Schema validation is owned by FastMCP rather than handwritten dispatch code."""

    server = _create_fastmcp()
    called = False

    @server.tool
    def record(value: int) -> dict[str, int]:
        nonlocal called
        called = True
        return {"value": value}

    token = mcp_auth_context.set({"tool_group": SimpleNamespace(activated_at=object(), tools=["record"])})
    try:
        async with Client(server, mode="2026-07-28") as client:
            with pytest.raises(ToolError, match="valid integer"):
                await client.call_tool("record", {"value": "not-an-integer"})
            assert called is False
    finally:
        mcp_auth_context.reset(token)


def test_mcp_startup_failure_is_not_hidden(monkeypatch: pytest.MonkeyPatch) -> None:
    """Invalid transport configuration fails application startup visibly."""

    def fail() -> tuple[list[str], list[str]]:
        raise ValueError("invalid transport policy")

    monkeypatch.setattr(Server, "_get_transport_security_allowlists", fail)

    with pytest.raises(ValueError, match="invalid transport policy"):
        Server.McpServer.get_http_app()


async def test_native_wrapper_uses_fastmcp_authorization_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Authorization failures have the same MCP error contract on every transport."""

    async def handler(value: int) -> dict[str, int]:
        return {"value": value}

    monkeypatch.setattr(Server.McpServer, "_validate_auth", lambda actor, tool_name: False)
    wrapper = Server.McpServer._wrap_tool("blocked", handler)
    token = mcp_auth_context.set({"user_or_bot": None})
    try:
        with pytest.raises(AuthorizationError, match="Authentication required"):
            await wrapper(value=1)
    finally:
        mcp_auth_context.reset(token)


def test_native_wrapper_preserves_visible_parameter_annotations(monkeypatch: pytest.MonkeyPatch) -> None:
    """FastMCP can register the filtered wrapper used by the full application registry."""

    def handler(title: str, user_or_bot: Server.User | Server.Bot) -> dict[str, str]:
        return {"title": title}

    monkeypatch.setattr(
        Server.McpTool,
        "get_tool",
        lambda name: {"exclude": ["user_or_bot"]},
    )

    tool = Tool.from_function(Server.McpServer._wrap_tool("create", handler), name="create")

    assert tool.parameters["required"] == ["title"]
    assert tool.parameters["properties"] == {"title": {"type": "string"}}


@pytest.mark.parametrize("async_handler", [False, True])
async def test_wrapper_keeps_blocking_work_off_loop_and_preserves_actor(monkeypatch, async_handler):
    loop = asyncio.get_running_loop()
    loop_thread = get_ident()
    started = asyncio.Event()
    released = Event()
    actor = Server.User(firstname="MCP", lastname="Reader", email="mcp@example.invalid", password="test-only")
    auth = {"user_or_bot": actor}

    def authorize(current_actor, handler, **kwargs):
        assert get_ident() != loop_thread, "Synchronous role queries block the API event loop"
        assert current_actor is actor and mcp_auth_context.get() is auth
        return True

    def sync_handler(value: int, user_or_bot: Server.User) -> dict[str, int]:
        assert get_ident() != loop_thread, "Synchronous tool handlers block the API event loop"
        assert user_or_bot is actor and mcp_auth_context.get() is auth
        loop.call_soon_threadsafe(started.set)
        assert released.wait(2), "The event loop could not release the running tool"
        return {"value": value}

    async def native_async_handler(value: int, user_or_bot: Server.User) -> dict[str, int]:
        assert get_ident() == loop_thread
        assert user_or_bot is actor and mcp_auth_context.get() is auth
        return {"value": value}

    monkeypatch.setattr(Server.McpServer, "_validate_auth", lambda *args: True)
    monkeypatch.setattr(Server.McpServer, "_validate_role", authorize)
    handler = native_async_handler if async_handler else sync_handler
    wrapper = Server.McpServer._wrap_tool("test_worker", handler)
    token = mcp_auth_context.set(auth)
    task = asyncio.create_task(wrapper(value=7))
    try:
        if not async_handler:
            await asyncio.wait_for(started.wait(), timeout=1)
            assert not task.done()
            released.set()
        assert await task == {"value": 7}
        assert mcp_auth_context.get() is auth
    finally:
        released.set()
        if not task.done():
            await task
        mcp_auth_context.reset(token)


@pytest.mark.parametrize("outcome", ["success", "handler_failure", "injection_failure", "cleanup_failure", "cancel"])
async def test_native_dependency_lifecycle_through_client(monkeypatch, outcome):
    from langboard.mcp_integration import Dependencies

    allocated, closed, invoked = [], [], []
    entered, cleaned = asyncio.Event(), asyncio.Event()
    loop_thread = get_ident()

    class Resource:
        def __init__(self):
            assert get_ident() != loop_thread
            self.number = len(allocated)
            allocated.append(self)
            if outcome == "injection_failure" and self.number == 1:
                raise ValueError("allocation failed")

        def close(self):
            assert get_ident() != loop_thread
            closed.append(self)
            if len(closed) == (1 if outcome == "injection_failure" else 2):
                loop.call_soon_threadsafe(cleaned.set)
            if outcome == "cleanup_failure" and self.number == 1:
                raise ValueError("cleanup failed")

    loop = asyncio.get_running_loop()
    monkeypatch.setattr(Dependencies, "DomainService", Resource)
    actor = Server.User(firstname="MCP", lastname="Reader", email="mcp@example.invalid", password="test-only")

    async def execute(value: int, first: Resource, second: Resource, user: Server.User) -> dict[str, int]:
        assert user is actor
        assert first is not second
        invoked.append(value)
        entered.set()
        if outcome == "cancel":
            await asyncio.Event().wait()
        if outcome == "handler_failure":
            raise ValueError("handler failed")
        return {"value": value}

    metadata = {"exclude": ["first", "second", "user"], "accessible_type": "all"}
    monkeypatch.setattr(Server.McpTool, "get_tool", lambda _: metadata)
    monkeypatch.setattr(Server.McpServer, "_validate_role", lambda *args, **kwargs: True)
    server = _create_fastmcp()
    server.add_tool(Tool.from_function(Server.McpServer._wrap_tool("execute", execute), name="execute"))
    token = mcp_auth_context.set(
        {"user_or_bot": actor, "tool_group": SimpleNamespace(activated_at=object(), tools=["execute"])}
    )
    try:
        async with Client(server) as client:
            catalog = await client.list_tools()
            assert catalog[0].input_schema["properties"] == {"value": {"type": "integer"}}
            for name in metadata["exclude"]:
                with pytest.raises(ToolError):
                    await client.call_tool("execute", {"value": 1, name: "malicious"})
            assert allocated == []
            if outcome == "cancel":
                task = asyncio.create_task(client.call_tool("execute", {"value": 1}))
                await asyncio.wait_for(entered.wait(), 3)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                await asyncio.wait_for(cleaned.wait(), 3)
            elif outcome == "success":
                result = await client.call_tool("execute", {"value": 1})
                assert result.structured_content == {"value": 1}
            else:
                with pytest.raises(ToolError):
                    await client.call_tool("execute", {"value": 1})
        expected = allocated[:1] if outcome == "injection_failure" else list(reversed(allocated))
        assert closed == expected
        assert invoked == ([] if outcome == "injection_failure" else [1])
    finally:
        mcp_auth_context.reset(token)


async def test_native_concurrent_dependencies_are_owned_by_each_call(monkeypatch):
    from langboard.mcp_integration import Dependencies

    allocated, closed = [], []
    both_entered = asyncio.Event()

    class Resource:
        def __init__(self):
            allocated.append(self)

        def close(self):
            closed.append(self)

    monkeypatch.setattr(Dependencies, "Repository", Resource)
    actors = [
        Server.User(firstname=str(i), lastname="Reader", email=f"mcp{i}@example.invalid", password="test")
        for i in range(2)
    ]

    async def execute(value: int, repository: Resource, user: Server.User) -> dict[str, int]:
        assert user is actors[value]
        if len(allocated) == 2:
            both_entered.set()
        await asyncio.wait_for(both_entered.wait(), 3)
        return {"value": value}

    monkeypatch.setattr(Server.McpTool, "get_tool", lambda _: {"exclude": ["repository", "user"]})
    monkeypatch.setattr(Server.McpServer, "_validate_role", lambda *args, **kwargs: True)
    server = _create_fastmcp()
    server.add_tool(Tool.from_function(Server.McpServer._wrap_tool("execute", execute), name="execute"))

    async def call(value):
        token = mcp_auth_context.set(
            {"user_or_bot": actors[value], "tool_group": SimpleNamespace(activated_at=object(), tools=["execute"])}
        )
        try:
            async with Client(server) as client:
                result = await client.call_tool("execute", {"value": value})
                assert result.structured_content == {"value": value}
        finally:
            mcp_auth_context.reset(token)

    await asyncio.gather(call(0), call(1))
    assert len(allocated) == 2 and allocated[0] is not allocated[1]
    assert set(closed) == set(allocated)


@pytest.mark.parametrize(
    "annotation", [Server.User, Server.User | None, Server.Bot, Server.Bot | None, Server.User | Server.Bot]
)
@pytest.mark.parametrize("actor_kind", ["user", "bot", "missing"])
async def test_native_actor_dependencies_preserve_restrictions(monkeypatch, annotation, actor_kind):
    actor = (
        Server.User(firstname="MCP", lastname="Reader", email="mcp@example.invalid", password="test")
        if actor_kind == "user"
        else Server.Bot(
            name="MCP", bot_uname="mcp", app_api_token="test", platform="default", platform_running_type="default"
        )
        if actor_kind == "bot"
        else None
    )
    invoked = []

    async def execute(current_actor) -> dict[str, bool]:
        invoked.append(current_actor)
        return {"ok": True}

    execute.__annotations__["current_actor"] = annotation
    monkeypatch.setattr(Server.McpTool, "get_tool", lambda _: {"exclude": ["current_actor"]})
    monkeypatch.setattr(Server.McpServer, "_validate_role", lambda *args, **kwargs: True)
    server = _create_fastmcp()
    server.add_tool(Tool.from_function(Server.McpServer._wrap_tool("execute", execute), name="execute"))
    token = mcp_auth_context.set(
        {"user_or_bot": actor, "tool_group": SimpleNamespace(activated_at=object(), tools=["execute"])}
    )
    try:
        async with Client(server) as client:
            from typing import get_args

            allowed = {item for item in get_args(annotation) if item is not type(None)} or {annotation}
            if type(actor) in allowed:
                await client.call_tool("execute", {})
                assert invoked == [actor]
            else:
                with pytest.raises(ToolError, match="authentication required|Authentication required"):
                    await client.call_tool("execute", {})
                assert invoked == []
    finally:
        mcp_auth_context.reset(token)


@pytest.mark.parametrize("denial", ["auth", "role"])
async def test_native_authority_gate_runs_before_resource_resolution(monkeypatch, denial):
    from langboard.mcp_integration import Dependencies

    allocated = []

    class Resource:
        def __init__(self):
            allocated.append(self)

        def close(self):
            raise AssertionError("Denied requests cannot allocate resources")

    monkeypatch.setattr(Dependencies, "DomainService", Resource)

    async def execute(service: Resource) -> dict[str, bool]:
        raise AssertionError("Denied handler cannot run")

    monkeypatch.setattr(Server.McpTool, "get_tool", lambda _: {"exclude": ["service"]})
    monkeypatch.setattr(Server.McpServer, "_validate_role", lambda *args, **kwargs: False)
    server = _create_fastmcp()
    server.add_tool(Tool.from_function(Server.McpServer._wrap_tool("execute", execute), name="execute"))
    actor = Server.User(firstname="MCP", lastname="Reader", email="mcp@example.invalid", password="test")
    token = mcp_auth_context.set(
        {
            "user_or_bot": actor if denial == "role" else None,
            "tool_group": SimpleNamespace(activated_at=object(), tools=["execute"]),
        }
    )
    try:
        async with Client(server) as client:
            with pytest.raises(
                ToolError, match="Insufficient permissions" if denial == "role" else "Authentication required"
            ):
                await client.call_tool("execute", {})
        assert allocated == []
    finally:
        mcp_auth_context.reset(token)
