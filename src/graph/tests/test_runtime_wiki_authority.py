"""Wiki selectors require current native visibility on execution and resume."""

from copy import deepcopy
from uuid import uuid4
import pytest
from langboard_shared.core.types import SafeDateTime, SnowflakeID
from langboard_shared.domain.models import (
    Bot,
    BotDefaultScopeBranch,
    ProjectAssignedUser,
    ProjectBotScope,
    ProjectRole,
    ProjectWiki,
    ProjectWikiAssignedUser,
)
from langboard_shared.domain.models.BaseBotModel import BotPlatform, BotPlatformRunningType
from sqlalchemy import delete, update
from sqlalchemy.orm import Session
from test_chat_history_authority import history_db  # noqa: F401
from test_runtime_authority import authority_graph, user_tweaks  # noqa: F401
from langboard_graph.core.graph.default import graph


@pytest.fixture
def wiki_graph(authority_graph, history_db):  # noqa: F811
    for model in (ProjectWiki, ProjectWikiAssignedUser):
        model.__table__.create(history_db)
    with Session(history_db) as db:
        db.add(ProjectAssignedUser(id=70, project_id=30, user_id=2))
        db.add(ProjectRole(id=71, project_id=30, user_id=2, actions=["read"]))
        db.add(ProjectWiki(id=60, project_id=30, title="Selected wiki"))
        db.commit()
    tweaks = user_tweaks(2, 30)
    tweaks["LangboardCalledVariablesComponent"]["rest_data"] = {
        "project_uid": SnowflakeID(30).to_short_code(),
        "chat_scope": "project_wiki",
        "project_wiki_uid": SnowflakeID(60).to_short_code(),
    }
    return authority_graph, tweaks


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["no_wiki", "no_project", "invalid_wiki_type"])
async def test_wiki_scope_requires_matching_selectors_before_checkpointing(wiki_graph, case):
    saver, tweaks = wiki_graph
    variables = tweaks["LangboardCalledVariablesComponent"]
    if case == "no_wiki":
        variables["rest_data"].pop("project_wiki_uid")
    elif case == "no_project":
        variables.pop("project_uid")
        variables["rest_data"].pop("project_uid")
    else:
        variables["rest_data"]["project_wiki_uid"] = {"uid": "untrusted"}
    thread = uuid4().hex
    with pytest.raises(ValueError, match="wiki scope"):
        await graph.run_default_graph("run", tweaks, thread, thread)
    assert await saver.aget_tuple({"configurable": {"thread_id": thread}}) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["public", "assigned", "private", "foreign", "deleted", "missing"])
async def test_wiki_initial_scope_requires_current_visibility(wiki_graph, history_db, case):  # noqa: F811
    saver, tweaks = wiki_graph
    with Session(history_db) as db:
        if case in {"assigned", "private"}:
            db.execute(update(ProjectWiki).values(is_public=False))
        if case == "assigned":
            db.add(ProjectWikiAssignedUser(id=80, project_assigned_id=70, project_wiki_id=60, user_id=2))
        elif case == "foreign":
            db.execute(update(ProjectWiki).values(project_id=31))
        elif case == "deleted":
            db.execute(update(ProjectWiki).values(deleted_at=SafeDateTime.now()))
        elif case == "missing":
            db.execute(delete(ProjectWiki))
        db.commit()
    thread = uuid4().hex
    if case in {"public", "assigned"}:
        result = await graph.run_default_graph("run", tweaks, thread, thread)
        assert result.interrupts
    else:
        with pytest.raises(ValueError, match="wiki"):
            await graph.run_default_graph("run", tweaks, thread, thread)
        assert await saver.aget_tuple({"configurable": {"thread_id": thread}}) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["current", "now_private", "assignment_revoked", "deleted", "moved"])
async def test_wiki_resume_rechecks_visibility_without_checkpoint_writes(wiki_graph, history_db, case):  # noqa: F811
    saver, tweaks = wiki_graph
    if case == "assignment_revoked":
        with Session(history_db) as db:
            db.execute(update(ProjectWiki).values(is_public=False))
            db.add(ProjectWikiAssignedUser(id=80, project_assigned_id=70, project_wiki_id=60, user_id=2))
            db.commit()
    thread = uuid4().hex
    assert (await graph.run_default_graph("run", tweaks, thread, thread)).interrupts
    config = {"configurable": {"thread_id": thread}}
    before = deepcopy(await saver.aget_tuple(config))
    with Session(history_db) as db:
        if case == "now_private":
            db.execute(update(ProjectWiki).values(is_public=False))
        elif case == "assignment_revoked":
            db.execute(delete(ProjectWikiAssignedUser))
        elif case == "deleted":
            db.execute(update(ProjectWiki).values(deleted_at=SafeDateTime.now()))
        elif case == "moved":
            db.execute(update(ProjectWiki).values(project_id=31))
        db.commit()
    decision = {"approved": True, "app_api_token": tweaks["LangboardCalledVariablesComponent"]["app_api_token"]}
    if case == "current":
        assert (await graph.run_default_graph(None, None, thread, thread, resume=decision)).message == "Resumed"
    else:
        with pytest.raises(ValueError, match="wiki"):
            await graph.run_default_graph(None, None, thread, thread, resume=decision)
        assert await saver.aget_tuple(config) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["public", "private", "foreign", "deleted", "resume_deleted"])
async def test_bot_wiki_scope_preserves_native_visibility_and_project_binding(wiki_graph, history_db, case):  # noqa: F811
    saver, tweaks = wiki_graph
    for model in (Bot, BotDefaultScopeBranch, ProjectBotScope):
        model.__table__.create(history_db)
    with Session(history_db) as db:
        db.add(
            Bot(
                id=40,
                name="Wiki bot",
                bot_uname="wiki-bot",
                app_api_token="wiki-bot-token",
                value="{}",
                platform=BotPlatform.Default,
                platform_running_type=BotPlatformRunningType.Default,
            )
        )
        db.commit()
        db.add(ProjectBotScope(id=50, project_id=30, bot_id=40, conditions=[]))
        if case == "private":
            db.execute(update(ProjectWiki).values(is_public=False))
        elif case == "foreign":
            db.execute(update(ProjectWiki).values(project_id=31))
        elif case == "deleted":
            db.execute(update(ProjectWiki).values(deleted_at=SafeDateTime.now()))
        db.commit()
    tweaks["LangboardCalledVariablesComponent"].update(current_runner_type="bot", app_api_token="wiki-bot-token")
    thread = uuid4().hex
    config = {"configurable": {"thread_id": thread}}
    if case in {"foreign", "deleted"}:
        with pytest.raises(ValueError, match="wiki"):
            await graph.run_default_graph("run", tweaks, thread, thread, model_source={"table": "bot", "id": 40})
        assert await saver.aget_tuple(config) is None
    else:
        assert (
            await graph.run_default_graph("run", tweaks, thread, thread, model_source={"table": "bot", "id": 40})
        ).interrupts
        before = deepcopy(await saver.aget_tuple(config))
        if case == "resume_deleted":
            with Session(history_db) as db:
                db.execute(update(ProjectWiki).values(deleted_at=SafeDateTime.now()))
                db.commit()
            with pytest.raises(ValueError, match="wiki"):
                await graph.run_default_graph(None, None, thread, thread, resume={"approved": True})
            assert await saver.aget_tuple(config) == before
        else:
            assert (
                await graph.run_default_graph(None, None, thread, thread, resume={"approved": True})
            ).message == "Resumed"
