"""Transactional schedule storage for authorized card reminder callers."""

from datetime import datetime, timedelta, timezone
from pydantic import TypeAdapter
from sqlalchemy import func, or_, select, type_coerce, update
from ...core.db import DbSession, SqlBuilder
from ...core.types import SafeDateTime
from ..models.Card import Card
from ..models.CardReminder import CardReminderOccurrence, CardReminderRule
from ..models.ProjectColumn import ProjectColumn
from ..models.User import User
from ..models.UserNotification import UserNotification
from ..models.WorkflowStageDefinition import WorkflowStageDefinition
from .CardReminderSchedule import ReminderSchedule, next_reminder


_schedule_adapter: TypeAdapter[ReminderSchedule] = TypeAdapter(ReminderSchedule)


def reminder_utc(value: datetime) -> datetime:
    # SQLite drops timezone metadata on read. All stored instants use UTC.
    return value.replace(tzinfo=timezone.utc) if value.utcoffset() is None else value.astimezone(timezone.utc)


def next_due_reminders_for_cards(card_ids: list[int], actor_id: int, now: datetime) -> dict[int, str]:
    """Project the earliest visible schedule for card IDs already authorized by the caller."""
    if not card_ids:
        return {}
    with DbSession.use(readonly=False) as db:
        rows = db.exec(
            select(CardReminderRule.card_id, type_coerce(func.min(CardReminderRule.next_due_at), CardReminderRule.next_due_at.type))
            .where(
                CardReminderRule.card_id.in_(card_ids),
                CardReminderRule.enabled.is_(True),
                CardReminderRule.next_due_at >= now,
                or_(CardReminderRule.is_shared.is_(True), CardReminderRule.owner_id == actor_id),
            )
            .group_by(CardReminderRule.card_id)
        ).all()
    return {card_id: reminder_utc(due).isoformat() for card_id, due in rows}


def create_rule(
    card_id: int,
    owner_id: int,
    schedule: ReminderSchedule,
    now: datetime,
    *,
    deadline: datetime | None = None,
    is_shared: bool = False,
    is_runnable: bool = True,
    external_delivery: bool = False,
    web_push_delivery: bool = False,
    recipient_overrides: dict | None = None,
    request_uid: str | None = None,
    request_fingerprint: str | None = None,
) -> CardReminderRule:
    due_at = next_reminder(schedule, now, deadline=deadline)
    rule = CardReminderRule(
        card_id=card_id,
        owner_id=owner_id,
        is_shared=is_shared,
        external_delivery=external_delivery,
        web_push_delivery=web_push_delivery,
        recipient_overrides=recipient_overrides,
        request_uid=request_uid,
        request_fingerprint=request_fingerprint,
        schedule=_schedule_adapter.dump_python(schedule, mode="json"),
        next_due_at=due_at if is_runnable else None,
        next_regular_due_at=due_at if is_runnable else None,
        deadline_snapshot=deadline if schedule.deadline_offset is not None else None,
    )
    with DbSession.atomic() as db:
        db.insert(rule)
    return rule


def create_automatic_deadline_reminder(card: Card, actor: User, column: ProjectColumn | None = None) -> CardReminderRule | None:
    """Create at most one automatic personal rule for this editor and card."""
    if card.deadline_at is None:
        return None
    deadline = reminder_utc(card.deadline_at)
    now = SafeDateTime.now()
    if deadline <= now:
        return None
    # Public creation requests accept UUIDs, so this stable key is reserved for this policy.
    request_uid = f"auto-deadline:{card.get_uid()}"
    with DbSession.atomic() as db:
        opted_in = db.exec(
            SqlBuilder.select.column(User.auto_deadline_reminder).where(User.id == actor.id, User.deleted_at.is_(None))
        ).first()
        if not opted_in or not card_allows_reminders(db, card, column):
            return None
        exists = db.exec(
            SqlBuilder.select.column(CardReminderRule.id).where(
                CardReminderRule.owner_id == actor.id, CardReminderRule.request_uid == request_uid
            )
        ).first()
        if exists:
            return None
        return create_rule(
            card.id, actor.id, ReminderSchedule(timezone="UTC", deadline_offset=timedelta()),
            now, deadline=deadline, request_uid=request_uid,
        )


def _skipped_times(db: DbSession, rule_id: int, after: datetime) -> set[datetime]:
    return {
        reminder_utc(value)
        for value in db.exec(
            SqlBuilder.select.column(CardReminderOccurrence.due_at).where(
                CardReminderOccurrence.rule_id == rule_id,
                or_(CardReminderOccurrence.skipped.is_(True), CardReminderOccurrence.moved_to.is_not(None)),
                CardReminderOccurrence.due_at >= after,
            )
        ).all()
    }


