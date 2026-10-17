"""Checkitem projections batch real timer SQL and retain latest-record semantics."""

from datetime import UTC, datetime
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from langboard_shared.domain.models import Card, Checkitem, CheckitemTimerRecord, Checklist, User
from langboard_shared.domain.models.Checkitem import CheckitemStatus
from langboard_shared.domain.services.CardVisibilityPolicy import CardVisibilityContext, CollaborationChannel
from langboard_shared.domain.services.factory.CheckitemService import CheckitemService
from langboard_shared.infrastructure.repositories.factory.CheckitemRepository import CheckitemRepository
from langboard_shared.infrastructure.repositories.factory.CheckitemTimerRecordRepository import (
    CheckitemTimerRecordRepository,
)
from sqlalchemy import event
from sqlalchemy.orm import Session


def test_active_workers_share_timestamp_ordering_and_keep_two_queries(timer_engine):
    early = datetime(2026, 10, 5, tzinfo=UTC)
    late = datetime(2026, 10, 6, tzinfo=UTC)
    with Session(timer_engine) as db:
        db.add(Card(id=2000, project_id=10, project_column_id=40, title="Timer workers"))
        db.commit()
        db.add(Checklist(id=3000, card_id=2000, title="Work"))
        db.commit()
        db.add_all(
            Checkitem(id=i, checklist_id=3000, title=f"Work {i}", user_id=2, status=status)
            for i, status in [(1, CheckitemStatus.Started), (2, CheckitemStatus.Paused), (3, CheckitemStatus.Started)]
        )
        db.commit()
        db.add_all(
            [
                CheckitemTimerRecord(id=100, checkitem_id=1, status=CheckitemStatus.Started, created_at=late),
                CheckitemTimerRecord(id=101, checkitem_id=1, status=CheckitemStatus.Started, created_at=early),
                CheckitemTimerRecord(id=200, checkitem_id=2, status=CheckitemStatus.Paused, created_at=late),
            ]
        )
        db.commit()
    statements = []

    def capture_query(conn, cursor, statement, params, context, many):
        statements.append(statement)

    def make_repo(repository):
        return repository(make_repo, lambda _: None)

    workers = make_repo(CheckitemRepository)
    event.listen(timer_engine, "before_cursor_execute", capture_query)
    try:
        result = workers.get_active_workers_by_project(10)
        assert len(statements) == 2
        anchors = {int(item.id): anchor for item, _, anchor in result}
        assert set(anchors) == {1, 2, 3}
        assert anchors[1].replace(tzinfo=UTC) == late
        assert anchors[2] is None
        assert anchors[3] is None
        statements.clear()
        assert workers.get_active_workers_by_project(10, 9999) == []
        assert len(statements) == 1
    finally:
        event.remove(timer_engine, "before_cursor_execute", capture_query)


