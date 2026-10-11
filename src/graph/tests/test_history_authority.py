"""Chat history must match the signed execution actor's current session ownership."""

import os
from datetime import timedelta
from uuid import uuid4
import pytest
from jwt import encode
from langboard_shared.core.db import ChatContentModel
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import (
    Bot,
    Card,
    CardComment,
    ChatHistory,
    ChatSession,
    Organization,
    Project,
    ProjectActivity,
    ProjectAssignedUser,
    ProjectColumn,
    User,
)
from langboard_shared.Env import Env
from sqlalchemy import create_engine, text, update
from sqlalchemy.orm import Session
from langboard_graph.core.graph.default.history import collect_chat_history_messages


@pytest.fixture(params=["sqlite", "postgres"])
def history_db(monkeypatch, request):
    url = "sqlite://" if request.param == "sqlite" else os.environ.get("LANGBOARD_FILE_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requires disposable PostgreSQL")
    monkeypatch.setattr(
        type(Env), "JWT_SECRET_KEY", property(lambda self: "test-only-signing-key-for-graph-history-32bytes")
    )
    engine = create_engine(url)
    schema = None
    if request.param == "postgres":
        schema = "history_authority_" + uuid4().hex
        with engine.begin() as db:
            db.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine.dispose()
        engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    models = (User, ChatSession, ChatHistory)
    if request.node.originalname in {
        "test_activity_scope_always_requires_matching_board",
        "test_comment_history_reuses_current_card_visibility",
    }:
        models += (Organization, Project, ProjectColumn, Bot, Card, ProjectActivity, ProjectAssignedUser, CardComment)
    for model in models:
        model.__table__.create(engine)
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)

    def stale_replica_forbidden():
        raise AssertionError("Chat ownership checks must use current primary rows")

    monkeypatch.setattr(DbEngine, "get_readonly_engine", stale_replica_forbidden)
    with Session(engine) as db:
        db.add_all(
            [
                User(
                    id=i,
                    firstname="Fixture",
                    lastname=str(i),
                    email=f"{i}@example.invalid",
                    password="test-only",
                    activated_at=SafeDateTime.now(),
                )
                for i in (1, 2)
            ]
        )
        db.commit()
        db.add_all([ChatSession(id=10, user_id=1), ChatSession(id=11, user_id=2)])
        db.commit()
        db.add_all(
            [
                ChatHistory(id=20, chat_session_id=10, message=ChatContentModel(content="Owned history")),
                ChatHistory(id=21, chat_session_id=11, message=ChatContentModel(content="Foreign history")),
            ]
        )
        db.commit()
    try:
        yield engine
    finally:
        if schema:
            with engine.begin() as db:
                db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()


@pytest.mark.parametrize(
    "case",
    [
        "owned",
        "foreign",
        "missing",
        "invalid",
        "expired",
        "wrong_internal",
        "bot_actor",
        "deactivated",
        "deleted",
        "owner_changed",
    ],
)
def test_chat_history_checks_signed_actor_and_session_owner(history_db, case):
    from langboard_shared.core.types import SnowflakeID

    payload = {
        "sub": "1",
        "iss": Env.PROJECT_NAME,
        "internal": "bot",
        "exp": int((SafeDateTime.now() + timedelta(minutes=5)).timestamp()),
    }
    if case == "expired":
        payload["exp"] = int((SafeDateTime.now() - timedelta(minutes=1)).timestamp())
    if case == "wrong_internal":
        payload["internal"] = "socket_dispatch"
    token = encode(payload, Env.JWT_SECRET_KEY, algorithm=Env.JWT_ALGORITHM)
    if case == "missing":
        token = ""
    if case == "invalid":
        token = "invalid"
    variables = {"current_runner_type": "bot" if case == "bot_actor" else "user", "app_api_token": token}
    # Client runner metadata must never replace signed token identity.
    variables["current_runner_data"] = {"uid": SnowflakeID(2).to_short_code()}
    if case in {"deactivated", "deleted", "owner_changed"}:
        with Session(history_db) as db:
            if case == "owner_changed":
                db.execute(update(ChatSession).where(ChatSession.id == 10).values(user_id=2))
            else:
                field = {"activated_at": None} if case == "deactivated" else {"deleted_at": SafeDateTime.now()}
                db.execute(update(User).where(User.id == 1).values(**field))
            db.commit()
    session = 11 if case == "foreign" else 10
    result = collect_chat_history_messages(variables, {"chat_session_uid": SnowflakeID(session).to_short_code()}, 20)
    assert [item["content"] for item in result] == (["Owned history"] if case == "owned" else [])


