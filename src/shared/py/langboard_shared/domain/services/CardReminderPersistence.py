"""Transactional schedule storage for authorized card reminder callers."""

from datetime import datetime, timezone
from pydantic import TypeAdapter
from sqlalchemy import update
from ...core.db import DbSession, SqlBuilder
from ..models.CardReminder import CardReminderOccurrence, CardReminderRule
from .CardReminderSchedule import ReminderSchedule, next_reminder


_schedule_adapter: TypeAdapter[ReminderSchedule] = TypeAdapter(ReminderSchedule)


def _utc(value: datetime) -> datetime:
    # SQLite drops timezone metadata on read. All stored instants use UTC.
    return value.replace(tzinfo=timezone.utc) if value.utcoffset() is None else value.astimezone(timezone.utc)


def create_rule(
    card_id: int,
    owner_id: int,
    schedule: ReminderSchedule,
    now: datetime,
    *,
    deadline: datetime | None = None,
    is_shared: bool = False,
    is_runnable: bool = True,
) -> CardReminderRule:
    due_at = next_reminder(schedule, now, deadline=deadline)
    rule = CardReminderRule(
        card_id=card_id,
        owner_id=owner_id,
        is_shared=is_shared,
        schedule=_schedule_adapter.dump_python(schedule, mode="json"),
        next_due_at=due_at if is_runnable else None,
        deadline_snapshot=deadline if schedule.deadline_offset is not None else None,
    )
    with DbSession.atomic() as db:
        db.insert(rule)
    return rule


def reschedule_rule(
    rule_id: int,
    expected_revision: int,
    schedule: ReminderSchedule,
    now: datetime,
    *,
    deadline: datetime | None = None,
    is_runnable: bool,
) -> bool:
    """Move the next due time forward or pause it without resetting the consumed count."""
    with DbSession.atomic() as db:
        rule = db.exec(SqlBuilder.select.table(CardReminderRule).where(CardReminderRule.id == rule_id)).first()
        if rule is None or rule.revision != expected_revision:
            return False
        due_at = next_reminder(schedule, now, deadline=deadline, consumed_count=rule.consumed_count)
        changed = db.exec(
            update(CardReminderRule)
            .where(
                CardReminderRule.id == rule_id,
                CardReminderRule.revision == expected_revision,
                CardReminderRule.consumed_count == rule.consumed_count,
            )
            .values(
                schedule=_schedule_adapter.dump_python(schedule, mode="json"),
                revision=expected_revision + 1,
                next_due_at=due_at if is_runnable else None,
                deadline_snapshot=deadline if schedule.deadline_offset is not None else None,
            )
        )
        return changed == 1


def materialize_due_occurrence(
    rule_id: int,
    expected_revision: int,
    now: datetime,
    *,
    deadline: datetime | None = None,
) -> CardReminderOccurrence | None:
    """Advance the observed schedule and insert its occurrence in the same transaction."""
    with DbSession.atomic() as db:
        rule = db.exec(SqlBuilder.select.table(CardReminderRule).where(CardReminderRule.id == rule_id)).first()
        if rule is None or rule.revision != expected_revision or rule.next_due_at is None:
            return None
        due_at = _utc(rule.next_due_at)
        if due_at > _utc(now):
            return None
        schedule = _schedule_adapter.validate_python(rule.schedule)
        if schedule.deadline_offset is not None and (
            deadline is None or rule.deadline_snapshot is None or _utc(rule.deadline_snapshot) != _utc(deadline)
        ):
            return None
        now = _utc(now)
        # Recovery sends only the latest missed recurrence. Earlier timestamps consume no quota.
        candidate = next_reminder(schedule, due_at, deadline=deadline)
        while candidate is not None and candidate <= now:
            due_at = candidate
            candidate = next_reminder(schedule, due_at, deadline=deadline)
        consumed = rule.consumed_count + 1
        next_due_at = None if schedule.count is not None and consumed >= schedule.count else candidate
        changed = db.exec(
            update(CardReminderRule)
            .where(
                CardReminderRule.id == rule_id,
                CardReminderRule.revision == expected_revision,
                CardReminderRule.next_due_at == rule.next_due_at,
                CardReminderRule.consumed_count == rule.consumed_count,
            )
            .values(next_due_at=next_due_at, consumed_count=consumed)
        )
        if changed != 1:
            return None
        occurrence = CardReminderOccurrence(rule_id=rule_id, rule_revision=expected_revision, due_at=due_at)
        db.insert(occurrence)
        return occurrence
