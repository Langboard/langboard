"""Checkpoint execution authority must come from current authenticated accounts."""

from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4
import pytest
from langboard_shared.core.security import AuthSecurity
from langboard_shared.core.types import SnowflakeID
from langboard_shared.domain.models import Organization, Project, ProjectAssignedUser, ProjectRole
from langboard_shared.Env import Env
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import update
from sqlalchemy.orm import Session
from test_chat_history_authority import history_db  # noqa: F401
from langboard_graph.core.graph.default import graph, history, nodes
from langboard_graph.core.graph.default.history import create_langboard_history_context_prompt


def user_tweaks(actor=1, project=30):
    return {
        "Graph": {"approval_request": {"message": "Approve"}},
        "LangboardCalledVariablesComponent": {
            "current_runner_type": "user",
            "current_runner_data": {"uid": SnowflakeID(2).to_short_code()},
            "app_api_token": AuthSecurity.create_bot_one_time_token(actor),
            "project_uid": SnowflakeID(project).to_short_code(),
        },
    }


@pytest.fixture
def authority_graph(history_db, monkeypatch):  # noqa: F811
    for model in (Organization, Project, ProjectAssignedUser, ProjectRole):
        model.__table__.create(history_db)
    with Session(history_db) as db:
        db.add_all([Project(id=30, owner_id=1, title="Own"), Project(id=31, owner_id=2, title="Other")])
        db.commit()
    monkeypatch.setattr(type(Env), "CARD_INTERNAL_ACCESS_MODE", property(lambda self: "project_members"))
    saver = InMemorySaver()
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def checkpoint(**kwargs):
        yield saver

    monkeypatch.setattr(graph, "open_graph_checkpointer", checkpoint)

    @asynccontextmanager
    async def thread_lock(*args):
        yield

    monkeypatch.setattr(graph, "lock_graph_thread", thread_lock, raising=False)
    monkeypatch.setattr(nodes, "create_langboard_history_context_prompt", lambda *args: "")
    monkeypatch.setattr(history, "create_langboard_history_context_prompt", lambda *args: "")
    monkeypatch.setattr(nodes, "create_default_chat_model", lambda *args: object())

    async def invoke(*args, **kwargs):
        return SimpleNamespace(content="Resumed"), []

    async def tools(*args):
        return [], {}

    monkeypatch.setattr(nodes, "_invoke_agent", invoke)
    monkeypatch.setattr(nodes, "create_langboard_api_tool_context", tools)
    return saver


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "other_user",
        "missing_token",
        "forged_token",
        "foreign_project",
        "revoked_user",
        "revoked_project",
        "thread_reuse",
        "rejection_without_token",
    ],
)
async def test_native_checkpoint_rejects_changed_authority_without_writes(authority_graph, history_db, case):  # noqa: F811
    thread = uuid4().hex
    result = await graph.run_default_graph("run", user_tweaks(), thread, thread)
    assert result.interrupts
    config = {"configurable": {"thread_id": thread}}
    before = deepcopy(await authority_graph.aget_tuple(config))
    decision = {"approved": True, "app_api_token": AuthSecurity.create_bot_one_time_token(1)}
    if case == "other_user":
        decision["app_api_token"] = AuthSecurity.create_bot_one_time_token(2)
    elif case in {"missing_token", "rejection_without_token"}:
        decision.pop("app_api_token")
        if case == "rejection_without_token":
            decision = {"approved": False, "rejected": True}
    elif case == "forged_token":
        decision["app_api_token"] = "forged"
    elif case == "revoked_user":
        from langboard_shared.domain.models import User

        with history_db.begin() as db:
            db.execute(update(User).where(User.id == 1).values(activated_at=None))
    elif case == "revoked_project":
        with history_db.begin() as db:
            db.execute(update(Project).where(Project.id == 30).values(owner_id=2))
    with pytest.raises(ValueError):
        if case == "foreign_project":
            await graph.run_default_graph("other", user_tweaks(2, 31), thread, thread)
        elif case == "thread_reuse":
            await graph.run_default_graph("other", user_tweaks(), thread, thread)
        else:
            await graph.run_default_graph(None, None, thread, thread, resume=decision)
    assert await authority_graph.aget_tuple(config) == before


