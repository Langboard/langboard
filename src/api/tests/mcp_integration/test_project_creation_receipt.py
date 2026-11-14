"""MCP creation UUIDs reuse the native owner while preserving legacy contracts."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4
import pytest
from fastmcp import Client, FastMCP
from langboard.mcp_integration import Dependencies
from langboard.mcp_integration.Providers import create_native_tool
from langboard.mcp_integration.Server import McpServer
from langboard.mcp_integration.Tool import McpTool
from langboard.mcp_tools import ProjectMcp
from langboard.middlewares.McpAuthMiddleware import mcp_auth_context


@pytest.mark.parametrize("name", ["create_project", "create_project_board"])
@pytest.mark.parametrize("modern", [False, True])
async def test_creation_transport_uuid_schema_forwarding_and_legacy_parity(monkeypatch, internal_mcp_actor, name, modern):
    actor = internal_mcp_actor
    create = Mock(
        return_value=(
            SimpleNamespace(get_uid=lambda: "board", title="Board", project_type="Other"),
            [],
            SimpleNamespace(name="Template"),
        )
    )
    service = SimpleNamespace(project_template=SimpleNamespace(create_project=create))

    def factory(_):
        @asynccontextmanager
        async def resource():
            yield service

        return resource

    monkeypatch.setattr(Dependencies, "_factory_dependency", factory)
    server = FastMCP("Creation receipt", strict_input_validation=True)
    server.add_tool(create_native_tool(name, McpTool.get_tool(name), McpServer._wrap_tool, modern_annotations=modern))
    payload = {"title": "Board"}
    if name == "create_project":
        payload.update(description=None, project_type="Other")
    expected = (
        {"project_uid": "board"}
        if name == "create_project"
        else {
            "project": {
                "uid": "board",
                "title": "Board",
                "project_type": "Other",
                "url": f"{ProjectMcp.Env.PUBLIC_UI_URL}/board/board",
                "template": "Template",
            },
            "columns": [],
        }
    )
    token = mcp_auth_context.set({"transport": "oauth", "user_or_bot": actor})
    try:
        async with Client(server) as client:
            schema = (await client.list_tools())[0].input_schema
            legacy_fields = {"title", "description", "template_name", "infer_template_prefix"}
            if name == "create_project":
                legacy_fields.add("project_type")
            assert set(schema["properties"]) == legacy_fields | ({"request_uuid"} if modern else set())
            if modern:
                assert schema["properties"]["request_uuid"]["anyOf"][0]["format"] == "uuid"
                assert "request_uuid" not in schema.get("required", [])
            assert (await client.call_tool(name, payload)).structured_content == expected
            assert create.call_args.args == (actor, "Board", None, "Other", None, False)
            assert create.call_args.kwargs == {}
            create.reset_mock()
            key = str(uuid4())
            if modern:
                assert (
                    await client.call_tool(name, {**payload, "request_uuid": key.upper()})
                ).structured_content == expected
                assert create.call_args.kwargs == {"request_uuid": key}
                create.reset_mock()
            rejected = await client.call_tool(
                name, {**payload, "request_uuid": "invalid" if modern else key}, raise_on_error=False
            )
            assert rejected.is_error
            create.assert_not_called()
    finally:
        mcp_auth_context.reset(token)


@pytest.mark.parametrize("handler", ["create_template_project", "create_project", "create_project_board"])
def test_direct_handler_rejects_invalid_uuid_before_native_mutation(handler):
    create = Mock()
    service = SimpleNamespace(project_template=SimpleNamespace(create_project=create))
    payload = dict(title="Board", user=object(), service=service, request_uuid="invalid")
    if handler != "create_project_board":
        payload.update(description=None, project_type="Other")
    with pytest.raises(ValueError):
        getattr(ProjectMcp, handler)(**payload)
    create.assert_not_called()


def test_missing_template_projection_keeps_string_contract_without_guessing_name():
    create = Mock(
        return_value=(
            SimpleNamespace(get_uid=lambda: "board", title="Board", project_type="Other"),
            [],
            None,
        )
    )
    service = SimpleNamespace(project_template=SimpleNamespace(create_project=create))
    for template_name in (None, "Requested alias"):
        result = ProjectMcp.create_project_board(
            "Board", object(), service, template_name=template_name, request_uuid=str(uuid4())
        )
        assert result["project"]["template"] == ""
