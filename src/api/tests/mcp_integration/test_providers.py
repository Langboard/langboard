"""Exercise the compatibility provider through actual FastMCP dispatch."""

import json
from inspect import Parameter, Signature
from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastmcp import Client
from fastmcp.exceptions import ToolError
from fastmcp.tools import Tool
from langboard.App import App
from langboard.Loader import ModuleLoader
from langboard.mcp_integration.Annotations import READ_ONLY_TOOLS
from langboard.mcp_integration.Providers import (
    AGENT_CORE_TOOLS,
    READ_PAGE_BOUNDS,
    create_agent_core_provider,
    create_compatibility_provider,
    create_raw_primitive_provider,
    with_read_page_bounds,
)
from langboard.mcp_integration.Server import McpServer, _create_fastmcp
from langboard.mcp_integration.Tool import McpTool
from langboard.middlewares.McpAuthMiddleware import mcp_auth_context
from langboard_shared.helpers import MiddlewareHelper


async def test_modern_annotations_reach_catalog_and_raw_search_without_changing_legacy():
    ModuleLoader.load("mcp_tools", "Mcp", log=False)
    registered = set(McpTool.get_tools())
    assert READ_ONLY_TOOLS - {"search_raw_tools"} <= registered
    token = mcp_auth_context.set({"tool_group": SimpleNamespace(activated_at=object(), tools=list(registered))})
    try:
        catalogs = {}
        for profile in ("compatibility", "agent", "raw"):
            _, server = McpServer.get_http_app(profile)
            async with Client(server) as client:
                catalogs[profile] = {tool.name: tool for tool in await client.list_tools()}
                if profile == "raw":
                    result = await client.call_tool("search_raw_tools", {"pattern": r"^get_public_card_metadata\b"})
                    definition = json.loads(result.content[0].text)[0]
                    assert definition["annotations"]["readOnlyHint"] is True
        assert all(tool.annotations is None for tool in catalogs["compatibility"].values())
        read = catalogs["agent"]["get_card_bundle"].annotations
        assert read.read_only_hint is True and read.destructive_hint is False and read.idempotent_hint is True
        write = catalogs["agent"]["create_card"].annotations
        assert write.read_only_hint is False and write.destructive_hint is True and write.idempotent_hint is False
        assert catalogs["raw"]["search_raw_tools"].annotations.read_only_hint is True
        assert catalogs["raw"]["call_raw_tool"].annotations.read_only_hint is False
        assert catalogs["raw"]["call_raw_tool"].annotations.idempotent_hint is False
    finally:
        mcp_auth_context.reset(token)


@pytest.mark.parametrize("role_allowed", [True, False])
async def test_provider_preserves_wrapped_dispatch_and_tool_group_deny(monkeypatch, role_allowed):
    calls = []

    def record(value: int) -> dict[str, int]:
        calls.append(value)
        return {"value": value}

    metadata = {"handler": record, "description": "Record a value", "exclude": [], "accessible_type": "all"}
    monkeypatch.setattr(McpTool, "get_tools", lambda: {"record": metadata, "denied": metadata})
    monkeypatch.setattr(McpTool, "get_tool", lambda name: metadata if name in {"record", "denied"} else None)
    monkeypatch.setattr(McpServer, "_validate_auth", lambda actor, name: True)
    monkeypatch.setattr(McpServer, "_validate_role", lambda actor, handler, **kwargs: role_allowed)
    server = _create_fastmcp()
    server.add_provider(create_compatibility_provider(McpServer._wrap_tool))
    token = mcp_auth_context.set(
        {"user_or_bot": object(), "tool_group": SimpleNamespace(activated_at=object(), tools=["record"])}
    )
    try:
        async with Client(server) as client:
            catalog = await client.list_tools()
            assert [tool.name for tool in catalog] == ["record"]
            assert catalog[0].input_schema["properties"]["value"]["type"] == "integer"
            if role_allowed:
                result = await client.call_tool("record", {"value": 3})
                assert result.structured_content == {"value": 3}
            else:
                with pytest.raises(ToolError, match="Insufficient permissions"):
                    await client.call_tool("record", {"value": 3})
            with pytest.raises(ToolError):
                await client.call_tool("denied", {"value": 4})
            assert calls == ([3] if role_allowed else [])
    finally:
        mcp_auth_context.reset(token)


