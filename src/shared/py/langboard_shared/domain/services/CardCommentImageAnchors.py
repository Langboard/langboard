"""Version-bound image comments in the existing native comment transaction."""

import io
import warnings
from hashlib import sha256
from json import dumps
from uuid import UUID
from sqlalchemy import update
from ...core.db import DbSession, EditorContentModel, SqlBuilder
from ...core.db.DbEngine import DbEngine
from ...core.security.CollaborationChannel import CollaborationChannel
from ...core.storage import Storage
from ...helpers import InfraHelper
from ..models import Card, CardAttachment, CardComment, Project, ProjectAssignedUser, ProjectRole, User
from ..models.ProjectRole import ProjectRoleAction
from .AppGovernance import lock_policy_mutation
from .CardCrossBoardOperations import _grant


# Match native MCP image transport and image-generation pixel protections.
MAX_IMAGE_BYTES = 25 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_777_216


class BoundedImageBuffer(io.BytesIO):
    def __init__(self, limit):
        super().__init__()
        self.limit = limit
        self.exceeded = False

    def write(self, data):
        if self.tell() + len(data) > self.limit:
            self.exceeded = True
            raise OSError("Image byte limit")
        return super().write(data)


def image_metadata(data):
    """Verify supported raster headers within byte/pixel limits, without raster decoding."""
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("byte_limit")
    try:
        # Pillow is an existing optional native decoder. Missing support fails closed.
        from PIL import Image

        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data), formats=("PNG", "JPEG", "GIF", "WEBP")) as image:
                width, height = image.size
                if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
                    raise ValueError("pixel_limit")
                # Use header metadata only; PNG.getexif() would load raster pixels.
                # Browser natural dimensions follow EXIF orientation.
                if Image.Image.getexif(image).get(274) in {5, 6, 7, 8}:
                    width, height = height, width
                mime = Image.MIME[image.format]
                image.verify()
                return mime, width, height
    except (ImportError, OSError, SyntaxError, Warning) as exc:
        raise ValueError("unsupported_type") from exc
    except Image.DecompressionBombError as exc:
        raise ValueError("pixel_limit") from exc


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
        # Keep project policy stable while allowing a concurrent card edit's FK KEY SHARE.
        project = db.exec(
            SqlBuilder.select.table(Project).where(Project.id == project_id).with_for_update(key_share=True)
        ).first()
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
            .execution_options(populate_existing=True)
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
            .execution_options(populate_existing=True)
        ).first()
        if (
            attachment is None
            or attachment.replaced_by_id is not None
            or attachment.image_revision != anchor.source.revision
        ):
            return None
        if receipt is not None:
            return {"comment_uid": receipt.get_uid(), "replayed": True}
        stored = (attachment.file.storage_type, attachment.file.storage_name, attachment.file.filename)
        with BoundedImageBuffer(MAX_IMAGE_BYTES) as buffer:
            try:
                found = Storage.download_file(attachment.file, buffer)
                if not found or buffer.exceeded:
                    return None
                _, width, height = image_metadata(buffer.getvalue())
            except (OSError, ValueError):
                return None
        if (width, height) != (anchor.image_width, anchor.image_height):
            return None
        # Storage I/O can yield control. Recheck source and authority before persistence.
        attachment = db.exec(
            SqlBuilder.select.table(CardAttachment)
            .where(
                CardAttachment.id == InfraHelper.convert_id(anchor.source.attachment_uid),
                CardAttachment.card_id == card.id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        ).first()
        if (
            attachment is None
            or attachment.replaced_by_id is not None
            or attachment.image_revision != anchor.source.revision
            or (attachment.file.storage_type, attachment.file.storage_name, attachment.file.filename) != stored
            or not _grant(db, actor, project, ProjectRoleAction.Read)
            or cards.resolve_readable_card(project, card, actor, channel) is None
        ):
            return None
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
