"""Verify calendar state changes against the real SQLite and PostgreSQL transactions."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
import pytest
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.domain.models.CardReminder import CardReminderOccurrence, CardReminderRule
from langboard_shared.domain.services.CardReminderPersistence import (
    create_rule,
    materialize_due_occurrence,
    reschedule_rule,
)
from langboard_shared.domain.services.CardReminderSchedule import ReminderSchedule


def at(day: int) -> datetime:
    return datetime(2027, 1, day, 9, tzinfo=timezone.utc)


@pytest.mark.parametrize("current_card", ["sqlite://", "postgresql-test"], indirect=True)
def test_durable_reminder_pause_recovery_and_count(current_card, monkeypatch):
    user, _, card, _ = current_card
    for model in (CardReminderRule, CardReminderOccurrence):
        model.__table__.create(DbEngine.get_main_engine())
    schedule = ReminderSchedule("UTC", anchor=at(2), frequency="daily", count=2)
    rule = create_rule(card.id, user.id, schedule, at(1))
    assert materialize_due_occurrence(rule.id, 1, at(1)) is None
    assert reschedule_rule(rule.id, 1, schedule, at(2), is_runnable=False)
    assert materialize_due_occurrence(rule.id, 2, at(4)) is None
    assert not reschedule_rule(rule.id, 1, schedule, at(4), is_runnable=True)
    assert reschedule_rule(rule.id, 2, schedule, at(4), is_runnable=True)
    insert = DbSession.insert

    def reject_occurrence(self, obj, *args, **kwargs):
        if isinstance(obj, CardReminderOccurrence):
            raise RuntimeError("fixture write failed")
        return insert(self, obj, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(DbSession, "insert", reject_occurrence)
        with pytest.raises(RuntimeError, match="fixture write failed"):
            materialize_due_occurrence(rule.id, 3, at(9))
    occurrence = materialize_due_occurrence(rule.id, 3, at(9))
    assert occurrence is not None and occurrence.due_at == at(9)
    assert materialize_due_occurrence(rule.id, 3, at(9)) is None
    assert materialize_due_occurrence(rule.id, 3, at(10)) is not None
    assert materialize_due_occurrence(rule.id, 3, at(11)) is None
    with DbSession.atomic() as db:
        saved = db.exec(SqlBuilder.select.table(CardReminderRule).where(CardReminderRule.id == rule.id)).first()
        assert saved.consumed_count == 2
        assert saved.next_due_at is None


@pytest.mark.parametrize("current_card", ["postgresql-test"], indirect=True)
def test_due_reminder_is_materialized_once_across_workers(current_card):
    user, _, card, _ = current_card
    for model in (CardReminderRule, CardReminderOccurrence):
        model.__table__.create(DbEngine.get_main_engine())
    schedule = ReminderSchedule("UTC", deadline_offset=timedelta(days=-1))
    rule = create_rule(card.id, user.id, schedule, at(1), deadline=at(3))
    assert materialize_due_occurrence(rule.id, 1, at(2), deadline=at(4)) is None
    gate = Barrier(2)

    def claim():
        gate.wait(timeout=5)
        return materialize_due_occurrence(rule.id, 1, at(2), deadline=at(3))

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(claim) for _ in range(2)]
        assert sum(job.result(10) is not None for job in futures) == 1
    with DbSession.atomic() as db:
        assert len(db.exec(SqlBuilder.select.table(CardReminderOccurrence)).all()) == 1
