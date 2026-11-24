"""Current audience authorization shared by rule editing and reminder delivery."""

from ...core.db import DbSession, SqlBuilder
from ...core.security.CollaborationChannel import CollaborationChannel
from ...core.types import SnowflakeID
from ..models import Card, CardAssignedUser, CardReminderRule, User, UserCardNotificationPreference


def readable_reminder_recipients(db: DbSession, card_service, card: Card, ids: set[int]) -> list[User]:
    if not ids:
        return []
    candidates = db.exec(
        SqlBuilder.select.table(User).where(User.id.in_(ids), User.activated_at.is_not(None), User.deleted_at.is_(None))
    ).all()
    recipients = []
    for recipient in candidates:
        context = card_service.resolve_work_visibility_contexts(
            recipient, CollaborationChannel.HumanUI, project_ids={int(card.project_id)}
        ).get(int(card.project_id))
        if context and context.can_read_card(card.visibility, owner_user_id=card.owner_user_id):
            recipients.append(recipient)
    return recipients


def current_reminder_recipients(db: DbSession, card_service, card: Card, rule: CardReminderRule) -> list[User]:
    if rule.is_shared:
        ids = set(
            db.exec(SqlBuilder.select.column(CardAssignedUser.user_id).where(CardAssignedUser.card_id == card.id)).all()
        )
        preferences = db.exec(
            SqlBuilder.select.table(UserCardNotificationPreference).where(
                UserCardNotificationPreference.card_id == card.id
            )
        ).all()
        participants = {"creator", "commenter", "mentioned"}
        ids.update(preference.user_id for preference in preferences if participants.intersection(preference.reasons))
        overrides = rule.recipient_overrides or {}
        ids.update(SnowflakeID.from_short_code(uid) for uid in overrides.get("include", []))
        ids.difference_update(SnowflakeID.from_short_code(uid) for uid in overrides.get("exclude", []))
    else:
        ids = {rule.owner_id}
    return readable_reminder_recipients(db, card_service, card, ids)