@pytest.mark.asyncio
async def test_native_checkpoint_resumes_same_current_actor_without_persisting_token(authority_graph):
    thread = uuid4().hex
    tweaks = user_tweaks()
    await graph.run_default_graph("run", tweaks, thread, thread)
    token = AuthSecurity.create_bot_one_time_token(1)
    result = await graph.run_default_graph(
        None, None, thread, thread, resume={"approved": True, "app_api_token": token}
    )
    assert result.message == "Resumed"
    async for retained in authority_graph.alist({"configurable": {"thread_id": thread}}):
        assert token not in str(retained.checkpoint)
        assert token not in str(retained.pending_writes)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["current", "hook_revoked", "bot_deleted", "token_rotated"])
async def test_bot_resume_uses_current_native_hook_authority(authority_graph, history_db, case):  # noqa: F811
    from langboard_shared.core.types import SafeDateTime
    from langboard_shared.domain.models import Bot, BotDefaultScopeBranch, ProjectBotScope
    from langboard_shared.domain.models.BaseBotModel import BotPlatform, BotPlatformRunningType
    from sqlalchemy import delete

    for model in (Bot, BotDefaultScopeBranch, ProjectBotScope):
        model.__table__.create(history_db)
    with Session(history_db) as db:
        db.add(
            Bot(
                id=40,
                name="Automation",
                bot_uname="automation",
                app_api_token="original-bot-token",
                value="{}",
                platform=BotPlatform.Default,
                platform_running_type=BotPlatformRunningType.Default,
            )
        )
        db.commit()
        db.add(ProjectBotScope(id=50, project_id=30, bot_id=40, conditions=[]))
        db.commit()
    tweaks = user_tweaks()
    tweaks["LangboardCalledVariablesComponent"].update(current_runner_type="bot", app_api_token="original-bot-token")
    thread = uuid4().hex
    await graph.run_default_graph("run", tweaks, thread, thread, model_source={"table": "bot", "id": 40})
    config = {"configurable": {"thread_id": thread}}
    before = deepcopy(await authority_graph.aget_tuple(config))
    with history_db.begin() as db:
        if case == "hook_revoked":
            db.execute(delete(ProjectBotScope).where(ProjectBotScope.id == 50))
        elif case == "bot_deleted":
            db.execute(update(Bot).where(Bot.id == 40).values(deleted_at=SafeDateTime.now()))
        elif case == "token_rotated":
            db.execute(update(Bot).where(Bot.id == 40).values(app_api_token="rotated-bot-token"))
    if case in {"hook_revoked", "bot_deleted"}:
        with pytest.raises(ValueError):
            await graph.run_default_graph(None, None, thread, thread, resume={"approved": True})
        assert await authority_graph.aget_tuple(config) == before
    else:
        result = await graph.run_default_graph(None, None, thread, thread, resume={"approved": True})
        assert result.message == "Resumed"
        async for retained in authority_graph.alist(config):
            for token in ("original-bot-token", "rotated-bot-token"):
                assert token not in str(retained.checkpoint)
                assert token not in str(retained.pending_writes)


