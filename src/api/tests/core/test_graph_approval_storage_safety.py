"""Native Graph approval writes arbitrate ID collisions without losing referential identity."""

import asyncio
import importlib
import pytest
from langboard_graph.core.graph.GraphRunner import GraphRunner
from langboard_graph.core.schema import GraphInterrupt
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.types import SnowflakeID
from langboard_shared.domain.models import (
    Bot,
    BotLog,
    BotTriggerGraphApprovalRequest,
    GraphApprovalRequest,
    Project,
    User,
)
from langboard_shared.domain.models.BaseBotModel import BotPlatform, BotPlatformRunningType
from langboard_shared.domain.models.bases import BaseGraphApprovalRequestModel
from langboard_shared.helpers import ModelHelper
from langboard_shared.publishers import GraphApprovalPublisher
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql.ddl import sort_tables


@pytest.fixture
def native_approvals(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'graph-approvals.sqlite'}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    models = [
        User,
        Project,
        Bot,
        BotLog,
        GraphApprovalRequest,
        *ModelHelper.get_models_by_base_class(BaseGraphApprovalRequestModel),
    ]
    tables = set()
    pending = [model.__table__ for model in models]
    while pending:
        table = pending.pop()
        if table in tables:
            continue
        tables.add(table)
        pending.extend(key.column.table for key in table.foreign_keys)
    for table in sort_tables(tables):
        table.create(engine)
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    publications = []
    monkeypatch.setattr(GraphApprovalPublisher, "requested", lambda project, response: publications.append(response))
    with DbSession.use(readonly=False) as db:
        actor = User(
            firstname="Graph",
            lastname="Fixture",
            email="graph@example.invalid",
            username="graph-fixture",
            password="test-only-hash",
        )
        db.insert(actor)
        project = Project(owner_id=actor.id, title="Graph approval collision fixture")
        bot = Bot(
            name="Fixture",
            bot_uname="fixture",
            app_api_token="test-only",
            platform=BotPlatform.Default,
            platform_running_type=BotPlatformRunningType.Default,
        )
        db.insert(project)
        db.insert(bot)
        log = BotLog(bot_id=bot.id)
        db.insert(log)
    monkeypatch.setattr(SnowflakeID, "_machine_id", 950)
    monkeypatch.setattr(SnowflakeID, "_current_millis", classmethod(lambda cls: cls.EPOCH + 100000))
    monkeypatch.setattr(SnowflakeID, "_last_timestamp", -1)
    monkeypatch.setattr(SnowflakeID, "_sequence", 0)
    runner = GraphRunner(
        None, raw_project=project.model_dump(), raw_bot=("bot", bot.model_dump()), raw_bot_log=(log.model_dump(), None)
    )

    def persist(name):
        return asyncio.run(
            runner._persist_approval_requests(
                [
                    GraphInterrupt(
                        id=name,
                        value={
                            "type": "approval_request",
                            "origin_type": "trigger",
                            "scope_table": "project",
                            "scope_uid": project.get_uid(),
                            "thread_id": f"thread-{name}",
                            "request_payload": {"operation": name},
                            "preview": {"label": name},
                        },
                    )
                ]
            )
        )

    def reread():
        # A new connection observes committed data, outside the service's write session.
        with engine.connect() as connection:
            requests = (
                connection.execute(select(GraphApprovalRequest.__table__).order_by(GraphApprovalRequest.id))
                .mappings()
                .all()
            )
            details = (
                connection.execute(
                    select(BotTriggerGraphApprovalRequest.__table__).order_by(BotTriggerGraphApprovalRequest.id)
                )
                .mappings()
                .all()
            )
            assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
        return requests, details

    try:
        yield engine, persist, reread, publications, project, bot, log
    finally:
        engine.dispose()


@pytest.mark.parametrize("boundary", ["request", "detail"])
def test_native_graph_recovers_primary_key_collision_and_rereads_identity(native_approvals, boundary):
    engine, persist, reread, publications, project, bot, log = native_approvals
    assert persist("prior") == 1
    prior_requests, prior_details = reread()
    attempts = []
    target_table = (
        GraphApprovalRequest.__tablename__ if boundary == "request" else BotTriggerGraphApprovalRequest.__tablename__
    )

    @event.listens_for(engine, "before_cursor_execute")
    def observe_identity(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith(f"INSERT INTO {target_table} "):
            attempts.append(parameters[0])

    if boundary == "request":
        SnowflakeID._last_timestamp = -1
        SnowflakeID._sequence = 0
    else:

        @event.listens_for(engine, "after_cursor_execute")
        def repeat_detail_identity(connection, cursor, statement, parameters, context, executemany):
            if statement.startswith("INSERT INTO graph_approval_request "):
                SnowflakeID._last_timestamp = 100000 + SnowflakeID.EPOCH
                SnowflakeID._sequence = 0

    assert persist("new") == 1
    requests, details = reread()
    assert len(requests) == len(details) == len(publications) == 2
    assert len({row["id"] for row in requests}) == len({row["id"] for row in details}) == 2
    assert prior_requests[0] in requests and prior_details[0] in details
    new_request = next(row for row in requests if row["run_id"] == "new")
    new_detail = next(row for row in details if row["approval_request_id"] == new_request["id"])
    assert new_detail["approval_request_id"] != prior_requests[0]["id"]
    assert new_detail["scope_id"] == project.id
    assert new_detail["bot_id"] == bot.id and new_detail["bot_log_id"] == log.id
    assert new_request["request_payload"] == {"operation": "new"}
    assert publications[-1]["uid"] == SnowflakeID(new_request["id"]).to_short_code()
    occupied = prior_requests[0]["id"] if boundary == "request" else prior_details[0]["id"]
    assert len(attempts) == 2 and attempts[0] == occupied and attempts[1] != occupied


def test_native_graph_detail_allocation_exhaustion_rolls_back_new_request(native_approvals, monkeypatch):
    _, persist, reread, publications, *_ = native_approvals
    assert persist("prior") == 1
    before = reread()
    occupied_detail_id = before[1][0]["id"]
    db_module = importlib.import_module("langboard_shared.core.db.DbSession")
    attempts = []

    def occupied_identity(*args):
        if args:
            return SnowflakeID(args[0])
        attempts.append(occupied_detail_id)
        return SnowflakeID(occupied_detail_id)

    monkeypatch.setattr(db_module, "SnowflakeID", occupied_identity)
    with pytest.raises(RuntimeError, match="allocation exhausted"):
        persist("failure")
    assert len(attempts) == 33  # One new request plus all 32 rejected detail allocations.
    assert reread() == before
    assert len(publications) == 1


def test_native_graph_non_id_unique_failure_rolls_back_new_request(native_approvals):
    engine, persist, reread, publications, *_ = native_approvals
    assert persist("prior") == 1
    before = reread()
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE UNIQUE INDEX fixture_approval_scope_unique ON bot_trigger_graph_approval_request (scope_table, scope_id)"
            )
        )
    with pytest.raises(IntegrityError, match="scope_table"):
        persist("failure")
    assert reread() == before
    assert len(publications) == 1
