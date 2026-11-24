"""Invocation-owned resources are released even when another release fails."""

import pytest
from langboard.mcp_integration.Server import McpServer
from langboard.middlewares.McpAuthMiddleware import mcp_auth_context
from langboard_shared.domain.services import DomainService
from langboard_shared.infrastructure.repositories import Repository


@pytest.mark.parametrize("failure", ["handler", "injection", "cleanup"])
async def test_every_created_resource_is_closed_after_failure(monkeypatch, failure):
    closed = []
    created = []

    def close(name):
        closed.append(name)
        if failure == "cleanup" and name == "second":
            raise RuntimeError("cleanup failure")

    def initialize(name):
        def create(self):
            if failure == "injection" and name == "second":
                raise RuntimeError("injection failure")
            created.append(name)

        return create

    async def handler(first: DomainService, second: Repository):
        if failure == "handler":
            raise RuntimeError("handler failure")
        return {"ok": True}

    monkeypatch.setattr(DomainService, "__init__", initialize("first"))
    monkeypatch.setattr(Repository, "__init__", initialize("second"))
    monkeypatch.setattr(DomainService, "close", lambda self: close("first"))
    monkeypatch.setattr(Repository, "close", lambda self: close("second"))
    monkeypatch.setattr(McpServer, "_validate_auth", lambda *args: True)
    monkeypatch.setattr(McpServer, "_validate_role", lambda *args, **kwargs: True)
    token = mcp_auth_context.set({"user_or_bot": object()})
    try:
        with pytest.raises(RuntimeError, match=f"{failure} failure"):
            await McpServer._wrap_tool("test_cleanup", handler)()
        assert closed == list(reversed(created))
    finally:
        mcp_auth_context.reset(token)
