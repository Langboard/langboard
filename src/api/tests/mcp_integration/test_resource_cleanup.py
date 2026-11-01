"""Invocation-owned resources are released even when another release fails."""

import asyncio
import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.tools import Tool
from langboard.mcp_integration import Dependencies
from langboard.mcp_integration.Server import McpServer
from langboard.mcp_integration.Tool import McpTool
from langboard.middlewares.McpAuthMiddleware import mcp_auth_context


@pytest.mark.parametrize("failure", ["handler", "injection", "cleanup", "cancel"])
async def test_every_created_resource_is_closed_after_failure(monkeypatch, failure):
    closed = []
    created = []
    entered, cleaned = asyncio.Event(), asyncio.Event()
    loop = asyncio.get_running_loop()

    class Resource:
        def __init__(self):
            self.name = "first" if not created else "second"
            if failure == "injection" and self.name == "second":
                raise RuntimeError("injection failure")
            created.append(self.name)

        def close(self):
            closed.append(self.name)
            if len(closed) == len(created):
                loop.call_soon_threadsafe(cleaned.set)
            if failure == "cleanup" and self.name == "second":
                raise RuntimeError("cleanup failure")

    async def handler(first: Resource, second: Resource):
        assert first is not second
        entered.set()
        if failure == "cancel":
            await asyncio.Event().wait()
        if failure == "handler":
            raise RuntimeError("handler failure")
        return {"ok": True}

    monkeypatch.setattr(Dependencies, "DomainService", Resource)
    monkeypatch.setattr(McpServer, "_validate_auth", lambda *args: True)
    monkeypatch.setattr(McpServer, "_validate_role", lambda *args, **kwargs: True)
    monkeypatch.setattr(McpTool, "get_tool", lambda _: {"exclude": ["first", "second"]})
    server = FastMCP("Resource cleanup", mask_error_details=False)
    server.add_tool(Tool.from_function(McpServer._wrap_tool("test_cleanup", handler), name="test_cleanup"))
    token = mcp_auth_context.set({"user_or_bot": object()})
    try:
        async with Client(server) as client:
            if failure == "cancel":
                task = asyncio.create_task(client.call_tool("test_cleanup", {}))
                await asyncio.wait_for(entered.wait(), 3)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                await asyncio.wait_for(cleaned.wait(), 3)
            else:
                message = "Failed to resolve dependency 'second'" if failure == "injection" else f"{failure} failure"
                with pytest.raises(ToolError, match=message):
                    await client.call_tool("test_cleanup", {})
        assert created == (["first"] if failure == "injection" else ["first", "second"])
        assert closed == list(reversed(created))
        assert entered.is_set() is (failure != "injection")
    finally:
        mcp_auth_context.reset(token)
