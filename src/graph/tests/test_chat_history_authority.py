"""Chat history must match the signed execution actor's current session ownership."""

import os
from datetime import timedelta
from uuid import uuid4
import pytest
from jwt import encode
from langboard_shared.core.db import ChatContentModel
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.types import SafeDateTime, SnowflakeID
from langboard_shared.domain.models import ChatHistory, ChatSession, User
from langboard_shared.Env import Env
from sqlalchemy import create_engine, text, update
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from langboard_graph.core.graph.default.history import collect_chat_history_messages


@pytest.fixture(params=["sqlite", "postgres"])
def history_db(monkeypatch, request):
    url = "sqlite://" if request.param == "sqlite" else os.environ.get("LANGBOARD_FILE_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requires disposable PostgreSQL")
    monkeypatch.setattr(
        type(Env), "JWT_SECRET_KEY", property(lambda self: "test-only-signing-key-for-graph-history-32bytes")
    )
    engine = (
        create_engine(url, connect_args={"check_same_thread": False}, poolclass=StaticPool)
        if request.param == "sqlite"
        else create_engine(url)
    )
    schema = None
    if request.param == "postgres":
        schema = "history_authority_" + uuid4().hex
        with engine.begin() as db:
            db.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine.dispose()
        engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    models = (User, ChatSession, ChatHistory)
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
        "zero_subject",
        "oversized_subject",
        "nonnumeric_subject",
    ],
)
def test_chat_history_checks_signed_actor_and_session_owner(history_db, case):
    payload = {
        "sub": "1",
        "iss": Env.PROJECT_NAME,
        "internal": "bot",
        "exp": int((SafeDateTime.now() + timedelta(minutes=5)).timestamp()),
    }
    if case in {"zero_subject", "oversized_subject", "nonnumeric_subject"}:
        payload["sub"] = {
            "zero_subject": "0",
            "oversized_subject": str(SnowflakeID.MAX_VALUE + 1),
            "nonnumeric_subject": "not-an-id",
        }[case]
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
