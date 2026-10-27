"""Exercise the real MCP timeline against current SQL card authority."""

from types import SimpleNamespace
import pytest
from fastmcp import Client
from langboard.mcp_integration.Server import McpServer
from langboard.mcp_integration.Tool import McpTool
from langboard.mcp_tools import CardMcp
from langboard.middlewares.McpAuthMiddleware import mcp_auth_context
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.domain.models import (
    Bot,
    Card,
    CardComment,
    CardCommentCompaction,
    CardCommentReaction,
    EmployeeMembershipPolicy,
    InternalBot,
    ProjectActivity,
    WorkflowRule,
)
from langboard_shared.domain.models.ProjectActivity import ProjectActivityType


@pytest.mark.parametrize("board", ["sqlite://", "postgresql-test"], indirect=True)
async def test_real_mcp_timeline_checks_current_owner_and_preserves_evidence(board, monkeypatch):
    assert CardMcp.get_card_timeline
    _, actor, project, _, _, columns, *_ = board
    engine = DbEngine.get_main_engine()
    monkeypatch.setattr(DbEngine, "get_readonly_engine", DbEngine.get_main_engine)
    # Timeline capability projection reads current workflow rules on the board.
    WorkflowRule.__table__.create(engine, checkfirst=True)
    for model in (Bot, Card, CardComment, CardCommentReaction, EmployeeMembershipPolicy, ProjectActivity, InternalBot, CardCommentCompaction):
        model.__table__.create(engine)
    with DbSession.atomic() as db:
        card = Card(project_id=project.id, project_column_id=columns[0].id, title="Private evidence",
                    visibility="PRIVATE", owner_user_id=actor.id, created_by_user_id=actor.id)
        db.insert(card)
        comment = CardComment(card_id=card.id, user_id=actor.id, content={"content": "Original comment"})
        db.insert(comment)
        move = ProjectActivity(project_id=project.id, card_id=card.id, user_id=actor.id,
                               activity_type=ProjectActivityType.CardMoved,
                               activity_history={"from_column": {"name": "Review"}, "column": {"name": "Doing"},
                                                 "recorder": {"uid": actor.get_uid(), "type": "user", "firstname": "Original mover", "email": "secret@example.invalid"}})
        db.insert(move)
    name = "get_card_timeline"
    metadata = {name: McpTool.get_tool(name)}
    monkeypatch.setattr(McpTool, "get_tools", lambda: metadata)
    monkeypatch.setattr(McpTool, "get_tool", lambda key: metadata.get(key))
    _, server = McpServer.get_http_app("agent")
    group = SimpleNamespace(activated_at=object(), tools=[name])
    token = mcp_auth_context.set({"tool_group": group, "user_or_bot": actor})
    try:
        async with Client(server) as client:
            schema = (await client.list_tools())[0].inputSchema
            assert schema["properties"]["before"]["anyOf"][0]["maxLength"] == 64
            result = await client.call_tool(name, {"project_uid": project.get_uid(), "card_uid": card.get_uid()})
            assert not result.is_error
            text = str(result.data)
            assert "Original comment" in text and "Original mover" in text and "Review" in text and "Doing" in text
            assert f"activity:{move.get_uid()}" in text and f"comment:{comment.get_uid()}" in text
            assert "secret@example.invalid" not in text
            with DbSession.atomic() as db:
                card.owner_user_id = project.owner_id
                card.created_by_user_id = project.owner_id
                db.update(card)
            denied = await client.call_tool(name, {"project_uid": project.get_uid(), "card_uid": card.get_uid()}, raise_on_error=False)
            assert denied.is_error
            assert "CARD_UNAVAILABLE" in str(denied.content)
            assert "Original comment" not in str(denied.content)
    finally:
        mcp_auth_context.reset(token)
