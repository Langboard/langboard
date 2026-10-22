"""Invocation-owned resources are released even when another release fails."""

from types import SimpleNamespace
import pytest
from langboard.mcp_integration import Dependencies
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

    def create(name):
        if failure == "injection" and name == "second":
            raise RuntimeError("injection failure")
        created.append(name)
        return SimpleNamespace(close=lambda: close(name))

    resource_factory = Dependencies._factory_dependency
    monkeypatch.setattr(
        Dependencies, "_factory_dependency",
        lambda factory: resource_factory(lambda: create("first" if factory is DomainService else "second")),
    )

    async def handler(first: DomainService, second: Repository):
        if failure == "handler":
            raise RuntimeError("handler failure")
        return {"ok": True}

    monkeypatch.setattr(McpServer, "_validate_auth", lambda *args: True)
    monkeypatch.setattr(McpServer, "_validate_role", lambda *args, **kwargs: True)
    token = mcp_auth_context.set({"user_or_bot": object()})
    try:
        message = r"Failed to resolve dependency 'second'|injection failure" if failure == "injection" else f"{failure} failure"
        with pytest.raises(RuntimeError, match=message) as error:
            await McpServer._wrap_tool("test_cleanup", handler)()
        if failure == "injection":
            assert str(error.value.__cause__ or error.value) == "injection failure"
        assert closed == list(reversed(created))
    finally:
        mcp_auth_context.reset(token)
