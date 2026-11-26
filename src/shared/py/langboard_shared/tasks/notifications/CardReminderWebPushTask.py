"""Drain ready browser receipts independently of the reminder scheduling transaction."""

from datetime import datetime, timedelta
from sqlalchemy import and_, or_, select, update
from ...core.db import DbSession, SqlBuilder
from ...core.security.CollaborationChannel import CollaborationChannel
from ...core.types import SafeDateTime
from ...core.utils.Encryptor import Encryptor
from ...domain.models import (
    Card,
    CardReminderOccurrence,
    CardReminderRule,
    CardReminderWebPushDelivery,
    Project,
    User,
    UserNotification,
    UserWebPushSubscription,
)
from ...domain.models.UserNotification import NotificationType
from ...domain.services.CardReminderAudience import current_reminder_recipients
from ...domain.services.CardReminderPersistence import card_allows_reminders, reminder_utc
from ...domain.services.ReminderWebPushPolicy import get_reminder_web_push_policy, quiet_hours_end
from ...domain.services.WebPushSubscription import WebPushRegistration, browser_push_public_key
from ...domain.services.WebPushTransport import send_reminder_push
from ...Env import Env


def _claim(now: datetime):
    with DbSession.atomic() as db:
        db.exec(
            update(CardReminderWebPushDelivery)
            .where(
                CardReminderWebPushDelivery.state == "sending",
                CardReminderWebPushDelivery.next_attempt_at <= now,
            )
            .values(state="unknown", last_result="interrupted")
        )
        now_urgent = (
            select(UserNotification.id)
            .join(CardReminderOccurrence, CardReminderOccurrence.id == UserNotification.reminder_occurrence_id)
            .join(CardReminderRule, CardReminderRule.id == CardReminderOccurrence.rule_id)
            .join(Card, Card.id == CardReminderRule.card_id)
            .where(UserNotification.id == CardReminderWebPushDelivery.notification_id, Card.priority == "P0")
            .exists()
        )
        receipt = db.exec(
            SqlBuilder.select.table(CardReminderWebPushDelivery)
            .where(
                CardReminderWebPushDelivery.state == "pending",
                or_(
                    CardReminderWebPushDelivery.next_attempt_at <= now,
                    and_(CardReminderWebPushDelivery.last_result == "quiet_hours", now_urgent),
                ),
            )
            .order_by(CardReminderWebPushDelivery.next_attempt_at, CardReminderWebPushDelivery.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        ).first()
        if receipt is None:
            return None
        receipt.state = "sending"
        receipt.attempts += 1
        receipt.next_attempt_at = now + timedelta(minutes=2)
        db.update(receipt)
        return receipt.id


def _destination(service, receipt_id: int, now: datetime):
    with DbSession.use(readonly=False) as db:
        receipt = db.exec(
            SqlBuilder.select.table(CardReminderWebPushDelivery).where(CardReminderWebPushDelivery.id == receipt_id)
        ).first()
        if receipt is None or receipt.state != "sending":
            return None
        notice = db.exec(
            SqlBuilder.select.table(UserNotification).where(UserNotification.id == receipt.notification_id)
        ).first()
        device = db.exec(
            SqlBuilder.select.table(UserWebPushSubscription).where(
                UserWebPushSubscription.id == receipt.subscription_id
            )
        ).first()
        if (
            not notice
            or notice.notification_type != NotificationType.CardReminder
            or not device
            or device.signed_out
            or not device.encrypted_subscription
            or device.endpoint_digest != receipt.endpoint_digest
            or reminder_utc(device.session_expires_at) <= now
            or notice.receiver_id != device.user_id
            or not notice.web_visible
            or notice.reminder_occurrence_id is None
            or notice.read_at is not None
            or notice.inbox_state == "done"
            or notice.reminder_snoozed_until is not None
        ):
            return None
        user = db.exec(SqlBuilder.select.table(User).where(User.id == device.user_id)).first()
        if (
            not user
            or user.deleted_at is not None
            or user.activated_at is None
            or user.auth_version != device.auth_version
        ):
            return None
        occurrence = db.exec(
            SqlBuilder.select.table(CardReminderOccurrence).where(
                CardReminderOccurrence.id == notice.reminder_occurrence_id
            )
        ).first()
        if (
            not occurrence
            or occurrence.skipped
            or (occurrence.moved_to is not None and occurrence.moved_processed_at is None)
        ):
            return None
        rule = db.exec(
            SqlBuilder.select.table(CardReminderRule).where(CardReminderRule.id == occurrence.rule_id)
        ).first()
        if not rule or not rule.enabled or not rule.web_push_delivery:
            return None
        card = db.exec(SqlBuilder.select.table(Card).where(Card.id == rule.card_id)).first()
        if not card or not card_allows_reminders(db, card):
            return None
        project = db.exec(SqlBuilder.select.table(Project).where(Project.id == card.project_id)).first()
        if (
            not project
            or project.deleted_at is not None
            or user.id not in {recipient.id for recipient in current_reminder_recipients(db, service.card, card, rule)}
            or not service.notification.get_inbox_events(user, {notice.id}, channel=CollaborationChannel.HumanUI)
        ):
            return None
        urgent = card.priority == "P0"
        policy = get_reminder_web_push_policy(db, user.id)
        if urgent and policy.block_urgent:
            return "urgent_blocked"
        if rule.schedule.get("frequency") != "once":
            newer = db.exec(
                select(CardReminderWebPushDelivery.id)
                .join(UserNotification, UserNotification.id == CardReminderWebPushDelivery.notification_id)
                .join(CardReminderOccurrence, CardReminderOccurrence.id == UserNotification.reminder_occurrence_id)
                .where(
                    CardReminderWebPushDelivery.subscription_id == receipt.subscription_id,
                    CardReminderWebPushDelivery.endpoint_digest == receipt.endpoint_digest,
                    CardReminderOccurrence.rule_id == rule.id,
                    or_(
                        UserNotification.created_at > notice.created_at,
                        and_(UserNotification.created_at == notice.created_at, UserNotification.id > notice.id),
                    ),
                )
                .limit(1)
            ).first()
            if newer is not None:
                return None
        if not urgent and policy.quiet_hours is not None:
            resume_at = quiet_hours_end(policy.quiet_hours, now)
            if resume_at is not None:
                return resume_at
        preview = card.title[:120] if policy.show_preview else None
        return (
            device.encrypted_subscription,
            f"/board/{project.get_uid()}/{card.get_uid()}",
            receipt.attempts,
            urgent,
            preview,
            notice.get_uid(),
        )


def _finish(receipt_id: int, state: str, reason: str, next_attempt_at: datetime) -> None:
    with DbSession.atomic() as db:
        receipt = db.exec(
            SqlBuilder.select.table(CardReminderWebPushDelivery)
            .where(
                CardReminderWebPushDelivery.id == receipt_id,
                CardReminderWebPushDelivery.state == "sending",
            )
            .with_for_update()
        ).first()
        if receipt is None:
            return
        receipt.state = state
        receipt.last_result = reason
        receipt.next_attempt_at = next_attempt_at
        if reason == "quiet_hours":
            receipt.attempts -= 1
        db.update(receipt)
        if reason in {"http_404", "http_410"}:
            db.exec(
                update(UserWebPushSubscription)
                .where(
                    UserWebPushSubscription.id == receipt.subscription_id,
                    UserWebPushSubscription.endpoint_digest == receipt.endpoint_digest,
                )
                .values(endpoint_digest=None, encrypted_subscription=None)
            )


def run_reminder_web_push(service, *, limit: int = 10) -> None:
    if not browser_push_public_key():
        return
    for _ in range(limit):
        now = SafeDateTime.now()
        receipt_id = _claim(now)
        if receipt_id is None:
            break
        try:
            destination = _destination(service, receipt_id, now)
            if destination is None:
                _finish(receipt_id, "skipped", "recipient_or_source_changed", now)
                continue
            if destination == "urgent_blocked":
                _finish(receipt_id, "skipped", destination, now)
                continue
            if isinstance(destination, datetime):
                _finish(receipt_id, "pending", "quiet_hours", destination)
                continue
            encrypted, path, attempt, urgent, preview, event_uid = destination
            subscription = WebPushRegistration.model_validate_json(Encryptor.decrypt(encrypted, Env.COMMON_SECRET_KEY))
        except Exception:
            _finish(receipt_id, "rejected", "invalid_destination", now)
            continue
        try:
            status, retry_after = send_reminder_push(
                subscription, path, urgent=urgent, preview=preview, event_uid=event_uid
            )
        except Exception:
            _finish(receipt_id, "unknown", "provider_outcome_unknown", now)
            continue
        if 200 <= status < 300:
            _finish(receipt_id, "delivered", "provider_accepted", now)
        elif status in (429, 503) and attempt < 3:
            delay = (
                min(600, max(60, int(retry_after)))
                if retry_after and retry_after.isdecimal() and len(retry_after) < 6
                else 60
            )
            _finish(receipt_id, "pending", f"http_{status}", now + timedelta(seconds=delay))
        else:
            _finish(receipt_id, "rejected", f"http_{status}", now)