@pytest.mark.parametrize("view", ["checklist", "card", "active"])
def test_thousand_item_views_use_one_timer_query_and_preserve_ties(timer_engine, monkeypatch, view):
    engine = timer_engine
    card = SimpleNamespace(id=2000, get_uid=lambda: "card", api_response=lambda: {"uid": "card"})
    project = SimpleNamespace(api_response=lambda: {"uid": "project"})
    checklist = SimpleNamespace(id=3000)
    items = [SimpleNamespace(id=i, checklist_id=3000, api_response=lambda i=i: {"uid": str(i)}) for i in range(1, 1001)]
    with Session(engine) as db:
        db.add(Card(id=2000, project_id=10, project_column_id=40, title="Timer batch"))
        db.commit()
        db.add(Checklist(id=3000, card_id=2000, title="Work"))
        db.commit()
        db.add_all([Checkitem(id=i, checklist_id=3000, title=f"Work {i}") for i in range(1, 1001)])
        db.commit()
    records = [(item, None, None) for item in items]
    active = [(item, card, project) for item in items]
    timers = CheckitemTimerRecordRepository(None, None)
    point_read = Mock(wraps=timers.get_by_checkitem_and_arc_type)
    timers.get_by_checkitem_and_arc_type = point_read
    rows = [CheckitemTimerRecord(id=i + 5000, checkitem_id=i, status=CheckitemStatus.Started) for i in range(1, 1000)]
    timestamp = datetime(2026, 10, 5, tzinfo=UTC)
    rows[0].created_at = timestamp
    # Same timestamp, greater ID is the authoritative newest event.
    rows.append(CheckitemTimerRecord(id=9000, checkitem_id=1, status=CheckitemStatus.Stopped, created_at=timestamp))
    with engine.begin() as conn:
        conn.execute(
            CheckitemTimerRecord.__table__.insert(),
            [{k: getattr(row, k) for k in CheckitemTimerRecord.model_fields} for row in rows],
        )
    queries = []

    def capture_query(conn, cursor, statement, params, context, many):
        queries.append(statement)

    event.listen(engine, "before_cursor_execute", capture_query)
    service = CheckitemService(
        lambda _: None,
        lambda _: None,
        SimpleNamespace(
            checkitem=SimpleNamespace(
                get_all_by_checklist=Mock(return_value=records),
                get_all_by_card=Mock(return_value=records),
                get_started_work_by_user=Mock(return_value=active),
            ),
            checkitem_timer_record=timers,
        ),
    )
    infra = import_module(CheckitemService.__module__).InfraHelper
    monkeypatch.setattr(infra, "get_records_with_foreign_by_params", lambda *args: (card, checklist))
    monkeypatch.setattr(infra, "get_by_id_like", lambda *args: card)
    try:
        if view == "checklist":
            result = service.get_api_list_by_checklist(card, checklist)
        elif view == "card":
            result = service.get_api_map_by_card(card)[3000]
        else:
            result = [row["checkitem"] for row in service.get_active_work(SimpleNamespace())]
        assert len(result) == 1000
        assert "timer_started_at" not in result[0]
        assert "timer_started_at" in result[1]
        assert "timer_started_at" not in result[-1]
        timer_reads = [
            query
            for query in queries
            if query.lstrip().upper().startswith("SELECT") and "checkitem_timer_record" in query
        ]
        assert len(timer_reads) == 1
        point_read.assert_not_called()
        # Existing single-event callers resolve ties identically to the batch.
        assert point_read(1, "last").id == 9000
    finally:
        event.remove(engine, "before_cursor_execute", capture_query)


def test_arc_batch_keeps_endpoints_without_loading_history_or_other_items(timer_engine):
    engine = timer_engine
    early = datetime(2026, 10, 5, tzinfo=UTC)
    late = datetime(2026, 10, 6, tzinfo=UTC)
    with Session(engine) as db:
        db.add(Card(id=2000, project_id=10, project_column_id=40, title="Timer endpoints"))
        db.commit()
        db.add(Checklist(id=3000, card_id=2000, title="Work"))
        db.commit()
        db.add_all([Checkitem(id=i, checklist_id=3000, title=f"Work {i}") for i in range(1, 5)])
        db.commit()
        db.add_all(
            [
                # First and last use the ID to break equal timestamps.
                CheckitemTimerRecord(id=100, checkitem_id=1, status=CheckitemStatus.Started, created_at=early),
                CheckitemTimerRecord(id=101, checkitem_id=1, status=CheckitemStatus.Started, created_at=early),
                CheckitemTimerRecord(id=102, checkitem_id=1, status=CheckitemStatus.Stopped, created_at=early),
                # Timestamp takes priority over ID; a single event is both endpoints.
                CheckitemTimerRecord(id=200, checkitem_id=2, status=CheckitemStatus.Started, created_at=late),
                CheckitemTimerRecord(id=201, checkitem_id=2, status=CheckitemStatus.Stopped, created_at=early),
                CheckitemTimerRecord(id=300, checkitem_id=4, status=CheckitemStatus.Started, created_at=early),
            ]
        )
        db.commit()
    queries, hydrated = [], []

    def capture_query(conn, cursor, statement, params, context, many):
        queries.append(statement)

    def capture_record(record, context):
        hydrated.append(record.id)

    timers = CheckitemTimerRecordRepository(None, None)
    event.listen(engine, "before_cursor_execute", capture_query)
    event.listen(CheckitemTimerRecord, "load", capture_record)
    try:
        assert timers.get_arc_map_by_checkitems([]) == {}
        assert queries == []
        # Item 3 has no history. Item 4 is deliberately outside the requested set.
        result = timers.get_arc_map_by_checkitems([SimpleNamespace(id=i) for i in (1, 2, 3)])
        assert len(queries) == 1
        assert set(hydrated) == {100, 102, 200, 201}
        assert set(result) == {1, 2}
        assert result[1]["first"].id == 100
        assert result[1]["last"].id == 102
        assert result[2]["first"].id == 201
        assert result[2]["last"].id == 200
        queries.clear()
        hydrated.clear()
        single = timers.get_arc_map_by_checkitems([SimpleNamespace(id=4)])
        assert len(queries) == 1
        assert hydrated == [300]
        assert single[4]["first"] is single[4]["last"]
    finally:
        event.remove(engine, "before_cursor_execute", capture_query)
        event.remove(CheckitemTimerRecord, "load", capture_record)