@pytest.mark.asyncio
async def test_completed_resume_recovery_rejects_different_actor(authority_graph, monkeypatch):
    from fastapi import HTTPException
    from langboard_graph.routes import RunApi

    thread = uuid4().hex
    await graph.run_default_graph("run", user_tweaks(), thread, thread)
    original = {"approved": True, "app_api_token": AuthSecurity.create_bot_one_time_token(1)}
    await graph.run_default_graph(None, None, thread, thread, resume=original)
    monkeypatch.setattr(RunApi, "lock_graph_thread", graph.lock_graph_thread)
    recovered = await RunApi.resume_graph(thread, RunApi.GraphResumeRequest(resume=original))
    assert recovered.message == "Resumed"
    before = deepcopy(await authority_graph.aget_tuple({"configurable": {"thread_id": thread}}))
    with pytest.raises(HTTPException) as exc:
        await RunApi.resume_graph(
            thread,
            RunApi.GraphResumeRequest(
                resume={"approved": True, "app_api_token": AuthSecurity.create_bot_one_time_token(2)}
            ),
        )
    assert exc.value.status_code == 400
    assert await authority_graph.aget_tuple({"configurable": {"thread_id": thread}}) == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["card_deleted", "card_moved", "chat_owner_changed", "read_revoked", "membership_revoked"]
)
async def test_resume_rechecks_current_content_authority(authority_graph, history_db, case):  # noqa: F811
    from langboard_shared.core.types import SafeDateTime
    from langboard_shared.domain.models import Card, ChatSession, ProjectColumn
    from sqlalchemy import delete

    tweaks = user_tweaks()
    variables = tweaks["LangboardCalledVariablesComponent"]
    variables["rest_data"] = {}
    if case.startswith("card"):
        for model in (ProjectColumn, Card):
            model.__table__.create(history_db)
        with Session(history_db) as db:
            db.add_all(
                [ProjectColumn(id=60, project_id=30, name="Own"), ProjectColumn(id=61, project_id=31, name="Other")]
            )
            db.commit()
            db.add(Card(id=70, project_id=30, project_column_id=60, title="Private context"))
            db.commit()
        variables["rest_data"]["card_uid"] = SnowflakeID(70).to_short_code()
    elif case == "chat_owner_changed":
        variables["rest_data"]["chat_session_uid"] = SnowflakeID(10).to_short_code()
    else:
        with Session(history_db) as db:
            db.add(ProjectAssignedUser(id=80, project_id=30, user_id=2))
            db.add(ProjectRole(id=81, project_id=30, user_id=2, actions=["read"]))
            db.commit()
        variables["app_api_token"] = AuthSecurity.create_bot_one_time_token(2)
    thread = uuid4().hex
    await graph.run_default_graph("run", tweaks, thread, thread)
    config = {"configurable": {"thread_id": thread}}
    before = deepcopy(await authority_graph.aget_tuple(config))
    with history_db.begin() as db:
        if case == "card_deleted":
            db.execute(update(Card).where(Card.id == 70).values(deleted_at=SafeDateTime.now()))
        elif case == "card_moved":
            db.execute(update(Card).where(Card.id == 70).values(project_id=31, project_column_id=61))
        elif case == "chat_owner_changed":
            db.execute(update(ChatSession).where(ChatSession.id == 10).values(user_id=2))
        elif case == "read_revoked":
            db.execute(update(ProjectRole).where(ProjectRole.id == 81).values(actions=["card_write"]))
        elif case == "membership_revoked":
            db.execute(delete(ProjectAssignedUser).where(ProjectAssignedUser.id == 80))
    with pytest.raises(ValueError):
        await graph.run_default_graph(
            None, None, thread, thread, resume={"approved": True, "app_api_token": variables["app_api_token"]}
        )
    assert await authority_graph.aget_tuple(config) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("runtime_project", [None, 31])
async def test_runner_rejects_project_selector_without_matching_resolved_project(runtime_project):
    from langboard_graph.core.graph.GraphRunner import GraphRunner
    from langboard_graph.core.schema import GraphRequestModel

    request = GraphRequestModel(
        session_id="scope-test",
        run_type="internal_bot",
        uid=SnowflakeID(40).to_short_code(),
        tweaks={
            "LangboardCalledVariablesComponent": {
                "project_uid": SnowflakeID(runtime_project).to_short_code() if runtime_project else None
            }
        },
    )
    project = Project(id=30, owner_id=1, title="Resolved project")
    runner = GraphRunner(request, raw_project=project.model_dump())
    with pytest.raises(ValueError, match="resolved runtime project"):
        await runner.run()


