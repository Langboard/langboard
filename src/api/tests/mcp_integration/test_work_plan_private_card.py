"""Real MCP plan tools must reject another member's private card before writes."""

from types import SimpleNamespace
from fastmcp import Client
from langboard.mcp_integration.Server import McpServer
from langboard.mcp_integration.Tool import McpTool
from langboard.mcp_tools import CardMcp
from langboard.middlewares.McpAuthMiddleware import mcp_auth_context
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.domain.models import (
    Card,
    CardRelationship,
    Checkitem,
    Checklist,
    GlobalCardRelationshipType,
    ProjectAssignedUser,
    ProjectRole,
    User,
)
from langboard_shared.Env import Env
from sqlalchemy import func, select


async def test_private_card_preview_and_apply_use_current_actor_through_mcp(board, monkeypatch, caplog):
    engine = DbEngine.get_main_engine()
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    monkeypatch.setattr(type(Env), "CARD_INTERNAL_ACCESS_MODE", property(lambda _: "project_members"))
    for model in (Card, Checklist, Checkitem, GlobalCardRelationshipType, CardRelationship):
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
        assert connection.execute(select(func.count()).select_from(Checklist.__table__)).scalar_one() == 0
        assert connection.execute(select(func.count()).select_from(Checkitem.__table__)).scalar_one() == 0