async def test_registered_profiles_partition_catalog_without_changing_schemas():
    ModuleLoader.load("mcp_tools", "Mcp", log=False)
    registered = set(McpTool.get_tools())
    assert AGENT_CORE_TOOLS <= registered
    token = mcp_auth_context.set({"tool_group": SimpleNamespace(activated_at=object(), tools=list(registered))})
    try:
        catalogs = {}
        for profile in ("compatibility", "agent", "raw"):
            if profile == "raw":
                server = _create_fastmcp()
                server.add_provider(create_raw_primitive_provider(McpServer._wrap_tool))
            else:
                _, server = McpServer.get_http_app(profile)
            async with Client(server) as client:
                catalogs[profile] = {tool.name: tool.input_schema for tool in await client.list_tools()}
        assert set(catalogs["agent"]) == AGENT_CORE_TOOLS
        assert set(catalogs["raw"]) == registered - AGENT_CORE_TOOLS
        modern = {**catalogs["agent"], **catalogs["raw"]}
        assert "profile" in modern["get_card_bundle"]["properties"]
        assert "profile" not in catalogs["compatibility"]["get_card_bundle"]["properties"]
        modern["get_card_bundle"]["properties"].pop("profile")
        assert modern["list_project_cards"]["properties"].pop("format") == {
            "default": "tree",
            "enum": ["tree", "normal"],
            "type": "string",
        }
        assert "format" not in catalogs["compatibility"]["list_project_cards"]["properties"]
        assert "query" not in catalogs["compatibility"]["get_projects"]["properties"]
        query = modern["get_projects"]["properties"].pop("query")
        assert query["default"] is None
        assert query["anyOf"][0]["minLength"] == 1
        assert query["anyOf"][0]["maxLength"] == 100

        for name, bounds in READ_PAGE_BOUNDS.items():
            for parameter, maximum in bounds.items():
                schema = modern[name]["properties"][parameter]
                assert schema.pop("minimum") == 1
                assert schema.pop("maximum") == maximum
        modern_fields = {
            "create_project": ("request_uuid",),
            "create_project_board": ("request_uuid",),
            "list_project_cards": ("visibility", "created_by", "assigned_to"),
            "add_card_comment": ("anchor", "request_uid"),
            "change_card_order_or_move_column": ("move_descendants", "confirmation_revision"),
            "upload_card_attachment": ("request_uid", "duplicate_action", "confirmation_token", "replacement_uid"),
        }
        for name, fields in modern_fields.items():
            assert McpTool.get_tool(name)["modern_only"] == fields
            for field in fields:
                assert field in modern[name]["properties"]
                assert field not in catalogs["compatibility"][name]["properties"]
                assert field not in modern[name]["required"]
        for name in ("add_card_comment", "upload_card_attachment"):
            assert modern[name]["properties"]["request_uid"]["anyOf"][0]["format"] == "uuid"
        for name in ("create_project", "create_project_board"):
            assert modern[name]["properties"]["request_uuid"]["anyOf"][0]["format"] == "uuid"
        assert modern["change_card_order_or_move_column"]["properties"]["move_descendants"]["anyOf"][0] == {
            "type": "boolean"
        }
        assert modern["upload_card_attachment"]["properties"]["duplicate_action"]["anyOf"][0]["enum"] == [
            "add",
            "replace",
            "cancel",
        ]
        anchors = modern["add_card_comment"]["properties"]["anchor"]["anyOf"]
        assert [anchor["properties"]["type"]["const"] for anchor in anchors[:2]] == [
            "TextQuoteSelector",
            "ImageRegionSelector",
        ]
        for name, fields in modern_fields.items():
            for field in fields:
                modern[name]["properties"].pop(field)
        assert modern == catalogs["compatibility"]
    finally:
        mcp_auth_context.reset(token)