@pytest.mark.parametrize("case", ["anchor_only", "foreign_anchor", "missing_anchor", "mismatched_session"])
def test_chat_history_anchor_limits_collection_to_one_session(history_db, case):
    from langboard_shared.core.types import SnowflakeID

    with Session(history_db) as db:
        db.add(ChatSession(id=12, user_id=1))
        db.commit()
        db.add_all(
            [
                ChatHistory(id=22, chat_session_id=10, message=ChatContentModel(content="Current message")),
                ChatHistory(id=23, chat_session_id=12, message=ChatContentModel(content="Other owned session")),
            ]
        )
        db.commit()
    token = encode(
        {
            "sub": "1",
            "iss": Env.PROJECT_NAME,
            "internal": "bot",
            "exp": int((SafeDateTime.now() + timedelta(minutes=5)).timestamp()),
        },
        Env.JWT_SECRET_KEY,
        algorithm=Env.JWT_ALGORITHM,
    )
    variables = {"current_runner_type": "user", "app_api_token": token}
    anchor = 21 if case == "foreign_anchor" else 99 if case == "missing_anchor" else 22
    rest = {"chat_history_uid": SnowflakeID(anchor).to_short_code()}
    if case == "mismatched_session":
        rest["chat_session_uid"] = SnowflakeID(12).to_short_code()
    result = collect_chat_history_messages(variables, rest, 20)
    assert [item["content"] for item in result] == (["Owned history"] if case == "anchor_only" else [])


@pytest.mark.parametrize(
    "scope", ["board", "own_column", "foreign_column", "own_card", "foreign_card", "missing_board"]
)
def test_activity_scope_always_requires_matching_board(history_db, scope, monkeypatch):
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: history_db)
    from langboard_shared.core.types import SnowflakeID
    from langboard_shared.domain.models.ProjectActivity import ProjectActivityType
    from langboard_graph.core.graph.default.history import collect_project_activity_messages

    with Session(history_db) as db:
        db.add_all([Project(id=30, owner_id=1, title="Own board"), Project(id=31, owner_id=1, title="Other board")])
        db.commit()
        db.add_all(
            [
                ProjectColumn(id=40, project_id=30, name="Own column"),
                ProjectColumn(id=41, project_id=31, name="Other column"),
            ]
        )
        db.commit()
        db.add_all(
            [
                Card(id=50, project_id=30, project_column_id=40, title="Own card"),
                Card(id=51, project_id=31, project_column_id=41, title="Other card"),
            ]
        )
        db.commit()
        db.add_all(
            [
                ProjectActivity(
                    id=60,
                    project_id=30,
                    project_column_id=40,
                    card_id=50,
                    activity_type=ProjectActivityType.CardCreated,
                    activity_history={"title": "Own event"},
                ),
                ProjectActivity(
                    id=61,
                    project_id=31,
                    project_column_id=41,
                    card_id=51,
                    activity_type=ProjectActivityType.CardCreated,
                    activity_history={"title": "Other event"},
                ),
            ]
        )
        db.commit()
    variables = {} if scope == "missing_board" else {"project_uid": SnowflakeID(30).to_short_code()}
    rest = {}
    if scope in {"own_column", "foreign_column", "missing_board"}:
        rest["project_column_uid"] = SnowflakeID(40 if scope == "own_column" else 41).to_short_code()
    if scope in {"own_card", "foreign_card"}:
        rest["card_uid"] = SnowflakeID(50 if scope == "own_card" else 51).to_short_code()
    result = collect_project_activity_messages(variables, rest, 20)
    assert [item["content"]["history"]["title"] for item in result] == (
        ["Own event"] if scope in {"board", "own_column", "own_card"} else []
    )


@pytest.mark.parametrize(
    "case", ["shared", "internal", "private_owner", "private_other", "wrong_board", "nonmember", "deleted_user", "bot"]
)
def test_comment_history_reuses_current_card_visibility(history_db, case, monkeypatch):
    from langboard_shared.core.db import EditorContentModel
    from langboard_shared.core.types import SnowflakeID
    from langboard_graph.core.graph.default.history import collect_card_comment_messages

    monkeypatch.setattr(type(Env), "CARD_INTERNAL_ACCESS_MODE", property(lambda self: "project_members"))
    visibility = "PRIVATE" if case.startswith("private") else "INTERNAL" if case == "internal" else "SHARED"
    creator = 2 if case == "private_other" else 1
    with Session(history_db) as db:
        db.add_all([Project(id=30, owner_id=1, title="Board"), Project(id=31, owner_id=1, title="Wrong board")])
        db.commit()
        db.add(ProjectColumn(id=40, project_id=30, name="Column"))
        db.commit()
        db.add(
            Card(
                id=50,
                project_id=30,
                project_column_id=40,
                title="Card",
                visibility=visibility,
                owner_user_id=creator if visibility == "PRIVATE" else None,
                created_by_user_id=creator,
            )
        )
        db.commit()
        db.add(CardComment(id=60, card_id=50, user_id=1, content=EditorContentModel(content="Scoped comment")))
        if case == "deleted_user":
            db.execute(update(User).where(User.id == 1).values(deleted_at=SafeDateTime.now()))
        db.commit()
    actor = 2 if case == "nonmember" else 1
    token = encode(
        {
            "sub": str(actor),
            "iss": Env.PROJECT_NAME,
            "internal": "bot",
            "exp": int((SafeDateTime.now() + timedelta(minutes=5)).timestamp()),
        },
        Env.JWT_SECRET_KEY,
        algorithm=Env.JWT_ALGORITHM,
    )
    variables = {
        "current_runner_type": "bot" if case == "bot" else "user",
        "app_api_token": token,
        "project_uid": SnowflakeID(31 if case == "wrong_board" else 30).to_short_code(),
    }
    result = collect_card_comment_messages({"card_uid": SnowflakeID(50).to_short_code()}, 20, variables)
    assert [item["content"] for item in result] == (
        ["Scoped comment"] if case in {"shared", "internal"} else []
    )
