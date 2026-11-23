"""Temporary reminder delays for the recipient's existing notification row."""

from datetime import datetime, timedelta
from logging import getLogger
from typing import Literal
from sqlalchemy import select, update
from ...core.db import DbSession, SqlBuilder
from ...core.types import SafeDateTime
from ...publishers import UserPublisher
from ..models import Card, CardReminderOccurrence, CardReminderRule, User, UserNotification
from ..models.UserNotification import NotificationType
from .CardReminderPersistence import card_allows_reminders, reminder_utc


SnoozeMinutes = Literal[10, 60, 1440]
_logger = getLogger(__name__)


class SnoozeConflict(Exception):
    """The reminder or its observed snooze was changed before the action."""


def _publish_inbox_update(user: User) -> None:
    try:
        UserPublisher.notification_mutated(user, {"action": "inbox_updated"})
    except Exception:
        _logger.warning("Could not publish reminder inbox update", exc_info=True)


def _lock_card_for_notice(db: DbSession, notice: UserNotification) -> Card | None:
    return db.exec(
        SqlBuilder.select.table(Card)
        .join(CardReminderRule, CardReminderRule.card_id == Card.id)
        .join(CardReminderOccurrence, CardReminderOccurrence.rule_id == CardReminderRule.id)
        .where(CardReminderOccurrence.id == notice.reminder_occurrence_id)
        .with_for_update(of=Card)
    ).first()


def snooze_reminder(
    user: User,
    notification_id: int,
    minutes: SnoozeMinutes,
    request_uid: str,
    expected_until: datetime | None,
    *,
    contexts,
    repository,
    now: datetime | None = None,
) -> dict | None:
    now = now if now is not None else SafeDateTime.now()
    with DbSession.atomic() as db:
        notice = db.exec(
            SqlBuilder.select.table(UserNotification).where(
                UserNotification.id == notification_id,
                UserNotification.receiver_id == user.id,
                UserNotification.notification_type == NotificationType.CardReminder,
                UserNotification.reminder_occurrence_id.is_not(None),
            )
        ).first()
        if notice is None:
            return None
        card = _lock_card_for_notice(db, notice)
        if card is None:
            return None
        visible = repository.get_scoped_events(user, {notification_id}, contexts=contexts, include_snoozed=True)
        if not visible:
            return None
        notice = db.exec(
            SqlBuilder.select.table(UserNotification)
            .where(UserNotification.id == notification_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).first()
        if notice is None:
            return None
        receipt = notice.reminder_snooze_receipt or {}
        if receipt.get("request_uid") == request_uid:
            if receipt.get("minutes") != minutes:
                raise SnoozeConflict()
            return {"snoozed_until": receipt["snoozed_until"]}
        current = reminder_utc(notice.reminder_snoozed_until) if notice.reminder_snoozed_until is not None else None
        if current != expected_until or notice.inbox_state == "done" or not card_allows_reminders(db, card):
            raise SnoozeConflict()
        until = now + timedelta(minutes=minutes)
        notice.reminder_snoozed_until = until
        notice.reminder_snooze_receipt = {
            "request_uid": request_uid,
            "minutes": minutes,
            "snoozed_until": until.isoformat(),
        }
        db.update(notice)
        db.after_commit(lambda: _publish_inbox_update(user))
        return {"snoozed_until": until.isoformat()}


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


def wake_due_snoozes(card_service, now: datetime, *, limit: int = 100) -> int:
    with DbSession.use(readonly=False) as db:
        ids = db.exec(
            SqlBuilder.select.column(UserNotification.id)
            .where(UserNotification.reminder_snoozed_until <= now)
            .order_by(UserNotification.reminder_snoozed_until, UserNotification.id)
            .limit(limit)
        ).all()
    woke = 0
    for notice_id in ids:
        try:
            woke += _wake(card_service, notice_id, now)
        except Exception:
            _logger.exception("Could not wake reminder")
    return woke


def _wake(card_service, notice_id: int, now: datetime) -> int:
    from ...core.security.CollaborationChannel import CollaborationChannel

    with DbSession.atomic() as db:
        notice = db.exec(SqlBuilder.select.table(UserNotification).where(UserNotification.id == notice_id)).first()
        if notice is None:
            return 0
        card = _lock_card_for_notice(db, notice)
        notice = db.exec(
            SqlBuilder.select.table(UserNotification)
            .where(UserNotification.id == notice_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).first()
        if notice is None or notice.reminder_snoozed_until is None or reminder_utc(notice.reminder_snoozed_until) > now:
            return 0
        notice.reminder_snoozed_until = None
        recipient = db.exec(SqlBuilder.select.table(User).where(User.id == notice.receiver_id)).first()
        should_wake = False
        if card and recipient and notice.inbox_state != "done" and card_allows_reminders(db, card):
            context = card_service.resolve_work_visibility_contexts(
                recipient, CollaborationChannel.HumanUI, project_ids={int(card.project_id)}
            ).get(int(card.project_id))
            should_wake = bool(context and context.can_read_card(card.visibility, owner_user_id=card.owner_user_id))
        if should_wake:
            notice.inbox_state = None
            notice.read_at = None
            notice.reminder_resurfaced_at = now
            db.after_commit(lambda: _publish_inbox_update(recipient))
        db.update(notice)
        return int(should_wake)