@pytest.mark.parametrize("role_allowed", [True, False])
async def test_raw_search_respects_grants_and_proxy_rechecks_current_authorization(monkeypatch, role_allowed):
    calls = []

    def record(value: int) -> dict[str, int]:
        calls.append(value)
        return {"value": value}

    metadata = {"handler": record, "description": "Record", "exclude": [], "accessible_type": "all"}
    names = {"archive_card", "delete_card", "get_projects"}

    def archive(value: int) -> dict[str, str]:
        calls.append(value)
        return {"message": "Archived"}

    registry = dict.fromkeys(names, metadata)
    registry["archive_card"] = {**metadata, "handler": archive}
    monkeypatch.setattr(McpTool, "get_tools", lambda: registry)
    monkeypatch.setattr(McpTool, "get_tool", lambda name: metadata if name in names else None)
    monkeypatch.setattr(McpServer, "_validate_auth", lambda actor, name: True)
    monkeypatch.setattr(McpServer, "_validate_role", lambda actor, handler, **kwargs: role_allowed)
    _, server = McpServer.get_http_app("raw")
    group = SimpleNamespace(activated_at=object(), tools=["archive_card", "get_projects"])
    token = mcp_auth_context.set({"user_or_bot": object(), "tool_group": group})
    try:
        async with Client(server) as client:
            assert {tool.name for tool in await client.list_tools()} == {"search_raw_tools", "call_raw_tool"}
            search = await client.call_tool("search_raw_tools", {"pattern": ".*"})
            definitions = json.loads(search.content[0].text)
            assert [tool["name"] for tool in definitions] == ["archive_card"]
            assert definitions[0]["inputSchema"]["properties"]["value"]["type"] == "integer"
            if role_allowed:
                result = await client.call_tool("call_raw_tool", {"name": "archive_card", "arguments": {"value": 3}})
                assert result.structured_content == {"message": "Archived"}
            else:
                with pytest.raises(ToolError):
                    await client.call_tool("call_raw_tool", {"name": "archive_card", "arguments": {"value": 3}})
            for denied in ("delete_card", "get_projects", "call_raw_tool"):
                with pytest.raises(ToolError):
                    await client.call_tool("call_raw_tool", {"name": denied, "arguments": {"value": 4}})
            group.tools = []
            revoked = await client.call_tool("search_raw_tools", {"pattern": ".*"})
            assert not revoked.content
            with pytest.raises(ToolError):
                await client.call_tool("call_raw_tool", {"name": "archive_card", "arguments": {"value": 5}})
            group.activated_at = None
            with pytest.raises(ToolError):
                await client.call_tool("search_raw_tools", {"pattern": ".*"})
            assert calls == ([3] if role_allowed else [])
    finally:
        mcp_auth_context.reset(token)