@pytest.mark.parametrize("internal", [False, True])
def test_active_workers_scope_before_loading_timers(timer_engine, internal):
    timestamp = datetime(2026, 10, 6, tzinfo=UTC)
    with Session(timer_engine) as db:
        db.add(User(id=3, firstname="Other", lastname="Owner", email="other@example.invalid", password="test-only"))
        db.commit()
        for index, fields in enumerate([
            {"visibility": "SHARED"},
            {"visibility": "INTERNAL"},
            {"visibility": "PRIVATE", "owner_user_id": 2, "created_by_user_id": 2},
            {"visibility": "PRIVATE", "owner_user_id": 3, "created_by_user_id": 3},
            {"visibility": "SHARED", "deleted_at": timestamp},
            {"visibility": "SHARED", "archived_at": timestamp},
        ]):
            db.add(Card(id=2000 + index, project_id=10, project_column_id=40, title=f"Card {index}", **fields))
            db.commit()
            db.add(Checklist(id=3000 + index, card_id=2000 + index, title="Work"))
            db.commit()
            db.add(Checkitem(id=4000 + index, checklist_id=3000 + index, title="Work", user_id=2,
                             status=CheckitemStatus.Started))
            db.commit()
            db.add(CheckitemTimerRecord(id=5000 + index, checkitem_id=4000 + index,
                                       status=CheckitemStatus.Started, created_at=timestamp))
            db.commit()

    def make_repo(repository):
        return repository(make_repo, lambda _: None)

    workers = make_repo(CheckitemRepository)
    # Deleted and archived parents must never surface even for legacy callers.
    assert {checklist.card_id for _, checklist, _ in workers.get_active_workers_by_project(10)} == {2000, 2001, 2002, 2003}
    context = CardVisibilityContext(CollaborationChannel.HumanUI, True, True, internal, actor_user_id=2)
    hydrated, statements = [], []

    def on_load(record, _context):
        hydrated.append(record.checkitem_id)

    def on_query(_conn, _cursor, statement, *_args):
        statements.append(statement)

    event.listen(CheckitemTimerRecord, "load", on_load)
    event.listen(timer_engine, "before_cursor_execute", on_query)
    try:
        result = workers.get_active_workers_by_project(10, context=context)
        expected = {2000, 2002} | ({2001} if internal else set())
        assert {checklist.card_id for _, checklist, _ in result} == expected
        assert set(hydrated) == {card_id + 2000 for card_id in expected}
        assert len(statements) == 2
        assert all(anchor.replace(tzinfo=UTC) == timestamp for _, _, anchor in result)
    finally:
        event.remove(CheckitemTimerRecord, "load", on_load)
        event.remove(timer_engine, "before_cursor_execute", on_query)
