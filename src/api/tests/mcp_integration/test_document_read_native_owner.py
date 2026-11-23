"""Canonical document dispatch preserves native receipts and authorization."""

import asyncio
from inspect import signature
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from fastmcp.exceptions import AuthorizationError, ValidationError
from langboard.mcp_integration import McpRoleFilter, McpTool
from langboard.mcp_integration.Providers import AGENT_CORE_TOOLS
from langboard.mcp_integration.Server import McpServer
from langboard.mcp_integration.ToolGroupMiddleware import ToolGroupMiddleware
from langboard.mcp_tools import CardMcp
from langboard.middlewares.McpAuthMiddleware import mcp_auth_context
from langboard_shared.domain.models import Bot, ProjectRole, User
from langboard_shared.domain.models.ProjectRole import ProjectRoleAction


@pytest.mark.parametrize(
    "options",
    [
        {"query": "words", "offset": 1},
        {"query": "words", "expected_generation": "generation"},
        {"query": "words", "page": 1},
        {"query": "words", "max_chars": 128},
        {"max_tokens": 128},
        {"max_tokens": False},
        {"query": "words", "max_tokens": 4001},
        {"query": ""},
        {"query": "  "},
        {"query": "a" * 1001},
        {"query": 42},
    ],
)
def test_incompatible_or_invalid_options_never_load_sources(options):
    service = SimpleNamespace(card=SimpleNamespace(resolve_readable_card=Mock()))
    with pytest.raises((ValueError, ValidationError)):
        CardMcp.read_card_document("board", "card", "attachment", object(), service, **options)
    service.card.resolve_readable_card.assert_not_called()


@pytest.mark.parametrize("max_tokens", [128, 2000, 4000])
def test_canonical_and_primitive_return_exact_native_receipt(monkeypatch, max_tokens):
    receipt = {"generation": "native", "matches": [{"content": "한국어 日本語 中文", "pages": [2]}]}
    owner = Mock(return_value=receipt)
    monkeypatch.setattr(CardMcp, "_search_card_document", owner)
    # Decorated compatibility entry point must never be invoked by canonical read.
    primitive = CardMcp.search_card_document
    monkeypatch.setattr(CardMcp, "search_card_document", Mock(side_effect=AssertionError("decorated invocation")))
    actor, service = object(), object()
    query = "  한국어 日本語 中文  "
    assert (
        CardMcp.read_card_document("board", "card", "attachment", actor, service, query=query, max_tokens=max_tokens)
        is receipt
    )
    assert primitive("board", "card", "attachment", query, actor, service, max_tokens) is receipt
    assert owner.call_args_list[0] == owner.call_args_list[1]
    assert owner.call_args.args == ("board", "card", "attachment", query, actor, service, max_tokens)


def test_registered_schema_preserves_positional_fields_and_bounds():
    parameters = list(signature(CardMcp.read_card_document).parameters)
    assert parameters == [
        "project_uid",
        "card_uid",
        "attachment_uid",
        "user",
        "service",
        "offset",
        "max_chars",
        "expected_generation",
        "page",
        "query",
        "max_tokens",
    ]
    tool = McpTool.get_tool("read_card_document")
    schema = tool["input_schema"]
    assert schema["required"] == ["project_uid", "card_uid", "attachment_uid"]
    assert set(schema["properties"]) == set(parameters) - {"user", "service"}
    assert schema["properties"]["query"]["anyOf"] == [
        {"maxLength": 1000, "minLength": 1, "type": "string"},
        {"type": "null"},
    ]
    assert schema["properties"]["query"]["default"] is None
    assert schema["properties"]["max_tokens"] == {"default": 2000, "maximum": 4000, "minimum": 128, "type": "integer"}
    assert schema["properties"]["offset"]["default"] == 0
    assert schema["properties"]["max_chars"]["default"] == 4000
    assert schema["properties"]["expected_generation"]["default"] is None
    assert schema["properties"]["page"]["default"] is None


