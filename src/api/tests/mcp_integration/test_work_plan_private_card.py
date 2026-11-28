"""Real MCP plan tools must reject another member's private card before writes."""

import importlib
import re
from contextlib import closing
from types import SimpleNamespace
import pytest
from fastapi import FastAPI, Request
from fastmcp import Client
from httpx import ASGITransport, AsyncClient
from langboard.card_workspace.application.work_plan import WorkPlan, WorkPlanRejected, WorkPlanService
from langboard.mcp_integration.Server import McpServer
from langboard.mcp_integration.Tool import McpTool
from langboard.mcp_tools import CardMcp
from langboard.middlewares.McpAuthMiddleware import mcp_auth_context
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.security import AuthSecurity
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import (
    Card,
    CardMetadata,
    CardRelationship,
    Checkitem,
    CheckitemTimerRecord,
    Checklist,
    GlobalCardRelationshipType,
    ProjectAssignedUser,
    ProjectRole,
    User,
)
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.factory.CheckitemService import CheckitemService
from langboard_shared.Env import Env
from sqlalchemy import func, select


@pytest.mark.parametrize("mutation", ["checklist", "cardify"])
async def test_private_card_preview_and_apply_use_current_actor_through_mcp(board, monkeypatch, caplog, mutation):
    engine = DbEngine.get_main_engine()
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    monkeypatch.setattr(type(Env), "CARD_INTERNAL_ACCESS_MODE", property(lambda _: "project_members"))
    for model in (Card, Checklist, Checkitem, CheckitemTimerRecord, GlobalCardRelationshipType, CardRelationship):
        model.__table__.create(engine)
    with DbSession.atomic() as db:
        owner = db.exec(SqlBuilder.select.table(User).where(User.id == board[2].owner_id)).first()
        board[4].actions = ["read", "card_update"]
        db.update(board[4])
        db.insert(ProjectAssignedUser(project_id=board[2].id, user_id=owner.id))
        db.insert(ProjectRole(project_id=board[2].id, user_id=owner.id, actions=["read", "card_update"]))
        card = Card(
            project_id=board[2].id,
            project_column_id=board[5][0].id,
            title="Private work",
            visibility="PRIVATE",
            owner_user_id=owner.id,
            created_by_user_id=owner.id,
        )
        db.insert(card)
        if mutation == "cardify":
            checklist = Checklist(card_id=card.id, title="Private steps", order=0)
            db.insert(checklist)
            item = Checkitem(checklist_id=checklist.id, title="Private task")
            db.insert(item)
    names = ("preview_card_work_plan", "apply_card_work_plan")
    assert all(getattr(CardMcp, name) for name in names)
    metadata = {name: McpTool.get_tool(name) for name in names}
    monkeypatch.setattr(McpTool, "get_tools", lambda: metadata)
    monkeypatch.setattr(McpTool, "get_tool", lambda name: metadata.get(name))
    _, server = McpServer.get_http_app("agent")
    plan = {
        "project_uid": board[2].get_uid(),
        "anchor_card_uid": card.get_uid(),
        "new_checklists": [{"target_card_ref": card.get_uid(), "title": "Steps", "items": ["Verify"]}],
    }
    if mutation == "cardify":
        plan.pop("new_checklists")
        plan["cardify_checkitems"] = [
            {
                "client_ref": "cardify:task",
                "source_card_uid": card.get_uid(),
                "checkitem_uid": item.get_uid(),
                "project_column_uid": board[5][0].get_uid(),
                "title": "Private task",
            }
        ]
    # This is the actual WorkPlanService and current database visibility policy;
    # no constructor, card resolver, checklist writer, or role check is replaced.
    tool_group = SimpleNamespace(activated_at=object(), tools=list(names))
    token = mcp_auth_context.set({"tool_group": tool_group, "user_or_bot": board[1]})
    try:
        async with Client(server) as client:
            for name, arguments in (
                (names[0], {"project_uid": plan["project_uid"], "plan": plan}),
                (
                    names[1],
                    {
                        "project_uid": plan["project_uid"],
                        "plan": plan,
                        "expected_revision": "a" * 64,
                        "request_id": "private-denied",
                    },
                ),
            ):
                result = await client.call_tool(name, arguments, raise_on_error=False)
                assert result.is_error
                assert card.title not in str(result)
                if name == names[0]:
                    assert "CARD_UNAVAILABLE" in str(result)
                else:
                    assert "Work plan card is unavailable" in caplog.text
                caplog.clear()
    finally:
        mcp_auth_context.reset(token)
    # The real owner can preview the identical card/plan; denial above was card
    # visibility, not an unavailable tool or an invalid plan.
    token = mcp_auth_context.set({"tool_group": tool_group, "user_or_bot": owner})
    try:
        async with Client(server) as client:
            result = await client.call_tool(
                names[0], {"project_uid": plan["project_uid"], "plan": plan}, raise_on_error=False
            )
            assert not result.is_error, result
    finally:
        mcp_auth_context.reset(token)
    with engine.connect() as connection:
        assert connection.execute(select(func.count()).select_from(Checklist.__table__)).scalar_one() == int(
            mutation == "cardify"
        )
        assert connection.execute(select(func.count()).select_from(Checkitem.__table__)).scalar_one() == int(
            mutation == "cardify"
        )


