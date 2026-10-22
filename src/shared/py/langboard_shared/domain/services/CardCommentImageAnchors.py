"""Version-bound image comments in the existing native comment transaction."""

from hashlib import sha256
from json import dumps
from uuid import UUID
from sqlalchemy import update
from ...core.db import DbSession, EditorContentModel, SqlBuilder
from ...core.db.DbEngine import DbEngine
from ...core.security.CollaborationChannel import CollaborationChannel
from ...helpers import InfraHelper
from ..models import Card, CardAttachment, CardComment, Project, ProjectAssignedUser, ProjectRole, User
from ..models.ProjectRole import ProjectRoleAction
from .AppGovernance import lock_policy_mutation
from .CardCrossBoardOperations import _grant


def create_image_comment(
    service,
    actor,
    project,
    card,
    content,
    anchor,
    *,
    request_uid,
    dispatch_effects=True,
    channel=CollaborationChannel.Api,
):
    """Recheck authority and immutable source before accepting a durable request receipt."""
    if not isinstance(actor, User):
        return None
    if not isinstance(request_uid, str):
        raise ValueError("Image comments require a caller request UUID")
    try:
        request_uid = str(UUID(request_uid))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("Image comments require a valid request UUID") from None
    if isinstance(content, dict):
        content = EditorContentModel(**content)
    project_id, card_id = InfraHelper.convert_id(project), InfraHelper.convert_id(card)
    fingerprint = sha256(
        dumps(
            [str(actor.id), str(project_id), str(card_id), content.model_dump(mode="json"), anchor.model_dump()],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode()
    ).hexdigest()
    from .factory.CardService import CardService

    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        # SQLite needs a writer reservation before decision reads. Other engines
        # follow the native policy -> actor -> project -> card authority order.
        if DbEngine.get_main_engine().dialect.name == "sqlite":
            db.exec(update(Card).where(Card.id == card_id).values(id=Card.id))
        actor = db.exec(SqlBuilder.select.table(User).where(User.id == actor.id).with_for_update()).first()
        project = db.exec(SqlBuilder.select.table(Project).where(Project.id == project_id).with_for_update()).first()
        card = db.exec(SqlBuilder.select.table(Card).where(Card.id == card_id).with_for_update()).first()
        if actor is None or project is None or card is None or card.project_id != project.id or card.is_linked_resource:
            return None
        for model in (ProjectAssignedUser, ProjectRole):
            db.exec(
                SqlBuilder.select.table(model)
                .where(model.project_id == project.id, model.user_id == actor.id)
                .with_for_update()
            ).all()
        cards = service._get_service(CardService)
        if not _grant(db, actor, project, ProjectRoleAction.Read):
            return None
        if cards.resolve_readable_card(project, card, actor, channel) is None:
            return None
        receipt = db.exec(
            SqlBuilder.select.table(CardComment, with_deleted=True)
            .where(CardComment.image_request_uid == request_uid)
            .with_for_update()
        ).first()
        if receipt is not None:
            if receipt.image_request_fingerprint != fingerprint:
                raise ValueError("Image comment request UUID already belongs to another payload")
            if receipt.deleted_at is not None:
                return None
        attachment = db.exec(
            SqlBuilder.select.table(CardAttachment)
            .where(
                CardAttachment.id == InfraHelper.convert_id(anchor.source.attachment_uid),
                CardAttachment.card_id == card.id,
            )
            .with_for_update()
        ).first()
        if (
            attachment is None
            or attachment.replaced_by_id is not None
            or attachment.image_revision != anchor.source.revision
        ):
            return None
        if receipt is not None:
            return {"comment_uid": receipt.get_uid(), "replayed": True}
        comment = CardComment(
            card_id=card.id,
            user_id=actor.id,
            content=content,
            anchor=anchor.model_dump(),
            image_request_uid=request_uid,
            image_request_fingerprint=fingerprint,
        )
        db.insert(comment)
        cards.mark_card_changed(card, CardService.UNREAD_TARGET_COMMENT, comment.id)
        if dispatch_effects:
            service.dispatch_created(actor, project, card, comment)
        return {"comment_uid": comment.get_uid(), "replayed": False}
