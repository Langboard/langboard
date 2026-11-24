"""Current audience authorization shared by rule editing and reminder delivery."""

from ...core.db import DbSession, SqlBuilder
from ...core.security.CollaborationChannel import CollaborationChannel
from ..models import Card, User


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