def _next_unskipped(
    schedule: ReminderSchedule,
    after: datetime,
    deadline: datetime | None,
    consumed_count: int,
    skipped_times: set[datetime],
) -> datetime | None:
    candidate = next_reminder(schedule, after, deadline=deadline, consumed_count=consumed_count)
    while candidate is not None and candidate in skipped_times:
        candidate = next_reminder(schedule, candidate, deadline=deadline, consumed_count=consumed_count)
    return candidate


def _pending_moves(db: DbSession, rule_id: int) -> list[CardReminderOccurrence]:
    return list(db.exec(
        SqlBuilder.select.table(CardReminderOccurrence)
        .where(
            CardReminderOccurrence.rule_id == rule_id,
            CardReminderOccurrence.moved_to.is_not(None),
            CardReminderOccurrence.moved_processed_at.is_(None),
            CardReminderOccurrence.skipped.is_(False),
        )
        .order_by(CardReminderOccurrence.moved_to, CardReminderOccurrence.id)
    ).all())


def _earliest_due(regular: datetime | None, moves: list[CardReminderOccurrence]) -> datetime | None:
    return min((instant for instant in [regular, *(reminder_utc(row.moved_to) for row in moves)] if instant is not None), default=None)


def change_next_occurrence(
    rule_id: int,
    expected_revision: int,
    expected_due_at: datetime,
    now: datetime,
    *,
    deadline: datetime | None,
    is_runnable: bool,
    moved_to: datetime | None = None,
) -> CardReminderOccurrence | None:
    """Replace one observed regular occurrence, leaving the recurrence anchor intact."""
    due_at = reminder_utc(expected_due_at)
    now = reminder_utc(now)
    target = reminder_utc(moved_to) if moved_to is not None else None
    with DbSession.atomic() as db:
        existing = db.exec(
            SqlBuilder.select.table(CardReminderOccurrence).where(
                CardReminderOccurrence.rule_id == rule_id, CardReminderOccurrence.due_at == due_at,
            )
        ).first()
        if existing is not None:
            same_move = existing.moved_to is not None and reminder_utc(existing.moved_to) == target
            same_skip = target is None and existing.moved_to is None and existing.skipped
            return existing if existing.rule_revision == expected_revision and (same_skip or same_move) else None
        rule = db.exec(SqlBuilder.select.table(CardReminderRule).where(CardReminderRule.id == rule_id)).first()
        if (
            not is_runnable or not rule or not rule.enabled or rule.revision != expected_revision
            or rule.next_regular_due_at is None or reminder_utc(rule.next_regular_due_at) != due_at or due_at <= now
        ):
            return None
        schedule = _schedule_adapter.validate_python(rule.schedule)
        if schedule.frequency == "once" or (schedule.deadline_offset is not None and (
            rule.deadline_snapshot is None or reminder_utc(rule.deadline_snapshot) != deadline
        )):
            return None
        if target is not None and (target <= now or target == due_at or (schedule.until is not None and target > schedule.until)):
            return None
        pending = _pending_moves(db, rule_id)
        exception = CardReminderOccurrence(
            rule_id=rule_id, rule_revision=expected_revision, due_at=due_at, skipped=target is None, moved_to=target,
        )
        if target is not None:
            pending.append(exception)
        next_regular = _next_unskipped(
            schedule, due_at, deadline, rule.consumed_count + len(pending), _skipped_times(db, rule_id, due_at),
        )
        changed = db.exec(
            update(CardReminderRule)
            .where(
                CardReminderRule.id == rule_id,
                CardReminderRule.revision == expected_revision,
                CardReminderRule.next_regular_due_at == rule.next_regular_due_at,
                CardReminderRule.consumed_count == rule.consumed_count,
                CardReminderRule.enabled.is_(True),
            )
            .values(next_due_at=_earliest_due(next_regular, pending), next_regular_due_at=next_regular, revision=expected_revision + 1)
        )
        if changed != 1:
            return None
        db.insert(exception)
        return exception