@pytest.mark.asyncio
@pytest.mark.parametrize("history_db", ["postgres"], indirect=True)
@pytest.mark.parametrize("case", ["current", "other_user", "project_revoked"])
async def test_durable_postgres_scope_survives_pool_reconnect(history_db, monkeypatch, case):  # noqa: F811
    """Use production pools/saver, including recovery after a complete disconnect."""
    import os
    from fastapi import HTTPException
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from langboard_graph.core.graph import checkpoint
    from langboard_graph.routes import RunApi

    for model in (Organization, Project, ProjectAssignedUser, ProjectRole):
        model.__table__.create(history_db)
    with Session(history_db) as db:
        db.add(Project(id=30, owner_id=1, title="Checkpoint scope"))
        db.commit()
    url = os.environ["LANGBOARD_FILE_TEST_DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")
    await checkpoint.close_graph_checkpointer()
    monkeypatch.setattr(type(Env), "MAIN_DATABASE_URL", property(lambda self: url))
    monkeypatch.setattr(type(Env), "CARD_INTERNAL_ACCESS_MODE", property(lambda self: "project_members"))
    monkeypatch.setattr(
        history,
        "collect_langboard_history_messages",
        lambda *args: [
            {
                "msg_id": "activity:durable-context",
                "msg_type": "card_action",
                "source": "project_activity",
                "change_type": "new",
                "timestamp": "2026-10-01T00:00:00+00:00",
                "content": "Visible history survives checkpoint reconnect",
            }
        ],
    )
    monkeypatch.setattr(nodes, "create_default_chat_model", lambda *args: object())
    invocations = []
    prompts = []

    async def invoke(*args, **kwargs):
        prompts.append("\n".join(str(message.content) for message in args[1]))
        invocations.append(deepcopy(kwargs["tweaks"]))
        return SimpleNamespace(content="Durably resumed"), []

    async def tools(*args):
        return [], {}

    monkeypatch.setattr(nodes, "_invoke_agent", invoke)
    monkeypatch.setattr(nodes, "create_langboard_api_tool_context", tools)
    thread = "durable-scope-" + uuid4().hex
    config = {"configurable": {"thread_id": thread}}
    tweaks = user_tweaks()
    fixture_secret = "durable-model-secret-not-real"
    tweaks["Graph"]["settings"] = {"api_key": fixture_secret}
    original_token = tweaks["LangboardCalledVariablesComponent"]["app_api_token"]
    decision = {"approved": True, "app_api_token": AuthSecurity.create_bot_one_time_token(1)}
    try:
        interrupted = await graph.run_default_graph("run", tweaks, thread, thread)
        assert interrupted.interrupts
        assert invocations == []
        async with checkpoint.open_graph_checkpointer() as saver:
            assert isinstance(saver, AsyncPostgresSaver)
            before = await saver.aget_tuple(config)
            assert before is not None
            scope = before.checkpoint["channel_values"]["execution_scope"]
            assert scope == {"user_id": 1, "project_uid": SnowflakeID(30).to_short_code(), "chat_session_id": None}
            retained_before = [entry async for entry in saver.alist(config)]
        old_pool = checkpoint._checkpoint_pool
        await checkpoint.close_graph_checkpointer()
        assert old_pool is not None and old_pool.closed
        assert checkpoint._checkpoint_pool is None
        async with checkpoint.open_graph_checkpointer() as saver:
            assert isinstance(saver, AsyncPostgresSaver)
            assert checkpoint._checkpoint_pool is not old_pool
            assert await saver.aget_tuple(config) == before
        if case == "other_user":
            decision["app_api_token"] = AuthSecurity.create_bot_one_time_token(2)
        elif case == "project_revoked":
            with history_db.begin() as db:
                db.execute(update(Project).where(Project.id == 30).values(owner_id=2))
        if case == "current":
            resumed = await RunApi.resume_graph(thread, RunApi.GraphResumeRequest(resume=decision))
            assert resumed.message == "Durably resumed"
            assert len(prompts) == 1
            assert "Visible history survives checkpoint reconnect" in prompts[0]
            assert invocations[0]["LangboardCalledVariablesComponent"]["app_api_token"] == decision["app_api_token"]
            # Completed-result recovery must enforce the same durable scope.
            await checkpoint.close_graph_checkpointer()
            recovered = await RunApi.resume_graph(thread, RunApi.GraphResumeRequest(resume=decision))
            assert recovered.message == "Durably resumed"
            assert len(invocations) == 1
            async with checkpoint.open_graph_checkpointer() as saver:
                completed = await saver.aget_tuple(config)
            await checkpoint.close_graph_checkpointer()
            foreign_decision = {"approved": True, "app_api_token": AuthSecurity.create_bot_one_time_token(2)}
            with pytest.raises(HTTPException) as exc:
                await RunApi.resume_graph(thread, RunApi.GraphResumeRequest(resume=foreign_decision))
            assert exc.value.status_code == 400
            with history_db.begin() as db:
                db.execute(update(Project).where(Project.id == 30).values(owner_id=2))
            with pytest.raises(HTTPException) as exc:
                await RunApi.resume_graph(thread, RunApi.GraphResumeRequest(resume=decision))
            assert exc.value.status_code == 400
            assert len(invocations) == 1
            async with checkpoint.open_graph_checkpointer() as saver:
                assert await saver.aget_tuple(config) == completed
        else:
            with pytest.raises(HTTPException) as exc:
                await RunApi.resume_graph(thread, RunApi.GraphResumeRequest(resume=decision))
            assert exc.value.status_code == 400
            assert invocations == []
            async with checkpoint.open_graph_checkpointer() as saver:
                assert await saver.aget_tuple(config) == before
                assert [entry async for entry in saver.alist(config)] == retained_before
        async with checkpoint.open_graph_checkpointer() as saver:
            async for retained in saver.alist(config):
                for secret in (original_token, decision["app_api_token"], fixture_secret):
                    assert secret not in str(retained.checkpoint)
                    assert secret not in str(retained.pending_writes)
    finally:
        async with checkpoint.open_graph_checkpointer() as saver:
            await saver.adelete_thread(thread)
        await checkpoint.close_graph_checkpointer()


