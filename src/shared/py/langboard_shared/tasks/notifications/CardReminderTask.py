"""Materialize due card reminders and persist in-app notifications atomically."""

from datetime import datetime
from pydantic import TypeAdapter
from ...core.db import DbSession, SqlBuilder
from ...core.logger import Logger
from ...domain.models import Card, CardReminderRule, Project, User
from ...domain.services import DomainService
from ...domain.services.CardReminderAudience import current_reminder_recipients
from ...domain.services.CardReminderPersistence import (
    card_allows_reminders,
    expire_snoozes_for_rules,
    materialize_due_occurrence,
    reminder_utc,
    reschedule_rule,
)
from ...domain.services.CardReminderSchedule import ReminderSchedule


logger = Logger.use("card_reminders")
_schedule = TypeAdapter(ReminderSchedule)


def run_due_card_reminders(service: DomainService, now: datetime, *, limit: int = 100) -> int:
    with DbSession.use(readonly=False) as db:
        due_ids = db.exec(
            SqlBuilder.select.column(CardReminderRule.id)
            .where(CardReminderRule.enabled.is_(True), CardReminderRule.next_due_at <= now)
            .order_by(CardReminderRule.next_due_at, CardReminderRule.id)
            .limit(limit)
        ).all()
    delivered = 0
    for rule_id in due_ids:
        try:
            delivered += _process_rule(service, rule_id, now)
        except Exception:
            # Keep other cards moving. The transaction rolls back and the same due rule is retried.
            logger.error(f"Card reminder rule {rule_id} could not be processed")
    return delivered


def _process_rule(service: DomainService, rule_id: int, now: datetime) -> int:
    with DbSession.atomic() as db:
        rule = db.exec(SqlBuilder.select.table(CardReminderRule).where(CardReminderRule.id == rule_id)).first()
        if rule is None:
            return 0
        card = db.exec(SqlBuilder.select.table(Card).where(Card.id == rule.card_id).with_for_update()).first()
        if card is None:
            return 0
        rule = db.exec(
            SqlBuilder.select.table(CardReminderRule)
            .where(CardReminderRule.id == rule_id)
            .execution_options(populate_existing=True)
        ).first()
        if rule is None or not rule.enabled or rule.next_due_at is None or reminder_utc(rule.next_due_at) > now:
            return 0
        deadline = reminder_utc(card.deadline_at) if card.deadline_at is not None else None
        project = db.exec(SqlBuilder.select.table(Project).where(Project.id == card.project_id)).first()
        active = bool(project and project.deleted_at is None and card_allows_reminders(db, card))
        schedule = _schedule.validate_python(rule.schedule)
        if not active:
            reschedule_rule(rule.id, rule.revision, schedule, now, deadline=deadline, is_runnable=False)
            return 0
        if schedule.deadline_offset is not None and (
            rule.deadline_snapshot is None or reminder_utc(rule.deadline_snapshot) != deadline
        ):
            reschedule_rule(rule.id, rule.revision, schedule, now, deadline=deadline, is_runnable=True)
            return 0

        notifier = db.exec(SqlBuilder.select.table(User).where(User.id == rule.owner_id)).first()
        if notifier is None:
            reschedule_rule(rule.id, rule.revision, schedule, now, deadline=deadline, is_runnable=True)
            return 0
        recipients = current_reminder_recipients(db, service.card, card, rule)
        if not recipients:
            # Neither revoke access permanently nor charge a recurrence during the revoked interval.
            reschedule_rule(rule.id, rule.revision, schedule, now, deadline=deadline, is_runnable=True)
            return 0
        occurrence = materialize_due_occurrence(rule.id, rule.revision, now, deadline=deadline)
        if occurrence is None:
            return 0
        if occurrence.moved_to is None:
            expire_snoozes_for_rules([int(rule.id)])
        count = sum(
            service.notification.notify_card_reminder(
                notifier, recipient, project, card, occurrence, external_delivery=rule.external_delivery,
                web_push_delivery=rule.web_push_delivery,
            )
            for recipient in recipients
        )
        if not count:
            raise RuntimeError("Card reminder recipients changed before delivery")
        return count
