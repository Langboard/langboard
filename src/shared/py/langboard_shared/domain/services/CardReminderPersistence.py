"""Transactional schedule storage for authorized card reminder callers."""

from datetime import datetime, timezone
from pydantic import TypeAdapter
from sqlalchemy import update
from ...core.db import DbSession, SqlBuilder
from ...core.types import SafeDateTime
from ..models.Card import Card
from ..models.CardReminder import CardReminderOccurrence, CardReminderRule
from ..models.ProjectColumn import ProjectColumn
from ..models.WorkflowStageDefinition import WorkflowStageDefinition
from .CardReminderSchedule import ReminderSchedule, next_reminder


_schedule_adapter: TypeAdapter[ReminderSchedule] = TypeAdapter(ReminderSchedule)


def reminder_utc(value: datetime) -> datetime:
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
    request_uid: str | None = None,
    request_fingerprint: str | None = None,
) -> CardReminderRule:
    due_at = next_reminder(schedule, now, deadline=deadline)
    rule = CardReminderRule(
        card_id=card_id,
        owner_id=owner_id,
        is_shared=is_shared,
        request_uid=request_uid,
        request_fingerprint=request_fingerprint,
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
    enabled: bool | None = None,
) -> bool:
    """Move the next due time forward or pause it without resetting the consumed count."""
    with DbSession.atomic() as db:
        rule = db.exec(SqlBuilder.select.table(CardReminderRule).where(CardReminderRule.id == rule_id)).first()
        if rule is None or rule.revision != expected_revision:
            return False
        enabled = rule.enabled if enabled is None else enabled
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
                enabled=enabled,
                next_due_at=due_at if enabled and is_runnable else None,
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
        if rule is None or rule.revision != expected_revision or not rule.enabled or rule.next_due_at is None:
            return None
        due_at = reminder_utc(rule.next_due_at)
        if due_at > reminder_utc(now):
            return None
        schedule = _schedule_adapter.validate_python(rule.schedule)
        if schedule.deadline_offset is not None and (
            deadline is None
            or rule.deadline_snapshot is None
            or reminder_utc(rule.deadline_snapshot) != reminder_utc(deadline)
        ):
            return None
        now = reminder_utc(now)
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


def reconcile_card_reminders(card: Card, column: ProjectColumn | None = None, *, now: datetime | None = None) -> None:
    """Keep pending reminders aligned with the card transaction and workflow policy."""

    with DbSession.atomic() as db:
        rules = db.exec(SqlBuilder.select.table(CardReminderRule).where(CardReminderRule.card_id == card.id)).all()
        if not rules:
            return
        active = card_allows_reminders(db, card, column)
        if not active:
            from .CardReminderSnooze import expire_snoozes_for_rules

            expire_snoozes_for_rules([int(rule.id) for rule in rules])
        now = now if now is not None else SafeDateTime.now()
        deadline = reminder_utc(card.deadline_at) if card.deadline_at is not None else None
        for rule in rules:
            if not rule.enabled:
                continue
            schedule = _schedule_adapter.validate_python(rule.schedule)
            snapshot = reminder_utc(rule.deadline_snapshot) if rule.deadline_snapshot is not None else None
            deadline_changed = schedule.deadline_offset is not None and deadline != snapshot
            if not deadline_changed and active == (rule.next_due_at is not None):
                continue
            next_due = (
                next_reminder(schedule, now, deadline=deadline, consumed_count=rule.consumed_count) if active else None
            )
            if next_due is None and rule.next_due_at is None and not deadline_changed:
                continue
            if not reschedule_rule(rule.id, rule.revision, schedule, now, deadline=deadline, is_runnable=active):
                raise RuntimeError("Card reminders changed during card lifecycle reconciliation")


def card_allows_reminders(db: DbSession, card: Card, column: ProjectColumn | None = None) -> bool:
    """Check the current card and its workflow policy independent of the column label."""
    if column is None:
        column = db.exec(
            SqlBuilder.select.table(ProjectColumn).where(ProjectColumn.id == card.project_column_id)
        ).first()
    active = bool(
        card.deleted_at is None
        and card.archived_at is None
        and column
        and column.deleted_at is None
        and not column.is_archive
    )
    if active and column and column.workflow_stage is not None:
        stage = db.exec(
            SqlBuilder.select.table(WorkflowStageDefinition).where(WorkflowStageDefinition.key == column.workflow_stage)
        ).first()
        active = not (stage is not None and stage.counts_as_completed)

    return active
