import ast
import os
from functools import wraps
from inspect import signature
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError, ValidationError


os.environ.setdefault("PROJECT_NAME", "langboard")

from langboard.mcp_integration import McpRoleFilter, McpTool  # noqa: E402
from langboard.mcp_integration.Providers import create_native_domain_provider  # noqa: E402
from langboard.mcp_tools import ProjectMcp  # noqa: E402
from langboard.mcp_tools.ProjectMcp import _column_target_order  # noqa: E402
from langboard_shared.domain.models import ProjectRole  # noqa: E402
from langboard_shared.domain.models.ProjectColumn import ProjectColumnWorkflowConflict  # noqa: E402
from langboard_shared.domain.models.ProjectRole import ProjectRoleAction  # noqa: E402
from langboard_shared.security import RoleFinder  # noqa: E402


def test_column_order_tool_reuses_native_service() -> None:
    """Column reorder is an MCP tool backed by the existing service method."""

    source = (Path(__file__).parents[2] / "langboard" / "mcp_tools" / "ProjectMcp.py").read_text()
    function = next(
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef) and node.name == "change_column_order"
    )

    assert any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "change_order"
        for node in ast.walk(function)
    )


def test_relative_column_order_rejects_foreign_anchor_and_conflicting_selectors() -> None:
    columns = [{"uid": "todo"}, {"uid": "doing"}, {"uid": "done"}]
    assert _column_target_order(columns, position="leftmost", after_column_uid=None, before_column_uid=None) == 0
    assert _column_target_order(columns, position=None, after_column_uid="doing", before_column_uid=None) == 2
    assert (
        _column_target_order(columns, position=None, after_column_uid=None, before_column_uid="done", moving_uid="todo")
        == 1
    )
    with pytest.raises(ValueError, match="not found"):
        _column_target_order(columns, position=None, after_column_uid="other-board", before_column_uid=None)
    with pytest.raises(ValueError, match="Choose one"):
        _column_target_order(columns, position="leftmost", after_column_uid="doing", before_column_uid=None)


@pytest.mark.parametrize(
    ("name", "action"),
    [("get_project_workflow_stages", ProjectRoleAction.Read), ("change_column_workflow_stage", ProjectRoleAction.Update)],
)
def test_workflow_tools_register_current_board_permissions(name, action):
    handler = getattr(ProjectMcp, name)
    metadata = McpTool.get_tool(name)
    assert metadata["handler"] is handler
    assert metadata["exclude"] == ["service"]
    assert McpRoleFilter.get_filtered(handler) == (ProjectRole, [action.value], RoleFinder.project, True)


@pytest.mark.parametrize("modern", [False, True])
async def test_registered_workflow_tools_preserve_native_contract(monkeypatch, modern):
    """Exercise registry, provider schema and validation, including optional null presence."""
    stages = [{"key": "review", "is_active": True}]
    columns = SimpleNamespace(
        get_workflow_stage_options=Mock(return_value=stages),
        change_workflow_stage=Mock(return_value=True),
    )
    service = SimpleNamespace(project_column=columns)
    metadata = {name: McpTool.get_tool(name) for name in (
        "get_project_workflow_stages", "change_column_workflow_stage"
    )}
    monkeypatch.setattr(McpTool, "get_tools", lambda: metadata)
    monkeypatch.setattr(McpTool, "get_tool", lambda name: metadata.get(name))

    def wrap(_name, handler):
        @wraps(handler)
        async def call(**kwargs):
            return handler(service=service, **kwargs)

        call.__signature__ = signature(handler).replace(
            parameters=[param for name, param in signature(handler).parameters.items() if name != "service"]
        )
        return call

    server = FastMCP("Column workflow fixture")
    server.add_provider(create_native_domain_provider(wrap, modern_annotations=modern))
    async with Client(server) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
        schema = tools["change_column_workflow_stage"].input_schema
        assert schema["required"] == ["project_uid", "column_uid", "workflow_stage"]
        assert schema["properties"]["expected_workflow_stage"] == {
            "anyOf": [{"type": "string"}, {"type": "null"}]
        }
        result = await client.call_tool("get_project_workflow_stages", {"project_uid": "board"})
        assert result.structured_content == {"stages": stages}
        columns.get_workflow_stage_options.assert_called_once_with("board")

        base = {"project_uid": "board", "column_uid": "column", "workflow_stage": "review"}
        for expectation in ({}, {"expected_workflow_stage": None}, {"expected_workflow_stage": "request"}):
            result = await client.call_tool("change_column_workflow_stage", {**base, **expectation})
            assert result.structured_content == {"workflow_stage": "review"}
            expected = {"check_expected": True, **expectation} if expectation else {}
            columns.change_workflow_stage.assert_called_once_with("board", "column", "review", **expected)
            columns.change_workflow_stage.reset_mock()

        result = await client.call_tool("change_column_workflow_stage", {**base, "workflow_stage": None})
        assert result.structured_content == {"workflow_stage": None}
        columns.change_workflow_stage.assert_called_once_with("board", "column", None)
        columns.change_workflow_stage.reset_mock()

        for field in ("workflow_stage", "expected_workflow_stage"):
            for invalid in (True, False, 1, [], {}):
                with pytest.raises(ToolError):
                    await client.call_tool("change_column_workflow_stage", {**base, field: invalid})
        columns.change_workflow_stage.assert_not_called()
        with pytest.raises(ToolError):
            await client.call_tool("change_column_workflow_stage", {"project_uid": "board", "column_uid": "column"})
        columns.change_workflow_stage.assert_not_called()

        for result, error, code in (
            (False, None, "NF2004"),
            (True, ProjectColumnWorkflowConflict(), "EX2001"),
            (True, ValueError("Unknown or inactive workflow stage"), "VA0000"),
        ):
            columns.change_workflow_stage.return_value = result
            columns.change_workflow_stage.side_effect = error
            with pytest.raises(ToolError, match=code):
                await client.call_tool("change_column_workflow_stage", {**base, "workflow_stage": "unknown"})
            columns.change_workflow_stage.assert_called_once_with("board", "column", "unknown")
            columns.change_workflow_stage.reset_mock()


@pytest.mark.parametrize("field", ["workflow_stage", "expected_workflow_stage"])
@pytest.mark.parametrize("value", [True, False, 1, [], {}])
def test_direct_workflow_tool_rejects_non_string_values(field, value):
    service = SimpleNamespace(project_column=SimpleNamespace(change_workflow_stage=Mock()))
    arguments = {"workflow_stage": "review", field: value}
    with pytest.raises(ValidationError, match="VA0000"):
        ProjectMcp.change_column_workflow_stage("board", "column", service=service, **arguments)
    service.project_column.change_workflow_stage.assert_not_called()