@pytest.mark.parametrize("name", ["read_card_document", "search_card_document"])
def test_native_registration_keeps_roles_user_only_and_existing_group_grants(name):
    tool = McpTool.get_tool(name)
    assert tool["accessible_type"] == "user" and tool["exclude"] == ["user", "service"]
    assert name in AGENT_CORE_TOOLS
    role, actions, _, allowed_admin = McpRoleFilter.get_filtered(tool["handler"])
    assert role is ProjectRole and actions == [ProjectRoleAction.Read.value] and allowed_admin is True
    assert McpServer._validate_auth(User.model_construct(), name) is True
    assert McpServer._validate_auth(Bot.model_construct(), name) is False
    assert McpServer._validate_auth(None, name) is False
    other = "search_card_document" if name == "read_card_document" else "read_card_document"
    token = mcp_auth_context.set({"tool_group": SimpleNamespace(activated_at=True, tools=[other])})
    called = Mock()

    async def invoke(_context):
        called()
        return "unexpected"

    try:
        with pytest.raises(AuthorizationError, match="not allowed"):
            asyncio.run(ToolGroupMiddleware().on_call_tool(SimpleNamespace(message=SimpleNamespace(name=name)), invoke))
        called.assert_not_called()
    finally:
        mcp_auth_context.reset(token)


def test_canonical_grant_covers_query_without_granting_compatibility_search(monkeypatch):
    """Canonical read intentionally adds query capability to its existing grant."""
    token = mcp_auth_context.set({"tool_group": SimpleNamespace(activated_at=True, tools=["read_card_document"])})
    receipt = {"generation": "native", "matches": []}
    owner = Mock(return_value=receipt)
    monkeypatch.setattr(CardMcp, "_search_card_document", owner)

    async def invoke(_context):
        return CardMcp.read_card_document("board", "card", "attachment", object(), object(), query="한국어")

    try:
        middleware = ToolGroupMiddleware()
        assert (
            asyncio.run(
                middleware.on_call_tool(SimpleNamespace(message=SimpleNamespace(name="read_card_document")), invoke)
            )
            is receipt
        )
        with pytest.raises(AuthorizationError, match="not allowed"):
            asyncio.run(
                middleware.on_call_tool(SimpleNamespace(message=SimpleNamespace(name="search_card_document")), invoke)
            )
        owner.assert_called_once()
    finally:
        mcp_auth_context.reset(token)


@pytest.mark.parametrize(
    "options", [{"query": "한국어", "offset": 1}, {"query": "words", "page": 1}, {"max_tokens": 128}]
)
@pytest.mark.asyncio
async def test_native_http_rejects_incompatible_read_options_before_owner(monkeypatch, options):
    from importlib import import_module
    from fastapi import FastAPI, Request
    from fastmcp import FastMCP
    from httpx import ASGITransport, AsyncClient
    from langboard_shared.core.security import AuthSecurity

    route = import_module("langboard.routes.mcp.McpApi")
    service = SimpleNamespace(
        mcp_tool_group=SimpleNamespace(
            get_by_id_like=lambda _: SimpleNamespace(activated_at=True, tools=["read_card_document"], user_id=None)
        ),
        close=lambda: None,
    )
    monkeypatch.setattr(route, "DomainService", lambda: service)
    owner = Mock(side_effect=AssertionError("Invalid options must not load a source"))
    monkeypatch.setattr(CardMcp, "_require_readable_document", owner)
    monkeypatch.setattr(CardMcp, "_search_card_document", owner)
    mcp = FastMCP("document-input-http-test")

    @mcp.tool(name="read_card_document")
    def execute(query: str | None = None, offset: int = 0, page: int | None = None, max_tokens: int = 2000):
        return CardMcp.read_card_document(
            "board",
            "card",
            "attachment",
            object(),
            service,
            query=query,
            offset=offset,
            page=page,
            max_tokens=max_tokens,
        )

    monkeypatch.setattr(route.McpServer, "mcp", mcp)
    app = FastAPI()

    @app.post("/mcp/tools/read_card_document")
    async def dispatch(request: Request):
        request.scope["auth"] = User.model_construct(id=1)
        return await route.execute_mcp_tool("read_card_document", request)

    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        response = await client.post(
            "/mcp/tools/read_card_document",
            json=options,
            headers={AuthSecurity.MCP_TOOL_GROUP_UID_HEADER: "group"},
        )
    assert response.status_code == 400
    assert "unknown" not in response.text.lower()
    owner.assert_not_called()
