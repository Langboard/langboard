"""Current card visibility for realtime delivery and collaboration admission."""

from typing import Literal
from ...core.db import DbSession, SqlBuilder
from ...core.security.CollaborationChannel import CollaborationChannel
from ...core.types import SnowflakeID
from ..models import Card, ProjectRole, User
from ..models.ProjectRole import ProjectRoleAction
from ...security import RoleFinder, RoleSecurity
from .CardVisibilityPolicy import CardVisibility


def can_receive_card_event(service, user: User, card_uid: str, operation: Literal["read", "remove", "edit"] = "read"):
    if not isinstance(user, User) or user.deleted_at or not user.activated_at:
        return False
    try:
        card_id = SnowflakeID.from_short_code(card_uid)
        if operation in ("read", "edit"):
            resolved = service.card.resolve_readable_card(None, card_id, user, CollaborationChannel.HumanUI)
            if resolved is None:
                return False
            return operation == "read" or RoleSecurity(ProjectRole).is_authorized(
                user.id, {"project_uid": resolved[0].get_uid()},
                [ProjectRoleAction.CardUpdate.value], RoleFinder.project,
            )
        if operation != "remove":
            return False
        # Deleted content is never readable; only a scrubbed removal is admitted.
        with DbSession.use(readonly=False) as db:
            card = db.exec(SqlBuilder.select.table(Card, with_deleted=True).where(Card.id == card_id)).first()
        if card is None or card.deleted_at is None:
            return False
        resolved = service.card.resolve_visibility_context(card.project_id, user, CollaborationChannel.HumanUI)
        return resolved is not None and resolved[1].can_read_card(
            CardVisibility(card.visibility), owner_user_id=card.owner_user_id,
        )
    except (TypeError, ValueError):
        return False
