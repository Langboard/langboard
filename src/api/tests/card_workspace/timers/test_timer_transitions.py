"""Persisted timer transitions reject stale snapshots and preserve elapsed time."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from langboard_shared.domain.models import Card, Checkitem, CheckitemTimerRecord, Checklist, Project, User
from langboard_shared.domain.models.Checkitem import CheckitemStatus
from langboard_shared.domain.services.factory.CheckitemService import CheckitemService
from langboard_shared.infrastructure.repositories.factory.CheckitemRepository import CheckitemRepository
from langboard_shared.infrastructure.repositories.factory.CheckitemTimerRecordRepository import (
    CheckitemTimerRecordRepository,
)
from langboard_shared.publishers import CheckitemPublisher
from langboard_shared.tasks.activities import CardCheckitemActivityTask
from langboard_shared.tasks.bots import CardCheckitemBotTask
from sqlalchemy import select, update
from sqlalchemy.orm import Session


@pytest.fixture
def transition(timer_engine, monkeypatch):
    now = datetime(2026, 10, 24, tzinfo=UTC)
    with Session(timer_engine, expire_on_commit=False) as db:
        db.add(Card(id=2000, project_id=10, project_column_id=40, title="Transitions"))
        db.commit()
        db.add(Checklist(id=3000, card_id=2000, title="Work"))
        db.commit()
        db.add(
            Checkitem(
                id=1, checklist_id=3000, title="Timer", user_id=2, status=CheckitemStatus.Started, accumulated_seconds=7
            )
        )
        db.commit()
        db.add(
            CheckitemTimerRecord(
                id=100, checkitem_id=1, status=CheckitemStatus.Started, created_at=now - timedelta(seconds=10)
            )
        )
        db.commit()
        actor, project, card, item = db.get(User, 2), db.get(Project, 10), db.get(Card, 2000), db.get(Checkitem, 1)
    for cls in (CheckitemPublisher, CardCheckitemActivityTask, CardCheckitemBotTask):
        for name in dir(cls):
            if name in ("status_changed", "board_progress_changed") or name.startswith("card_checkitem_timer_"):
                monkeypatch.setattr(cls, name, Mock())
    service = CheckitemService(
        lambda _: None,
        lambda _: None,
        SimpleNamespace(
            checkitem=CheckitemRepository(None, None), checkitem_timer_record=CheckitemTimerRecordRepository(None, None)
        ),
    )
    monkeypatch.setattr(service, "_mark_card_changed_for_unread", Mock())
    return service, actor, project, card, item, now


def test_first_pause_uses_persisted_timer_time(transition, timer_engine):
    service, actor, project, card, item, now = transition
    assert service.change_status(actor, project, card, item, CheckitemStatus.Paused, now)
    with Session(timer_engine) as db:
        saved = db.get(Checkitem, 1)
        assert saved.status == CheckitemStatus.Paused
        assert saved.accumulated_seconds == 17
        assert len(db.scalars(select(CheckitemTimerRecord)).all()) == 2


def test_stale_pause_retry_does_not_duplicate_or_lose_elapsed_time(transition, timer_engine):
    service, actor, project, card, item, now = transition
    stale = Checkitem(**{name: getattr(item, name) for name in Checkitem.model_fields})
    assert service.change_status(actor, project, card, item, CheckitemStatus.Paused, now)
    assert service.change_status(actor, project, card, stale, CheckitemStatus.Paused, now + timedelta(seconds=5))
    with Session(timer_engine) as db:
        saved = db.get(Checkitem, 1)
        assert saved.accumulated_seconds == 17
        assert len(db.scalars(select(CheckitemTimerRecord)).all()) == 2


def test_overlapping_pause_requests_append_one_fact(transition, timer_engine):
    if timer_engine.dialect.name != "postgresql":
        pytest.skip("Row-lock concurrency requires PostgreSQL")
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, current_thread
    from sqlalchemy import event

    service, actor, project, card, item, now = transition
    first_writing, release_first, second_read = Event(), Event(), Event()

    def hold_first_write(conn, cursor, statement, params, context, many):
        if current_thread().name.endswith("_0") and statement.startswith("UPDATE checkitem SET"):
            if not first_writing.is_set():
                first_writing.set()
                assert release_first.wait(5), "First write was never released"
        if current_thread().name.endswith("_1") and "FROM checkitem_timer_record" in statement:
            second_read.set()

    event.listen(timer_engine, "before_cursor_execute", hold_first_write)
    try:
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="timer") as pool:
            first = pool.submit(service.change_status, actor, project, card, item, CheckitemStatus.Paused, now)
            assert first_writing.wait(5)
            second = pool.submit(service.change_status, actor, project, card, item, CheckitemStatus.Paused, now)
            # Before repair the second request reads the old timer while the first
            # owns an uncommitted write. With locks it waits before reading state.
            second_read.wait(0.3)
            release_first.set()
            assert first.result(5)
            assert second.result(5)
    finally:
        release_first.set()
        event.remove(timer_engine, "before_cursor_execute", hold_first_write)
    with Session(timer_engine) as db:
        assert db.get(Checkitem, 1).accumulated_seconds == 17
        assert len(db.scalars(select(CheckitemTimerRecord)).all()) == 2


def test_completion_stops_timer_and_keeps_checked_intent(transition, timer_engine, monkeypatch):
    service, actor, project, card, item, _ = transition
    monkeypatch.setattr(CardCheckitemActivityTask, "card_checkitem_checked", Mock())
    monkeypatch.setattr(CardCheckitemBotTask, "card_checkitem_checked", Mock())
    assert service.toggle_checked(actor, project, card, item, desired_checked=True)
    with Session(timer_engine) as db:
        saved = db.get(Checkitem, 1)
        assert saved.is_checked
        assert saved.status == CheckitemStatus.Stopped
        assert len(db.scalars(select(CheckitemTimerRecord)).all()) == 2


def test_overlapping_starts_on_different_cards_leave_one_active_timer(transition, timer_engine):
    if timer_engine.dialect.name != "postgresql":
        pytest.skip("Row-lock concurrency requires PostgreSQL")
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, current_thread
    from sqlalchemy import event

    service, actor, project, card, item, now = transition
    with Session(timer_engine) as db:
        db.execute(update(Checkitem).where(Checkitem.id == 1).values(status=CheckitemStatus.Stopped))
        db.execute(
            update(CheckitemTimerRecord).where(CheckitemTimerRecord.id == 100).values(status=CheckitemStatus.Stopped)
        )
        db.add(Card(id=2001, project_id=10, project_column_id=40, title="Other card"))
        db.commit()
        db.add(Checklist(id=3001, card_id=2001, title="Other work"))
        db.commit()
        db.add(Checkitem(id=2, checklist_id=3001, title="Other timer", user_id=2))
        db.commit()
    first_writing, release_first, second_query = Event(), Event(), Event()

    def hold_first_write(conn, cursor, statement, params, context, many):
        if current_thread().name.endswith("_0") and statement.startswith("UPDATE checkitem SET"):
            if not first_writing.is_set():
                first_writing.set()
                assert release_first.wait(5)
        if current_thread().name.endswith("_1") and "FROM checkitem " in statement:
            second_query.set()

    event.listen(timer_engine, "before_cursor_execute", hold_first_write)
    try:
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="timer") as pool:
            first = pool.submit(service.change_status, actor, project, card, 1, CheckitemStatus.Started, now)
            assert first_writing.wait(5)
            second = pool.submit(service.change_status, actor, project, 2001, 2, CheckitemStatus.Started, now)
            second_query.wait(0.3)
            release_first.set()
            assert first.result(5)
            assert second.result(5)
    finally:
        release_first.set()
        event.remove(timer_engine, "before_cursor_execute", hold_first_write)
    with Session(timer_engine) as db:
        assert db.get(Checkitem, 1).status == CheckitemStatus.Paused
        assert db.get(Checkitem, 2).status == CheckitemStatus.Started
        assert db.get(Checkitem, 1).user_id == db.get(Checkitem, 2).user_id == actor.id