@pytest.mark.parametrize(
    "case",
    ["active", "new", "archive_anchor", "archive_endpoint", "hidden", "deleted", "foreign", "invalid_ref", "duplicate"],
)
async def test_native_preview_safe_rejection_and_no_writes(board, monkeypatch, case):
    engine = DbEngine.get_main_engine()
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    monkeypatch.setattr(type(Env), "CARD_INTERNAL_ACCESS_MODE", property(lambda _: "project_members"))
    for model in (Card, Checklist, Checkitem, GlobalCardRelationshipType, CardRelationship):
        model.__table__.create(engine)
    with DbSession.atomic() as db:
        anchor = Card(project_id=board[2].id, project_column_id=board[5][0].id, title="Anchor")
        endpoint = Card(project_id=board[2].id, project_column_id=board[5][0].id, title="Sensitive endpoint")
        if case == "archive_anchor":
            anchor.archived_at = SafeDateTime.now()
        if case in {"archive_endpoint", "hidden", "deleted", "foreign"}:
            endpoint.archived_at = SafeDateTime.now()
        if case == "hidden":
            endpoint.visibility, endpoint.owner_user_id = "PRIVATE", board[2].owner_id
            endpoint.created_by_user_id = board[2].owner_id
        if case == "deleted":
            endpoint.deleted_at = SafeDateTime.now()
        if case == "foreign":
            endpoint.project_id = 11
        db.insert(anchor)
        db.insert(endpoint)
        relation_type = GlobalCardRelationshipType(parent_name="Contains", child_name="Part of")
        db.insert(relation_type)
        if case == "duplicate":
            db.insert(
                CardRelationship(
                    relationship_type_id=relation_type.id, card_id_parent=anchor.id, card_id_child=endpoint.id
                )
            )
    name = "preview_card_work_plan"
    metadata = {name: McpTool.get_tool(name)}
    monkeypatch.setattr(McpTool, "get_tools", lambda: metadata)
    monkeypatch.setattr(McpTool, "get_tool", lambda name: metadata.get(name))
    _, server = McpServer.get_http_app("agent")
    plan = {
        "project_uid": board[2].get_uid(),
        "anchor_card_uid": anchor.get_uid(),
        "new_checklists": [{"target_card_ref": endpoint.get_uid(), "title": "Steps", "items": ["Verify"]}],
        "add_edges": [
            {
                "parent_ref": anchor.get_uid(),
                "child_ref": endpoint.get_uid(),
                "relationship_type_uid": relation_type.get_uid(),
            }
        ],
    }
    if case in {"new", "invalid_ref"}:
        plan["new_cards"] = [{"client_ref": "new:child", "title": "Child"}]
        plan["add_edges"] = [
            {"parent_ref": anchor.get_uid(), "child_ref": "new:child", "relationship_type_uid": relation_type.get_uid()}
        ]
        plan["new_checklists"][0]["target_card_ref"] = "new:child" if case == "new" else "new:missing"
    token = mcp_auth_context.set(
        {"tool_group": SimpleNamespace(activated_at=True, tools=[name]), "user_or_bot": board[1]}
    )
    try:
        async with Client(server) as client:
            result = await client.call_tool(
                name, {"project_uid": plan["project_uid"], "plan": plan}, raise_on_error=False
            )
    finally:
        mcp_auth_context.reset(token)
    if case in {"active", "new"}:
        assert not result.is_error, result
        assert len(result.structured_content["revision"]) == 64
    else:
        assert result.is_error
        text = " ".join(item.text for item in result.content if hasattr(item, "text"))
        code = (
            "CARD_ARCHIVED"
            if case.startswith("archive")
            else "WORK_PLAN_INVALID"
            if case in {"invalid_ref", "duplicate"}
            else "CARD_UNAVAILABLE"
        )
        assert code in text, text
        assert "Sensitive endpoint" not in text
        assert re.search(r"request_id=[0-9a-f]{32}", text), text
        if case.startswith("archive"):
            assert "restore" in text.lower()
    route = importlib.import_module("langboard.routes.mcp.McpApi")
    group = SimpleNamespace(activated_at=True, tools=[name], user_id=None)
    monkeypatch.setattr(
        route,
        "DomainService",
        lambda: SimpleNamespace(mcp_tool_group=SimpleNamespace(get_by_id_like=lambda _: group), close=lambda: None),
    )
    monkeypatch.setattr(route.McpServer, "mcp", server)
    app = FastAPI()

    @app.post("/mcp/tools/{tool_name}")
    async def dispatch(tool_name: str, request: Request):
        request.scope["auth"] = board[1]
        return await route.execute_mcp_tool(tool_name, request)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/mcp/tools/{name}",
            json={"project_uid": plan["project_uid"], "plan": plan},
            headers={AuthSecurity.MCP_TOOL_GROUP_UID_HEADER: "group", "X-Request-ID": "unsafe-user-value"},
        )
    if case in {"active", "new"}:
        assert response.status_code == 200, response.text
        assert len(response.json()["result"]["revision"]) == 64
    else:
        assert response.status_code == 400, response.text
        payload = response.json()
        assert payload["code"] == code
        assert payload["outcome"] == "not_applied" and payload["retryable"] is False
        assert re.fullmatch(r"[0-9a-f]{32}", payload["request_id"])
        assert response.headers["X-Request-ID"] == payload["request_id"]
        assert "Sensitive endpoint" not in response.text and "unsafe-user-value" not in response.text
        if case == "invalid_ref":
            safe_id = "12345678-1234-5678-1234-567812345678"
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                correlated = await client.post(
                    f"/mcp/tools/{name}",
                    json={"project_uid": plan["project_uid"], "plan": plan},
                    headers={AuthSecurity.MCP_TOOL_GROUP_UID_HEADER: "group", "X-Request-ID": safe_id},
                )
            assert correlated.status_code == 400
            assert correlated.json()["request_id"] == safe_id.replace("-", "")
            assert correlated.headers["X-Request-ID"] == safe_id.replace("-", "")
    with engine.connect() as connection:
        for model, expected in (
            (Card, 2),
            (Checklist, 0),
            (Checkitem, 0),
            (CardRelationship, int(case == "duplicate")),
        ):
            assert connection.execute(select(func.count()).select_from(model.__table__)).scalar_one() == expected


