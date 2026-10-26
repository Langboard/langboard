"""Real PostgreSQL arbitration for repositories that insert IDs directly."""

import importlib
import os
from uuid import uuid4
import pytest
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.types import SnowflakeID
from langboard_shared.domain.models import ChatHistory, ChatSession, InternalBotRun, OllamaModelPull, ProjectChatSession
from langboard_shared.domain.models.InternalBotRun import InternalBotRunKind
from langboard_shared.infrastructure.repositories.factory.InternalBotRunRepository import InternalBotRunRepository
from langboard_shared.infrastructure.repositories.factory.OllamaModelPullRepository import OllamaModelPullRepository
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateTable


@pytest.fixture
def postgres(monkeypatch):
    url = os.getenv("LANGBOARD_FILE_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Dedicated PostgreSQL proof URL not set")
    admin = create_engine(url)
    schema = f"direct_snowflake_{uuid4().hex}"
    with admin.begin() as connection:
        connection.execute(text(f"CREATE SCHEMA {schema}"))
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        with engine.begin() as connection:
            for model in (InternalBotRun, OllamaModelPull, ChatSession, ProjectChatSession, ChatHistory):
                connection.execute(CreateTable(model.__table__, include_foreign_key_constraints=[]))
        monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
        monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
        monkeypatch.setattr(SnowflakeID, "_last_timestamp", -1)
        monkeypatch.setattr(SnowflakeID, "_sequence", 0)
        monkeypatch.setattr(SnowflakeID, "_machine_id", 950)
        monkeypatch.setattr(SnowflakeID, "_current_millis", classmethod(lambda cls: cls.EPOCH + 100000))
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        admin.dispose()


def make_run(key, digest="digest"):
    return InternalBotRun(
        request_key=key,
        request_digest=digest,
        client_task_id=key,
        user_id=SnowflakeID(1),
        project_id=SnowflakeID(2),
        internal_bot_id=SnowflakeID(3),
        kind=InternalBotRunKind.BoardChat,
        request_payload={"message": "hello"},
    )


def repository(kind):
    cls = OllamaModelPullRepository if kind == "pull" else InternalBotRunRepository
    return cls(lambda _: None, lambda _: None)


def accept(repo, kind, key, digest="digest"):
    return repo.accept(key) if kind == "pull" else repo.accept(make_run(key, digest))


@pytest.mark.parametrize("kind", ["pull", "run", "board_chat"])
def test_two_consecutive_worker_ids_do_not_exhaust_acceptance(postgres, kind):
    repo = repository(kind)
    first, _ = accept(repo, kind, "old-1")
    second, _ = accept(repo, kind, "old-2")
    assert second.id == first.id + 1
    model = OllamaModelPull if kind == "pull" else InternalBotRun
    with postgres.connect() as connection:
        original_rows = connection.execute(select(model.__table__).order_by(model.column("id"))).mappings().all()
    SnowflakeID._last_timestamp = -1
    SnowflakeID._sequence = 0
    if kind == "board_chat":
        saved, inserted, *_ = repo.accept_board_chat(make_run("new"), "hello", "all", None)
    else:
        saved, inserted = accept(repo, kind, "new")
    assert inserted and saved.id not in {first.id, second.id}
    assert int(saved.id) >> 22 == 100001
    if kind == "board_chat":
        duplicate, inserted, *_ = repo.accept_board_chat(make_run("new"), "hello", "all", None)
    else:
        duplicate, inserted = accept(repo, kind, "new")
    assert not inserted and duplicate.id == saved.id
    if kind != "pull":
        with pytest.raises(ValueError, match="different AI request"):
            if kind == "board_chat":
                repo.accept_board_chat(make_run("new", "different digest"), "hello", "all", None)
            else:
                accept(repo, kind, "new", "different digest")
    model = OllamaModelPull if kind == "pull" else InternalBotRun
    with postgres.connect() as connection:
        rows = connection.execute(select(model.__table__).order_by(model.column("id"))).mappings().all()
    assert len(rows) == 3
    assert rows[:2] == original_rows
    key_column = "model_name" if kind == "pull" else "request_key"
    assert [(row["id"], row[key_column]) for row in rows[:2]] == [(first.id, "old-1"), (second.id, "old-2")]


