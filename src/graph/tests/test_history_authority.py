"""Chat history must match the signed execution actor's current session ownership."""

import os
from datetime import timedelta
from uuid import uuid4
import pytest
from jwt import encode
from langboard_shared.core.db import ChatContentModel
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import ChatHistory, ChatSession, User
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
    for model in (User, ChatSession, ChatHistory):
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