@pytest.mark.parametrize(
    "case",
    [
        "unchanged",
        "title_mismatch",
        "missing_item",
        "foreign_checklist",
        "cardified",
        "missing_column",
        "foreign_column",
        "archived_column",
        "deleted_column",
        "unknown_error",
    ],
)
async def test_native_cardification_preview_rejection_boundary(board, monkeypatch, case):
    engine = DbEngine.get_main_engine()
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    monkeypatch.setattr(type(Env), "CARD_INTERNAL_ACCESS_MODE", property(lambda _: "project_members"))
    models = (
        Card,
        Checklist,
        Checkitem,
        CheckitemTimerRecord,
        GlobalCardRelationshipType,
        CardRelationship,
        CardMetadata,
    )
    for model in models:
        model.__table__.create(engine)
    column = board[5][1]
    with DbSession.atomic() as db:
        anchor = Card(project_id=board[2].id, project_column_id=board[5][0].id, title="Anchor")
        other = Card(project_id=board[2].id, project_column_id=board[5][0].id, title="Other source")
        db.insert(anchor)
        db.insert(other)
        checklist = Checklist(card_id=other.id if case == "foreign_checklist" else anchor.id, title="Source steps")
        db.insert(checklist)
        item = Checkitem(
            checklist_id=checklist.id,
            title="Sensitive current source title",
            cardified_id=other.id if case == "cardified" else None,
        )
        db.insert(item)
        item_uid = item.get_uid()
        relation_type = GlobalCardRelationshipType(parent_name="Contains", child_name="Part of")
        db.insert(relation_type)
        if case == "missing_item":
            db.delete(item, purge=True)
        if case == "missing_column":
            db.delete(column, purge=True)
        elif case in {"foreign_column", "archived_column", "deleted_column"}:
            if case == "foreign_column":
                column.project_id = 11
            elif case == "archived_column":
                column.is_archive = True
            else:
                column.deleted_at = SafeDateTime.now()
            db.update(column)
    plan = WorkPlan(
        project_uid=board[2].get_uid(),
        anchor_card_uid=anchor.get_uid(),
        cardify_checkitems=[
            {
                "client_ref": "cardify:task",
                "source_card_uid": anchor.get_uid(),
                "checkitem_uid": item_uid,
                "project_column_uid": column.get_uid(),
                "title": "Changed proposed title" if case == "title_mismatch" else item.title,
            }
        ],
        add_edges=[
            {
                "parent_ref": anchor.get_uid(),
                "child_ref": "cardify:task",
                "relationship_type_uid": relation_type.get_uid(),
            }
        ],
        new_checklists=[{"target_card_ref": "cardify:task", "title": "Next steps", "items": ["Verify"]}],
    )

    def snapshot():
        with engine.connect() as connection:
            counts = {
                model.__name__: connection.execute(select(func.count()).select_from(model.__table__)).scalar_one()
                for model in models
            }
            sources = connection.execute(select(Checkitem.id, Checkitem.title, Checkitem.cardified_id)).all()
        return counts, sources

    before = snapshot()
    assert before[0] == {
        "Card": 2,
        "Checklist": 1,
        "Checkitem": int(case != "missing_item"),
        "CheckitemTimerRecord": 0,
        "GlobalCardRelationshipType": 1,
        "CardRelationship": 0,
        "CardMetadata": 0,
    }
    if case == "unknown_error":

        def unexpected_failure(*_):
            raise ValueError("private-source-error-detail")

        monkeypatch.setattr(CheckitemService, "get_by_id_like", unexpected_failure)
    rejection = None
    with closing(DomainService()) as service:
        try:
            reviewed = WorkPlanService(board[1], service).preview(plan)
        except ValueError as error:
            rejection = error
    name = "preview_card_work_plan"
    metadata = {name: McpTool.get_tool(name)}
    monkeypatch.setattr(McpTool, "get_tools", lambda: metadata)
    monkeypatch.setattr(McpTool, "get_tool", lambda name: metadata.get(name))
    _, server = McpServer.get_http_app("agent")
    token = mcp_auth_context.set(
        {"tool_group": SimpleNamespace(activated_at=True, tools=[name]), "user_or_bot": board[1]}
    )
    arguments = {"project_uid": plan.project_uid, "plan": plan.model_dump(mode="json")}
    try:
        async with Client(server) as client:
            result = await client.call_tool(name, arguments, raise_on_error=False)
    finally:
        mcp_auth_context.reset(token)
    route = importlib.import_module("langboard.routes.mcp.McpApi")
    group = SimpleNamespace(activated_at=True, tools=[name], user_id=None)
    monkeypatch.setattr(
        route,
        "DomainService",
        lambda: SimpleNamespace(mcp_tool_group=SimpleNamespace(get_by_id_like=lambda _: group), close=lambda: None),
    )
    monkeypatch.setattr(route.McpServer, "mcp", server)
    app = FastAPI()

    @app.post("/mcp/tools/{tool_name}")
    async def dispatch(tool_name: str, request: Request):
        request.scope["auth"] = board[1]
        return await route.execute_mcp_tool(tool_name, request)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/mcp/tools/{name}",
            json=arguments,
            headers={AuthSecurity.MCP_TOOL_GROUP_UID_HEADER: "group", "X-Request-ID": "unsafe-user-value"},
        )
    assert snapshot() == before, "Preview must preserve row counts, source titles, and cardification state"
    payload = response.json()
    assert response.status_code == (200 if case == "unchanged" else 500 if case == "unknown_error" else 400), payload
    if case == "unchanged":
        assert rejection is None
        assert not result.is_error, result
        assert reviewed == result.structured_content == payload["result"]
        assert reviewed["counts"] == {"cards": 0, "cardifications": 1, "checklists": 1}
    else:
        assert result.is_error
        text = " ".join(part.text for part in result.content if hasattr(part, "text"))
        assert re.fullmatch(r"[0-9a-f]{32}", payload["request_id"])
        assert response.headers["X-Request-ID"] == payload["request_id"]
        for sensitive in (item.title, "Other source", "private-source-error-detail", "unsafe-user-value"):
            assert sensitive not in text + response.text
        if case == "unknown_error":
            assert type(rejection) is ValueError
            assert payload["outcome"] == "unknown"
            assert "WORK_PLAN_INVALID" not in text + response.text
        else:
            assert isinstance(rejection, WorkPlanRejected)
            assert rejection.code == payload["code"] == "WORK_PLAN_INVALID"
            assert rejection.next_action == payload["next_action"] == "read_and_review"
            assert "WORK_PLAN_INVALID" in text and re.search(r"request_id=[0-9a-f]{32}", text)
            assert payload["outcome"] == "not_applied" and payload["retryable"] is False
