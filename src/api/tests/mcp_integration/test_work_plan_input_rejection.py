"""Only validation of the native input plan proves apply never entered its owner."""

import importlib
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from fastapi import FastAPI, Request
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ValidationError
from fastmcp.tools import Tool
from httpx import ASGITransport, AsyncClient
from langboard.card_workspace.application.work_plan import WorkPlan, WorkPlanRejected
from langboard.mcp_integration import McpTool
from langboard.mcp_integration.Receipts import MutationReceiptMiddleware
from langboard.mcp_tools import CardMcp
from langboard_shared.core.db import DbSession
from langboard_shared.core.security import AuthSecurity


CASES = [
    {"add_edges": [{"parent_ref": "anchor", "child_ref": "anchor", "relationship_type_uid": "type"}]},
    {"add_edges": [{"parent_ref": " anchor ", "child_ref": "anchor", "relationship_type_uid": "type"}]},
    {"new_cards": [{"client_ref": "new:", "title": "private input"}]},
    {"new_cards": [{"client_ref": "new:a", "title": "A"}] * 2},
    {
        "cardify_checkitems": [
            {
                "client_ref": "cardify:",
                "source_card_uid": "anchor",
                "checkitem_uid": "i",
                "project_column_uid": "c",
                "title": "A",
            }
        ]
    },
    {
        "cardify_checkitems": [
            {
                "client_ref": f"cardify:{i}",
                "source_card_uid": "anchor",
                "checkitem_uid": "i",
                "project_column_uid": "c",
                "title": "A",
            }
            for i in range(2)
        ]
    },
    {
        "add_edges": [{"parent_ref": "anchor", "child_ref": "other", "relationship_type_uid": "type"}],
        "remove_relationship_uids": [f"e{i}" for i in range(25)],
    },
    {"remove_relationship_uids": ["edge", "edge"]},
    {"add_edges": [{"parent_ref": "anchor", "child_ref": "other", "relationship_type_uid": "type"}] * 2},
    {
        "cardify_checkitems": [
            {
                "client_ref": "cardify:a",
                "source_card_uid": "anchor",
                "checkitem_uid": str(i),
                "project_column_uid": "c",
                "title": "A",
            }
            for i in range(2)
        ]
    },
    {},
    {"new_cards": [{"client_ref": "new:a", "title": "A"}], "private_extra": "private input"},
]
VALID = {"new_cards": [{"client_ref": "new:a", "title": "A"}]}


def server_for(monkeypatch, name, mode):
    effects = []
    owner = Mock()

    def run(*args):
        effects.append("owner")
        if mode == "pydantic":
            WorkPlan.model_validate({})
        if mode == "value":
            raise ValueError("private input")
        if mode == "validation":
            raise ValidationError("private input")
        if mode == "rejected":
            raise WorkPlanRejected("WORK_PLAN_INVALID", "private input", "revise_plan")
        if mode == "generic":
            raise RuntimeError("private input")
        if mode == "output":
            return []
        return {"ok": True}

    owner.preview.side_effect = run
    owner.apply.side_effect = run
    constructor = Mock(return_value=owner)
    monkeypatch.setattr(CardMcp, "WorkPlanService", constructor)
    monkeypatch.setattr(DbSession, "use", Mock(side_effect=AssertionError("SQL entered")))
    monkeypatch.setattr(DbSession, "atomic", Mock(side_effect=AssertionError("SQL entered")))
    # Use the actual registered signature, excluding only native dependencies.
    from functools import wraps
    from inspect import signature

    native = McpTool.get_tool(name)["handler"]

    @wraps(native)
    def dispatch(**kwargs):
        return native(**kwargs, user_or_bot=object(), service=object())

    dispatch.__signature__ = signature(native).replace(
        parameters=[p for n, p in signature(native).parameters.items() if n not in {"user_or_bot", "service"}]
    )
    server = FastMCP("work-plan-input", strict_input_validation=True, mask_error_details=True)
    server.add_tool(Tool.from_function(dispatch, name=name))
    return server, effects, constructor