@pytest.mark.parametrize("path", ["/mcp/stream", "/mcp/agent/stream", "/mcp/raw/stream"])
def test_mounted_profiles_retain_http_authentication(monkeypatch, path):
    monkeypatch.setattr(ModuleLoader, "load", lambda *args, **kwargs: {})
    monkeypatch.setattr(MiddlewareHelper, "validate_auth", lambda scope, **kwargs: 401)
    app = App.__new__(App)
    app.config = SimpleNamespace(is_restarting=False)
    app.api = FastAPI()
    app._init_mcp_server()
    app._init_api_routes()
    with TestClient(app.api) as client:
        response = client.post(path, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert response.status_code == 401


@pytest.mark.parametrize(
    "factory,visible,hidden",
    [
        (create_agent_core_provider, "get_projects", "archive_card"),
        (create_raw_primitive_provider, "archive_card", "get_projects"),
        (create_compatibility_provider, "get_projects", None),
    ],
)
@pytest.mark.parametrize("role_allowed", [True, False])
async def test_profiles_preserve_domain_dispatch_and_deny_hidden_or_ungranted_calls(
    monkeypatch,
    factory,
    visible,
    hidden,
    role_allowed,
):
    calls = []

    def record(value: int) -> dict[str, list]:
        calls.append(value)
        return {"projects": []}

    metadata = {"handler": record, "description": "Record", "exclude": [], "accessible_type": "all"}
    names = {"get_projects", "archive_card", "denied"}

    def archive(value: int) -> dict[str, str]:
        calls.append(value)
        return {"message": "Archived"}

    registry = dict.fromkeys(names, metadata)
    registry["archive_card"] = {**metadata, "handler": archive}
    monkeypatch.setattr(McpTool, "get_tools", lambda: registry)
    monkeypatch.setattr(McpTool, "get_tool", lambda name: metadata if name in names else None)
    monkeypatch.setattr(McpServer, "_validate_auth", lambda actor, name: True)
    monkeypatch.setattr(McpServer, "_validate_role", lambda actor, handler, **kwargs: role_allowed)
    server = _create_fastmcp()
    server.add_provider(factory(McpServer._wrap_tool))
    token = mcp_auth_context.set(
        {
            "user_or_bot": object(),
            "tool_group": SimpleNamespace(activated_at=object(), tools=["get_projects", "archive_card"]),
        }
    )
    try:
        async with Client(server) as client:
            tools = await client.list_tools()
            assert {tool.name for tool in tools} == ({visible} if hidden else {"get_projects", "archive_card"})
            if role_allowed:
                result = await client.call_tool(visible, {"value": 3})
                assert result.structured_content == (
                    {"message": "Archived"} if visible == "archive_card" else {"projects": []}
                )
            else:
                with pytest.raises(ToolError, match="Insufficient permissions"):
                    await client.call_tool(visible, {"value": 3})
            if hidden:
                with pytest.raises(ToolError):
                    await client.call_tool(hidden, {"value": 4})
            with pytest.raises(ToolError):
                await client.call_tool("denied", {"value": 5})
            assert calls == ([3] if role_allowed else [])
    finally:
        mcp_auth_context.reset(token)


@pytest.mark.parametrize(
    "factory", [create_compatibility_provider, create_agent_core_provider, create_raw_primitive_provider]
)
async def test_provider_exposes_server_owned_workflow_resource_and_prompt(monkeypatch, factory):
    monkeypatch.setattr(McpTool, "get_tools", lambda: {})
    monkeypatch.setattr(McpTool, "get_tool", lambda name: None)
    server = _create_fastmcp()
    server.add_provider(factory(McpServer._wrap_tool))
    async with Client(server) as client:
        resources = await client.list_resources()
        assert [str(resource.uri) for resource in resources] == ["langboard://policy/workflow"]
        resource = await client.read_resource("langboard://policy/workflow")
        assert "workflow_stage_status" in str(resource)
        prompts = await client.list_prompts()
        assert [prompt.name for prompt in prompts] == ["apply_workflow_policy"]
        prompt = await client.get_prompt("apply_workflow_policy", {})
        assert "get_project_identity" in str(prompt)


@pytest.mark.parametrize("granted", [True, False])
@pytest.mark.parametrize(
    "factory", [create_compatibility_provider, create_agent_core_provider, create_raw_primitive_provider]
)
async def test_card_policy_resource_reuses_current_query_and_rejects_ungranted_reads(monkeypatch, granted, factory):
    state = {"workflow": {"workflow_guidance": "Review first"}, "work_state": {"verification_state": "unverified"}}
    calls = []

    async def bundle(**kwargs):
        calls.append(kwargs)
        return {"card": state}

    monkeypatch.setattr(McpTool, "get_tools", lambda: {})
    monkeypatch.setattr(McpTool, "get_tool", lambda name: {"handler": bundle} if name == "get_card_bundle" else None)
    server = _create_fastmcp()
    server.add_provider(factory(lambda name, handler: handler))
    token = mcp_auth_context.set(
        {"tool_group": SimpleNamespace(activated_at=object(), tools=["get_card_bundle"] if granted else [])}
    )
    try:
        async with Client(server) as client:
            if granted:
                resource = await client.read_resource("langboard://projects/project/cards/card/workflow")
                assert "Review first" in str(resource)
                state["workflow"]["workflow_guidance"] = "Changed server guidance"
                prompt = await client.get_prompt("apply_card_workflow", {"project_uid": "project", "card_uid": "card"})
                assert "Changed server guidance" in str(prompt)
                assert len(calls) == 2
                assert calls[0] == {"project_uid": "project", "card_uid": "card", "include": []}
            else:
                with pytest.raises(Exception, match="get_card_bundle is not allowed"):
                    await client.read_resource("langboard://projects/project/cards/card/workflow")
                with pytest.raises(Exception, match="get_card_bundle is not allowed"):
                    await client.get_prompt("apply_card_workflow", {"project_uid": "project", "card_uid": "card"})
                assert calls == []
    finally:
        mcp_auth_context.reset(token)


@pytest.mark.parametrize(
    ("name", "parameter", "maximum"),
    [
        ("list_project_cards", "limit", 25),
        ("list_project_wikis", "limit", 50),
        ("list_wiki_revisions", "limit", 50),
        ("read_wiki_content", "limit", 16000),
        ("read_wiki_revision", "limit", 16000),
        ("get_shared_user_activities", "limit", 50),
        ("get_shared_user_activities", "max_chars", 8000),
    ],
)
async def test_modern_page_bounds_reject_invalid_arguments_before_domain_dispatch(name, parameter, maximum):
    calls = []

    async def read(**kwargs) -> dict:
        calls.append(kwargs[parameter])
        return kwargs

    read.__signature__ = Signature([Parameter(parameter, Parameter.KEYWORD_ONLY, default=20, annotation=int)])
    read.__annotations__ = {parameter: int, "return": dict}

    server = _create_fastmcp()
    server.add_tool(Tool.from_function(with_read_page_bounds(name, read), name=name))
    token = mcp_auth_context.set({"tool_group": SimpleNamespace(activated_at=object(), tools=[name])})
    try:
        async with Client(server) as client:
            for invalid in (0, maximum + 1, True, 1.5):
                with pytest.raises(ToolError):
                    await client.call_tool(name, {parameter: invalid})
            assert calls == []
            for valid in (1, maximum):
                result = await client.call_tool(name, {parameter: valid})
                assert result.structured_content == {parameter: valid}
            assert calls == [1, maximum]
    finally:
        mcp_auth_context.reset(token)


@pytest.mark.parametrize("profile", ["compatibility", "agent"])
async def test_project_card_list_compatibility_preserves_frozen_flat_contract(monkeypatch, profile):
    from pathlib import Path
    from unittest.mock import Mock
    from langboard.mcp_tools import CardMcp
    from langboard_shared.domain.models import User

    ModuleLoader.load("mcp_tools", "Mcp", log=False)
    metadata = McpTool.get_tool("list_project_cards")
    monkeypatch.setattr(McpTool, "get_tools", lambda: {"list_project_cards": metadata})
    monkeypatch.setattr(McpTool, "get_tool", lambda name: metadata if name == "list_project_cards" else None)
    monkeypatch.setattr(McpServer, "_validate_role", lambda *args, **kwargs: True)
    page = SimpleNamespace(
        items=[
            {"uid": "a", "title": "A", "project_column_uid": "column"},
            {"uid": "b", "title": "B", "project_column_uid": "column"},
        ],
        total_count=100,
        next_cursor_fields=("2026-10-08T00:00:00+00:00", "b"),
        workflow_stages={"doing": {"name": "Doing"}},
        columns={"column": {"name": "In progress"}},
    )
    port = SimpleNamespace(
        card_query_actor=lambda: "User:fixture:mcp",
        get_project_card_page=Mock(return_value=page),
        get_project_card_relationships=Mock(
            return_value=[{"parent_card_uid": "a", "child_card_uid": "b", "machine_semantic": "contains"}]
        ),
    )
    monkeypatch.setattr(CardMcp, "_adapter", lambda actor, service: port)
    actor = User(firstname="MCP", lastname="Reader", email="mcp@example.invalid", password="test")
    token = mcp_auth_context.set(
        {"user_or_bot": actor, "tool_group": SimpleNamespace(activated_at=object(), tools=["list_project_cards"])}
    )
    try:
        _, server = McpServer.get_http_app(profile)
        async with Client(server, mode="legacy" if profile == "compatibility" else "2026-07-28") as client:
            tool = (await client.list_tools())[0]
            arguments = {"project_uid": "board", "limit": 2, "include_closed": True, "workflow_stages": ["doing"]}
            result = (await client.call_tool("list_project_cards", arguments)).structured_content
            if profile == "compatibility":
                frozen = json.loads((Path(__file__).parent / "fixtures/legacy-native-v1.json").read_text())
                expected = next(item for item in frozen["contract"]["tools"] if item["name"] == "list_project_cards")
                assert tool.input_schema == expected["input_schema"]
                registered = (await server.list_tools(run_middleware=False))[0]
                assert registered.output_schema == expected["output_schema"]
                # FastMCP client discovery inlines local schema references.
                wire_schema = dict(expected["output_schema"])
                definitions = wire_schema.pop("$defs")
                wire_schema["properties"] = {**wire_schema["properties"], "cards": definitions["BoundedItemsDto"]}
                assert tool.output_schema == wire_schema
                assert set(result) == {"project_uid", "cards", "workflow_stages"}
                assert [item["uid"] for item in result["cards"]["items"]] == ["a", "b"]
                assert all(item["project_column_name"] == "In progress" for item in result["cards"]["items"])
                port.get_project_card_relationships.assert_not_called()
                for forbidden in ("tree", "normal"):
                    with pytest.raises(ToolError):
                        await client.call_tool("list_project_cards", {**arguments, "format": forbidden})
                assert port.get_project_card_page.call_count == 1
            else:
                assert tool.input_schema["properties"]["format"]["default"] == "tree"
                assert result["format"] == "tree"
                assert result["cards"]["items"][0]["children"][0]["uid"] == "b"
                assert result["relationship_scope"] == "current_page"
                normal = (
                    await client.call_tool("list_project_cards", {**arguments, "format": "normal"})
                ).structured_content
                assert set(normal) == {"project_uid", "cards", "workflow_stages", "columns"}
                assert [item["uid"] for item in normal["cards"]["items"]] == ["a", "b"]
                assert all("project_column_name" not in item for item in normal["cards"]["items"])
                assert normal["cards"]["next_cursor"] == result["cards"]["next_cursor"]
                assert normal["columns"] == page.columns
                port.get_project_card_relationships.assert_called_once_with("board", ["a", "b"])
            assert result["cards"]["limit"] == 2 and result["cards"]["total_count"] == 100
            await client.call_tool("list_project_cards", {**arguments, "cursor": result["cards"]["next_cursor"]})
            args, kwargs = port.get_project_card_page.call_args
            assert args[:2] == ("board", 2) and args[2] is not None and args[3] == "b"
            assert kwargs == {"include_closed": True, "workflow_stages": ["doing"]}
    finally:
        mcp_auth_context.reset(token)
