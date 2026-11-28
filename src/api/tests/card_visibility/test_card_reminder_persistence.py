"""Verify calendar state changes against the real SQLite and PostgreSQL transactions."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
import pytest
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.domain.models import ProjectColumn, UserNotification, WorkflowStageDefinition
from langboard_shared.domain.models.CardReminder import CardReminderOccurrence, CardReminderRule
from langboard_shared.domain.services.CardReminderPersistence import (
    change_next_occurrence,
    create_automatic_deadline_reminder,
    create_rule,
    materialize_due_occurrence,
    next_due_reminders_for_cards,
    reconcile_card_reminders,
    reminder_utc,
    reschedule_rule,
)
from langboard_shared.domain.services.CardReminderSchedule import ReminderSchedule


def at(day: int) -> datetime:
    return datetime(2027, 1, day, 9, tzinfo=timezone.utc)


@pytest.mark.parametrize("current_card", ["sqlite://", "postgresql-test"], indirect=True)
def test_automatic_deadline_reminder_is_opt_in_private_and_reuses_rule(current_card):
    user, project, card, _ = current_card
    for model in (CardReminderRule, CardReminderOccurrence, WorkflowStageDefinition):
        model.__table__.create(DbEngine.get_main_engine())
    column = ProjectColumn(project_id=project.id, name="Active")
    earlier = datetime(2090, 1, 1, 9, tzinfo=timezone.utc)
    later = earlier + timedelta(days=1)
    card.deadline_at = earlier

    assert create_automatic_deadline_reminder(card, user, column) is None
    with DbSession.atomic() as db:
        user.auto_deadline_reminder = True
        db.update(user)

    rule = create_automatic_deadline_reminder(card, user, column)
    assert rule is not None
    assert rule.owner_id == user.id
    assert not rule.is_shared and not rule.external_delivery
    assert reminder_utc(rule.next_due_at) == earlier
    assert create_automatic_deadline_reminder(card, user, column) is None
    assert next_due_reminders_for_cards([card.id], int(user.id) + 1, at(1)) == {}

    card.deadline_at = None
    reconcile_card_reminders(card, column, now=at(1))
    assert next_due_reminders_for_cards([card.id], user.id, at(1)) == {}
    card.deadline_at = later
    reconcile_card_reminders(card, column, now=at(1))
    assert create_automatic_deadline_reminder(card, user, column) is None
    assert next_due_reminders_for_cards([card.id], user.id, at(1)) == {card.id: later.isoformat()}


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


@pytest.mark.parametrize("current_card", ["sqlite://", "postgresql-test"], indirect=True)
def test_card_workflow_and_deadline_reconcile_the_rule(current_card):
    user, project, card, _ = current_card
    for model in (CardReminderRule, CardReminderOccurrence, UserNotification, WorkflowStageDefinition):
        model.__table__.create(DbEngine.get_main_engine())
    active = ProjectColumn(project_id=project.id, name="Done", workflow_stage="active")
    completed = ProjectColumn(project_id=project.id, name="Custom release", workflow_stage="released")
    archive = ProjectColumn(project_id=project.id, name="Shelf", is_archive=True)
    with DbSession.atomic() as db:
        db.insert(WorkflowStageDefinition(key="active", name="Active"))
        db.insert(WorkflowStageDefinition(key="released", name="Released", counts_as_completed=True))

    relative = ReminderSchedule("UTC", deadline_offset=timedelta(days=-1), frequency="daily")
    absolute = ReminderSchedule("UTC", anchor=at(3), frequency="daily")
    card.deadline_at = at(5)
    scheduled = create_rule(card.id, user.id, relative, at(1), deadline=card.deadline_at)
    unchanged = create_rule(card.id, user.id, absolute, at(1))

    card.deadline_at = at(8)
    reconcile_card_reminders(card, active, now=at(2))
    assert materialize_due_occurrence(scheduled.id, 1, at(4), deadline=at(5)) is None
    assert materialize_due_occurrence(scheduled.id, 2, at(6), deadline=at(8)) is None
    assert materialize_due_occurrence(unchanged.id, 1, at(3)) is not None

    reconcile_card_reminders(card, completed, now=at(6))
    assert materialize_due_occurrence(scheduled.id, 3, at(8), deadline=at(8)) is None
    reconcile_card_reminders(card, active, now=at(9))
    assert materialize_due_occurrence(scheduled.id, 4, at(9), deadline=at(8)) is None
    occurrence = materialize_due_occurrence(scheduled.id, 4, at(10), deadline=at(8))
    assert occurrence is not None and occurrence.due_at == at(10)

    reconcile_card_reminders(card, archive, now=at(11))
    assert materialize_due_occurrence(scheduled.id, 5, at(12), deadline=at(8)) is None
    assert reschedule_rule(scheduled.id, 5, relative, at(12), deadline=at(8), is_runnable=False, enabled=False)
    reconcile_card_reminders(card, active, now=at(12))
    assert materialize_due_occurrence(scheduled.id, 6, at(13), deadline=at(8)) is None
    with DbSession.atomic() as db:
        stored = db.exec(SqlBuilder.select.table(CardReminderRule).where(CardReminderRule.id == scheduled.id)).first()
        assert stored.consumed_count == 1
        assert stored.next_due_at is None



@pytest.mark.parametrize("current_card", ["sqlite://", "postgresql-test"], indirect=True)
def test_board_projects_only_earliest_visible_next_reminder(current_card):
    owner, _, card, _ = current_card
    CardReminderRule.__table__.create(DbEngine.get_main_engine())
    reader_id = int(owner.id) + 1
    personal = ReminderSchedule("UTC", anchor=at(3))
    shared = ReminderSchedule("UTC", anchor=at(5))
    earlier = ReminderSchedule("UTC", anchor=at(2))
    # The fixture owner owns the personal rule. The reader ID is only used in the visibility predicate.
    create_rule(card.id, owner.id, personal, at(1))
    create_rule(card.id, owner.id, shared, at(1), is_shared=True)
    disabled = create_rule(card.id, owner.id, earlier, at(1), is_shared=True)
    with DbSession.atomic() as db:
        disabled.enabled = False
        db.update(disabled)
    assert next_due_reminders_for_cards([card.id], owner.id, at(1)) == {card.id: at(3).isoformat()}
    assert next_due_reminders_for_cards([card.id], reader_id, at(1)) == {card.id: at(5).isoformat()}
    assert next_due_reminders_for_cards([card.id], owner.id, at(4)) == {card.id: at(5).isoformat()}
    assert next_due_reminders_for_cards([], owner.id, at(1)) == {}
    assert next_due_reminders_for_cards([card.id], owner.id, at(6)) == {}



@pytest.mark.parametrize("current_card", ["sqlite://", "postgresql-test"], indirect=True)
def test_skip_preserves_count_and_survives_edit_and_outage(current_card):
    user, _, card, _ = current_card
    for model in (CardReminderRule, CardReminderOccurrence):
        model.__table__.create(DbEngine.get_main_engine())
    schedule = ReminderSchedule("UTC", anchor=at(2), frequency="daily", count=2)
    rule = create_rule(card.id, user.id, schedule, at(1))

    def skip(revision, due, now=at(1)):
        return change_next_occurrence(rule.id, revision, due, now, deadline=None, is_runnable=True)

    assert skip(1, at(3)) is None
    skipped = skip(1, at(2))
    assert skipped and skipped.skipped
    assert skip(1, at(2), at(4)).id == skipped.id
    assert skip(2, at(2)) is None
    with DbSession.atomic() as db:
        saved = db.exec(SqlBuilder.select.table(CardReminderRule).where(CardReminderRule.id == rule.id)).first()
        assert reminder_utc(saved.next_due_at) == at(3)
        assert saved.revision == 2 and saved.consumed_count == 0
        assert saved.schedule == rule.schedule

    assert reschedule_rule(rule.id, 2, schedule, at(1), is_runnable=True)
    assert materialize_due_occurrence(rule.id, 3, at(2)) is None
    first = materialize_due_occurrence(rule.id, 3, at(4))
    second = materialize_due_occurrence(rule.id, 3, at(5))
    assert first and not first.skipped and first.due_at == at(4)
    assert second and not second.skipped and second.due_at == at(5)
    assert materialize_due_occurrence(rule.id, 3, at(6)) is None



@pytest.mark.parametrize("current_card", ["sqlite://", "postgresql-test"], indirect=True)
def test_moved_occurrence_preserves_intervening_regular_slots_and_its_count(current_card):
    user, _, card, _ = current_card
    for model in (CardReminderRule, CardReminderOccurrence):
        model.__table__.create(DbEngine.get_main_engine())
    schedule = ReminderSchedule("UTC", anchor=at(2), frequency="daily", count=2, until=at(8))
    rule = create_rule(card.id, user.id, schedule, at(1))

    def move(target, now=at(1)):
        return change_next_occurrence(rule.id, 1, at(2), now, deadline=None, is_runnable=True, moved_to=target)

    assert move(at(9)) is None
    moved = move(at(5))
    assert moved and moved.moved_to == at(5) and moved.due_at == at(2)
    assert move(at(6)) is None
    assert move(at(5), at(6)).id == moved.id
    assert materialize_due_occurrence(rule.id, 2, at(2)) is None
    first = materialize_due_occurrence(rule.id, 2, at(4))
    assert first and first.due_at == at(4) and first.moved_to is None
    with DbSession.atomic() as db:
        saved = db.exec(SqlBuilder.select.table(CardReminderRule).where(CardReminderRule.id == rule.id)).first()
        assert saved.next_regular_due_at is None and reminder_utc(saved.next_due_at) == at(5)
        assert saved.consumed_count == 1
    delivered = materialize_due_occurrence(rule.id, 2, at(5))
    assert delivered and delivered.id == moved.id and delivered.moved_processed_at == at(5)
    assert materialize_due_occurrence(rule.id, 2, at(6)) is None


@pytest.mark.parametrize("current_card", ["sqlite://", "postgresql-test"], indirect=True)
def test_moved_occurrence_missed_during_pause_does_not_consume_delivery(current_card):
    user, _, card, _ = current_card
    for model in (CardReminderRule, CardReminderOccurrence):
        model.__table__.create(DbEngine.get_main_engine())
    schedule = ReminderSchedule("UTC", anchor=at(2), frequency="daily", count=1)
    rule = create_rule(card.id, user.id, schedule, at(1))
    moved = change_next_occurrence(rule.id, 1, at(2), at(1), deadline=None, is_runnable=True, moved_to=at(4))
    assert moved
    assert reschedule_rule(rule.id, 2, schedule, at(3), is_runnable=False)
    assert materialize_due_occurrence(rule.id, 3, at(4)) is None
    assert reschedule_rule(rule.id, 3, schedule, at(5), is_runnable=True)
    with DbSession.atomic() as db:
        skipped = db.exec(SqlBuilder.select.table(CardReminderOccurrence).where(CardReminderOccurrence.id == moved.id)).first()
        assert skipped.skipped and skipped.moved_processed_at is None
    delivered = materialize_due_occurrence(rule.id, 4, at(6))
    assert delivered and delivered.id != moved.id and delivered.due_at == at(6)
    assert materialize_due_occurrence(rule.id, 4, at(7)) is None