@pytest.mark.asyncio
async def test_resume_rebuilds_current_history_before_first_model_invocation(authority_graph, monkeypatch):
    """The history watermark can advance before an approval interrupt runs the model."""
    # The authority fixture replaces prompt collection; use the actual history boundary here.
    monkeypatch.setattr(history, "create_langboard_history_context_prompt", create_langboard_history_context_prompt)
    monkeypatch.setattr(nodes, "create_langboard_history_context_prompt", create_langboard_history_context_prompt)
    visible = [
        {
            "msg_id": "activity:initial",
            "msg_type": "card_action",
            "source": "project_activity",
            "change_type": "new",
            "timestamp": "2026-10-01T00:00:00+00:00",
            "content": {"title": "Still visible history", "api_key": "history-secret-not-real"},
        },
        {
            "msg_id": "activity:revoked",
            "msg_type": "card_action",
            "source": "project_activity",
            "change_type": "new",
            "timestamp": "2026-10-02T00:00:00+00:00",
            "content": "History later hidden by current ACLs",
        },
    ]
    monkeypatch.setattr(history, "collect_langboard_history_messages", lambda *args: deepcopy(visible))
    prompts = []

    async def invoke(model, messages, tools, **kwargs):
        prompts.append("\n".join(str(message.content) for message in messages))
        return SimpleNamespace(content="Resumed with current history"), []

    monkeypatch.setattr(nodes, "_invoke_agent", invoke)
    thread = uuid4().hex
    config = {"configurable": {"thread_id": thread}}
    interrupted = await graph.run_default_graph("run", user_tweaks(), thread, thread)
    assert interrupted.interrupts
    assert prompts == []
    saved = await authority_graph.aget_tuple(config)
    assert saved.checkpoint["channel_values"]["last_history_sync_at"] == "2026-10-02T00:00:00+00:00"
    visible.pop()
    result = await graph.run_default_graph(
        None,
        None,
        thread,
        thread,
        resume={"approved": True, "app_api_token": AuthSecurity.create_bot_one_time_token(1)},
    )
    assert result.message == "Resumed with current history"
    assert len(prompts) == 1
    assert "Still visible history" in prompts[0]
    assert "History later hidden" not in prompts[0]
    assert "history-secret-not-real" not in prompts[0]
    async for retained in authority_graph.alist(config):
        assert "history-secret-not-real" not in str(retained.checkpoint)
        assert "history-secret-not-real" not in str(retained.pending_writes)
