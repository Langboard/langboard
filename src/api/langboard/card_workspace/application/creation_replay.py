"""Keep a creation key and its card in the same native transaction."""

from collections.abc import Callable
from hashlib import sha256
from json import dumps
from uuid import UUID
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.domain.models import Bot, User
from langboard_shared.domain.models.CardCreationReceipt import CardCreationReceipt
from langboard_shared.domain.services import DomainService
from sqlalchemy.exc import IntegrityError
from ..domain import CardInputRejected


def create_once(
    service: DomainService,
    actor: User | Bot,
    project_uid: str,
    request_uuid: str,
    intent: list,
    create: Callable[[], tuple],
) -> dict:
    try:
        key = str(UUID(str(request_uuid)))
    except ValueError as exc:
        raise CardInputRejected("Invalid card creation request UUID") from exc
    digest = sha256(dumps([project_uid, *intent], ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    reservation = CardCreationReceipt(
        actor_id=actor.id,
        actor_type="user" if isinstance(actor, User) else "bot",
        request_uuid=key,
        payload_digest=digest,
    )
    with DbSession.atomic() as db, db.savepoint():
        try:
            with db.savepoint():
                db.insert(reservation)
        except IntegrityError:
            previous = db.exec(
                SqlBuilder.select.table(CardCreationReceipt).where(
                    CardCreationReceipt.actor_type == reservation.actor_type,
                    CardCreationReceipt.actor_id == reservation.actor_id,
                    CardCreationReceipt.request_uuid == key,
                )
            ).first()
            if previous is None:
                raise
            if previous.payload_digest != digest:
                raise CardInputRejected("Card creation request UUID already used with different input")
            resolved = service.card.resolve_readable_card(
                project_uid, previous.card_id, actor, CollaborationChannel.Mcp, metadata_only=True
            )
            if resolved is None:
                raise CardInputRejected("Original card is unavailable in this project")
            project, card, _ = resolved
            return {"uid": card.get_uid(), "project_uid": project.get_uid(), "replayed": True}
        card, result = create()
        reservation.card_id = card.id
        db.update(reservation)
        return result