def reschedule_rule(
    rule_id: int,
    expected_revision: int,
    schedule: ReminderSchedule,
    now: datetime,
    *,
    deadline: datetime | None = None,
    is_runnable: bool,
    enabled: bool | None = None,
    external_delivery: bool | None = None,
    web_push_delivery: bool | None = None,
    recipient_overrides: dict | None = None,
) -> bool:
    """Move the next due time forward or pause it without resetting the consumed count."""
    with DbSession.atomic() as db:
        rule = db.exec(SqlBuilder.select.table(CardReminderRule).where(CardReminderRule.id == rule_id)).first()
        if rule is None or rule.revision != expected_revision:
            return False
        enabled = rule.enabled if enabled is None else enabled
        pending = _pending_moves(db, rule_id)
        available_moves = pending
        if enabled and is_runnable:
            available_moves = [
                row for row in pending if schedule.frequency != "once"
                and reminder_utc(row.moved_to) > reminder_utc(now)
                and (schedule.until is None or reminder_utc(row.moved_to) <= schedule.until)
            ]
            if schedule.count is not None:
                available_moves = available_moves[:max(0, schedule.count - rule.consumed_count)]
        due_at = _next_unskipped(
            schedule, now, deadline, rule.consumed_count + len(available_moves), _skipped_times(db, rule_id, now),
        )
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
                external_delivery=rule.external_delivery if external_delivery is None else external_delivery,
                web_push_delivery=rule.web_push_delivery if web_push_delivery is None else web_push_delivery,
                recipient_overrides=rule.recipient_overrides if recipient_overrides is None else recipient_overrides,
                next_due_at=_earliest_due(due_at, available_moves) if enabled and is_runnable else None,
                next_regular_due_at=due_at if enabled and is_runnable else None,
                deadline_snapshot=deadline if schedule.deadline_offset is not None else None,
            )
        )
        if changed != 1:
            return False
        retained_ids = {row.id for row in available_moves}
        for row in pending:
            if row.id not in retained_ids:
                row.skipped = True
                db.update(row)
        if not enabled:
            expire_snoozes_for_rules([rule_id])
        return True


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
        pending = _pending_moves(db, rule_id)
        moved = pending[0] if pending and reminder_utc(pending[0].moved_to) <= now else None
        consumed = rule.consumed_count + 1
        if moved is not None:
            pending = pending[1:]
            next_regular = reminder_utc(rule.next_regular_due_at) if rule.next_regular_due_at is not None else None
        else:
            if rule.next_regular_due_at is None:
                return None
            due_at = reminder_utc(rule.next_regular_due_at)
            # Recovery sends only the latest missed regular recurrence. Explicit moves are delivered separately.
            skipped_times = _skipped_times(db, rule_id, due_at)
            candidate = _next_unskipped(schedule, due_at, deadline, rule.consumed_count, skipped_times)
            while candidate is not None and candidate <= now:
                due_at = candidate
                candidate = _next_unskipped(schedule, due_at, deadline, rule.consumed_count, skipped_times)
            next_regular = None if schedule.count is not None and consumed + len(pending) >= schedule.count else candidate
        next_due_at = _earliest_due(next_regular, pending)
        changed = db.exec(
            update(CardReminderRule)
            .where(
                CardReminderRule.id == rule_id,
                CardReminderRule.revision == expected_revision,
                CardReminderRule.next_due_at == rule.next_due_at,
                CardReminderRule.consumed_count == rule.consumed_count,
            )
            .values(next_due_at=next_due_at, next_regular_due_at=next_regular, consumed_count=consumed)
        )
        if changed != 1:
            return None
        if moved is not None:
            moved.moved_processed_at = now
            db.update(moved)
            return moved
        occurrence = CardReminderOccurrence(rule_id=rule_id, rule_revision=expected_revision, due_at=due_at)
        db.insert(occurrence)
        return occurrence


def expire_snoozes_for_rules(rule_ids: list[int]) -> None:
    """Cancel future wakes while leaving historical read and done facts intact."""
    with DbSession.atomic() as db:
        occurrences = select(CardReminderOccurrence.id).where(CardReminderOccurrence.rule_id.in_(rule_ids))
        db.exec(
            update(UserNotification)
            .where(
                UserNotification.reminder_occurrence_id.in_(occurrences),
                UserNotification.reminder_snoozed_until.is_not(None),
            )
            .values(reminder_snoozed_until=None)
        )


def reconcile_card_reminders(card: Card, column: ProjectColumn | None = None, *, now: datetime | None = None) -> None:
    """Keep pending reminders aligned with the card transaction and workflow policy."""

    with DbSession.atomic() as db:
        rules = db.exec(SqlBuilder.select.table(CardReminderRule).where(CardReminderRule.card_id == card.id)).all()
        if not rules:
            return
        active = card_allows_reminders(db, card, column)
        if not active:
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
            if next_due is None and rule.next_due_at is None and not deadline_changed and not (active and _pending_moves(db, rule.id)):
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