@pytest.mark.parametrize("kind", ["pull", "run", "board_chat"])
@pytest.mark.parametrize("constraint", ["check", "unique", "not_null", "foreign_key"])
def test_non_id_constraints_propagate(postgres, monkeypatch, kind, constraint):
    repo = repository(kind)
    model = OllamaModelPull if kind == "pull" else InternalBotRun
    table = model.__tablename__
    column = "attempt"
    with postgres.begin() as connection:
        if constraint == "check":
            connection.execute(text(f"ALTER TABLE {table} ADD CONSTRAINT fixture_check CHECK ({column} < 0)"))
        elif constraint == "unique":
            accept(repo, kind, "old")
            connection.execute(text(f"CREATE UNIQUE INDEX fixture_unique ON {table} ({column})"))
        elif constraint == "not_null":
            nullable_column = "failure_reason" if kind == "pull" else "error_message"
            connection.execute(text(f"ALTER TABLE {table} ALTER COLUMN {nullable_column} SET NOT NULL"))
        else:
            connection.execute(text("CREATE TABLE fixture_parent (id INTEGER PRIMARY KEY)"))
            connection.execute(
                text(
                    f"ALTER TABLE {table} ADD CONSTRAINT fixture_fk FOREIGN KEY ({column}) REFERENCES fixture_parent(id)"
                )
            )
    calls = []
    monkeypatch.setattr(SnowflakeID, "advance_after_collision", classmethod(lambda cls, value: calls.append(value)))
    with pytest.raises(IntegrityError):
        if kind == "board_chat":
            repo.accept_board_chat(make_run("new"), "hello", "all", None)
        else:
            accept(repo, kind, "new")
    assert calls == []


@pytest.mark.parametrize("kind", ["pull", "run", "board_chat"])
def test_collision_retry_budget_is_bounded(postgres, monkeypatch, kind):
    repo = repository(kind)
    first, _ = accept(repo, kind, "old")
    allocator_module = importlib.import_module(type(repo).__module__)
    db_module = importlib.import_module("langboard_shared.core.db.DbSession")
    # Suppress the expected identical traceback repeated for each exhausted attempt.
    monkeypatch.setattr(db_module._logger, "exception", lambda *args, **kwargs: None)
    calls = []
    monkeypatch.setattr(allocator_module, "SnowflakeID", lambda: first.id)
    monkeypatch.setattr(SnowflakeID, "advance_after_collision", classmethod(lambda cls, value: calls.append(value)))
    with pytest.raises(RuntimeError, match="Could not accept a concurrent"):
        if kind == "board_chat":
            repo.accept_board_chat(make_run("new"), "hello", "all", None)
        else:
            accept(repo, kind, "new")
    assert calls == [first.id] * 32
    model = OllamaModelPull if kind == "pull" else InternalBotRun
    with postgres.connect() as connection:
        assert connection.execute(select(model.column("id"))).scalars().all() == [first.id]


@pytest.fixture
def sqlite_engine(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'acceptance.sqlite'}")
    with engine.begin() as connection:
        for model in (InternalBotRun, OllamaModelPull, ChatSession, ProjectChatSession, ChatHistory):
            connection.execute(CreateTable(model.__table__, include_foreign_key_constraints=[]))
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.mark.parametrize("kind", ["pull", "run", "board_chat"])
def test_sqlite_same_business_key_insert_race_remains_idempotent(sqlite_engine, monkeypatch, kind):
    from langboard_shared.core.db import DbSession

    repo = repository(kind)
    model = OllamaModelPull if kind == "pull" else InternalBotRun
    original_insert = DbSession.insert
    winner = []

    def insert_competing_request(db, obj):
        if isinstance(obj, model) and not winner:
            winner.append(None)
            # Commit another acceptance after this transaction's empty SELECT,
            # before its INSERT, deterministically exercising the UNIQUE race.
            winner[0], inserted = accept(repo, kind, "same-key")
            assert inserted
        return original_insert(db, obj)

    monkeypatch.setattr(DbSession, "insert", insert_competing_request)
    if kind == "board_chat":
        saved, inserted, *_ = repo.accept_board_chat(make_run("same-key"), "hello", "all", None)
        assert inserted  # The winner has no chat records; this call creates them.
    else:
        saved, inserted = accept(repo, kind, "same-key")
        assert not inserted
    assert saved.id == winner[0].id
    with sqlite_engine.connect() as connection:
        assert connection.execute(select(model.column("id"))).scalars().all() == [saved.id]
    if kind != "pull":
        with pytest.raises(ValueError, match="different AI request"):
            accept(repo, kind, "same-key", "different digest")


@pytest.mark.parametrize("kind", ["pull", "run", "board_chat"])
def test_sqlite_unrelated_unique_constraint_propagates(sqlite_engine, kind):
    repo = repository(kind)
    model = OllamaModelPull if kind == "pull" else InternalBotRun
    accept(repo, kind, "old")
    with sqlite_engine.begin() as connection:
        connection.execute(text(f"CREATE UNIQUE INDEX fixture_unique ON {model.__tablename__} (attempt)"))
    with pytest.raises(IntegrityError):
        if kind == "board_chat":
            repo.accept_board_chat(make_run("new"), "hello", "all", None)
        else:
            accept(repo, kind, "new")