def arguments(name, plan):
    value = {"project_uid": "project", "plan": {"project_uid": "project", "anchor_card_uid": "anchor", **plan}}
    if name == "apply_card_work_plan":
        value.update(expected_revision="a" * 64, request_id="original-request")
    return value


@pytest.mark.parametrize("plan", CASES)
@pytest.mark.parametrize("name", ["apply_card_work_plan", "preview_card_work_plan"])
async def test_actual_fastmcp_invalid_apply_is_definitely_not_applied(monkeypatch, plan, name):
    server, effects, owner = server_for(monkeypatch, name, "success")
    server.add_middleware(MutationReceiptMiddleware())
    async with Client(server) as client:
        result = await client.call_tool(name, arguments(name, plan), raise_on_error=False)
    assert result.is_error
    if name == "apply_card_work_plan":
        assert result.meta["mutation_receipt"]["outcome"] == "not_applied"
    assert "private input" not in str(result.content)
    assert "WORK_PLAN_INVALID" in str(result.content)
    owner.assert_not_called()
    assert effects == []
    DbSession.use.assert_not_called()
    DbSession.atomic.assert_not_called()


@pytest.mark.parametrize("mode", ["pydantic", "value", "validation", "rejected", "generic", "output", "success"])
async def test_actual_fastmcp_apply_after_owner_is_unknown(monkeypatch, mode):
    server, effects, owner = server_for(monkeypatch, "apply_card_work_plan", mode)
    server.add_middleware(MutationReceiptMiddleware())
    async with Client(server) as client:
        result = await client.call_tool(
            "apply_card_work_plan", arguments("apply_card_work_plan", VALID), raise_on_error=False
        )
    assert result.meta["mutation_receipt"]["outcome"] == ("applied" if mode == "success" else "unknown")
    assert effects == ["owner"]
    owner.assert_called_once()


@pytest.mark.parametrize(
    "name,mode",
    [
        (name, mode)
        for name in ["preview_card_work_plan", "apply_card_work_plan"]
        for mode in [
            *[f"invalid:{i}" for i in range(len(CASES))],
            "pydantic",
            "value",
            "validation",
            "rejected",
            "generic",
            "output",
            "success",
            "schema_project_uid",
            "schema_expected_revision",
            "schema_request_id",
            "schema_extra",
        ]
        if not (name.startswith("preview") and mode in {"rejected", "schema_expected_revision", "schema_request_id"})
    ],
)
async def test_native_http_input_and_after_owner_outcomes(monkeypatch, name, mode):
    route = importlib.import_module("langboard.routes.mcp.McpApi")
    server, effects, owner = server_for(monkeypatch, name, mode)
    group = SimpleNamespace(activated_at=True, tools=[name], user_id=None)
    closed = []
    monkeypatch.setattr(
        route,
        "DomainService",
        lambda: SimpleNamespace(
            mcp_tool_group=SimpleNamespace(get_by_id_like=lambda _: group), close=lambda: closed.append(True)
        ),
    )
    monkeypatch.setattr(route, "User", SimpleNamespace)
    monkeypatch.setattr(route.McpTool, "get_tool", lambda _: {"accessible_type": "user"})
    monkeypatch.setattr(route.McpServer, "mcp", server)
    app = FastAPI()

    @app.post("/plan")
    async def dispatch(request: Request):
        request.scope["auth"] = SimpleNamespace(id=1)
        return await route.execute_mcp_tool(name, request)

    request_id = "00000000000040008000000000000001"
    args = arguments(name, CASES[int(mode.split(":")[1])] if mode.startswith("invalid:") else VALID)
    if mode.startswith("schema_"):
        args[mode.removeprefix("schema_")] = 123
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/plan", json=args, headers={AuthSecurity.MCP_TOOL_GROUP_UID_HEADER: "group", "X-Request-ID": request_id}
        )
    legacy_validation = mode == "validation" or mode.startswith("schema_")
    assert response.status_code == (
        400 if mode.startswith("invalid:") or legacy_validation else 200 if mode == "success" else 500
    )
    if legacy_validation:
        assert (response.json().get("outcome") == "not_applied") is name.startswith("preview")
    elif mode != "success":
        assert response.json()["outcome"] == ("not_applied" if mode.startswith("invalid:") else "unknown")
        assert response.json()["request_id"] == request_id
        assert response.headers["X-Request-ID"] == request_id
        assert "private input" not in response.text
    assert effects == ([] if mode.startswith("invalid:") or mode.startswith("schema_") else ["owner"])
    if mode.startswith("invalid:") or mode.startswith("schema_"):
        owner.assert_not_called()
    assert closed == [True]
    assert route.mcp_auth_context.get() is None


