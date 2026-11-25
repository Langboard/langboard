"""Admit web-push receipts into the existing reminder transaction."""

from ...core.db import DbSession, SqlBuilder
from ...core.types import SafeDateTime
from ..models.CardReminderWebPushDelivery import CardReminderWebPushDelivery
from ..models.User import User
from ..models.UserNotification import UserNotification
from ..models.UserWebPushSubscription import UserWebPushSubscription
from .WebPushSubscription import browser_push_public_key


def admit_web_push(notification: UserNotification, user: User) -> None:
    """Snapshot this recipient's current devices with the authoritative in-app event."""
    if browser_push_public_key() is None:
        return
    now = SafeDateTime.now()
    with DbSession.atomic() as db:
        devices = db.exec(
            SqlBuilder.select.table(UserWebPushSubscription).where(
                UserWebPushSubscription.user_id == user.id,
                UserWebPushSubscription.auth_version == user.auth_version,
                UserWebPushSubscription.session_expires_at > now,
                UserWebPushSubscription.signed_out.is_(False),
                UserWebPushSubscription.endpoint_digest.is_not(None),
            )
        ).all()
        for device in devices:
            db.insert(
                CardReminderWebPushDelivery(
                    notification_id=notification.id,
                    subscription_id=device.id,
                    endpoint_digest=device.endpoint_digest,
                    next_attempt_at=now,
                )
            )