@pytest.mark.parametrize("profile", ["agent", "compatibility"])
@pytest.mark.parametrize("mode", ["invalid", "success", "output", "pydantic", "value", "validation", "rejected"])
async def test_registered_providers_keep_grants_and_mutation_classification(monkeypatch, profile, mode):
    from langboard.mcp_integration.Server import McpServer
    from langboard.middlewares.McpAuthMiddleware import mcp_auth_context
    from langboard_shared.domain.models import User

    name = "apply_card_work_plan"
    metadata = McpTool.get_tool(name)
    _, effects, owner = server_for(monkeypatch, name, mode)
    if mode == "success":

        def success(*args):
            effects.append("owner")
            return {
                "graph": None,
                "cardifications": [],
                "checklists": [],
                "applied_revision": "a" * 64,
                "all_succeeded": True,
                "replayed": False,
            }

        owner.return_value.apply.side_effect = success
    monkeypatch.setattr(McpTool, "get_tools", lambda: {name: metadata})
    monkeypatch.setattr(McpTool, "get_tool", lambda requested: metadata if requested == name else None)
    monkeypatch.setattr(McpServer, "_validate_auth", lambda *args: True)
    monkeypatch.setattr(McpServer, "_validate_role", lambda *args, **kwargs: True)
    _, server = McpServer.get_http_app(profile)
    token = mcp_auth_context.set(
        {
            "user_or_bot": User(
                id=1, firstname="Test", lastname="User", email="test@example.test", password="test-only"
            ),
            "tool_group": SimpleNamespace(activated_at=True, tools=[name]),
        }
    )
    try:
        async with Client(server) as client:
            args = arguments(name, {} if mode == "invalid" else VALID)
            if profile == "compatibility" and mode == "pydantic":
                from mcp.shared.exceptions import MCPError

                with pytest.raises(MCPError, match="Invalid request parameters"):
                    await client.call_tool(name, args, raise_on_error=False)
                assert effects == ["owner"]
                return
            result = await client.call_tool(
                name,
                args,
                raise_on_error=False,
            )
            receipt = (result.meta or {}).get("mutation_receipt")
            assert result.is_error is (mode != "success")
            if profile == "compatibility":
                assert receipt is None
            else:
                assert receipt["outcome"] == (
                    "not_applied" if mode == "invalid" else "applied" if mode == "success" else "unknown"
                )
            assert effects == ([] if mode == "invalid" else ["owner"])
            # The native group still denies an ungranted call; no owner entry.
            effects.clear()
            mcp_auth_context.set(
                {
                    "user_or_bot": User(
                        id=1, firstname="Test", lastname="User", email="test@example.test", password="test-only"
                    ),
                    "tool_group": SimpleNamespace(activated_at=True, tools=[]),
                }
            )
            denied = await client.call_tool(
                name,
                args,
                raise_on_error=False,
            )
            assert denied.is_error
            assert not (denied.meta or {}).get("mutation_receipt")
            assert effects == []
    finally:
        mcp_auth_context.reset(token)
