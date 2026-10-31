"""Current-state duplicate decisions for native card uploads.

Object keys are immutable. Historical object provenance resolves directly to the
stable live attachment; authorization always follows its current source card.
"""

from hashlib import sha256
from hmac import compare_digest
from hmac import new as hmac_new
from json import dumps
from pathlib import Path
from tempfile import SpooledTemporaryFile
from uuid import UUID, uuid4
from sqlalchemy import or_, update
from ...core.db import DbSession, SqlBuilder
from ...core.db.DbEngine import DbEngine
from ...core.security.CollaborationChannel import CollaborationChannel
from ...core.storage import Storage, StorageName
from ...core.utils.Converter import convert_python_data
from ...Env import Env
from ...helpers import InfraHelper
from ...tasks.activities.UserActivityTask import record_project_activity
from ...tasks.activities.utils import ActivityHistoryHelper, ActivityTaskHelper
from ..models import (
    Card,
    CardAttachment,
    Project,
    ProjectActivity,
    ProjectAssignedUser,
    ProjectColumn,
    ProjectRole,
    User,
)
from ..models.ProjectActivity import ProjectActivityType
from ..models.ProjectRole import ProjectRoleAction
from .AppGovernance import lock_policy_mutation
from .CardAppMutation import require_card_app_mutation
from .CardCrossBoardOperations import _grant


_CHUNK_SIZE = 64 * 1024


def _fingerprint(upload):
    filename = upload.filename
    if not filename or filename in {".", ".."} or "/" in filename or "\\" in filename:
        raise ValueError("An attachment filename without path components is required")
    digest = sha256()
    size = 0
    upload.file.seek(0)
    try:
        while chunk := upload.file.read(_CHUNK_SIZE):
            size += len(chunk)
            if size > Env.MAX_FILE_SIZE_MB * 1024 * 1024:
                raise ValueError("Attachment exceeds the configured upload limit")
            digest.update(chunk)
    finally:
        upload.file.seek(0)
    return filename, digest.hexdigest(), size


class _LimitedDestination:
    """Bound native download writes before a legacy source can consume excess disk."""

    def __init__(self, file):
        self.file = file

    def write(self, chunk):
        if self.file.tell() + len(chunk) > Env.MAX_FILE_SIZE_MB * 1024 * 1024:
            raise ValueError("Legacy attachment exceeds the configured upload limit")
        return self.file.write(chunk)

    def __getattr__(self, name):
        return getattr(self.file, name)


def _legacy_hash(file):
    with SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b") as spool:
        if not Storage.download_file(file, _LimitedDestination(spool)):
            raise ValueError("Legacy attachment cannot be verified; replacement cancelled")
        spool.seek(0)
        digest = sha256()
        while chunk := spool.read(_CHUNK_SIZE):
            digest.update(chunk)
        return digest.hexdigest()


def _key(file):
    return file.storage_type, file.storage_name, file.filename


def _attachment_response(db, attachment, actor):
    owner = (
        actor
        if attachment.user_id == actor.id
        else db.exec(SqlBuilder.select.table(User, with_deleted=True).where(User.id == attachment.user_id)).first()
    )
    return {**attachment.api_response(), "user": owner.api_response() if owner is not None else None}


def upload_attachment(
    service,
    user,
    project,
    card,
    upload,
    *,
    channel=None,
    duplicate_action=None,
    confirmation_token=None,
    replacement_uid=None,
    request_uid=None,
):
    if not isinstance(user, User):
        return None
    if DbSession.has_active_transaction():
        raise ValueError("Attachment upload must own its storage transaction")
    if duplicate_action not in {None, "add", "replace", "cancel"}:
        raise ValueError("Choose add, replace, or cancel")
    request_uid = request_uid or str(uuid4())
    try:
        request_uid = str(UUID(request_uid))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("A valid upload request UUID is required") from None
    filename, content_hash, size = _fingerprint(upload)
    channel = channel or CollaborationChannel.Api
    identity = sha256(
        dumps(
            [
                str(user.id),
                str(InfraHelper.convert_id(project)),
                str(InfraHelper.convert_id(card)),
                filename,
                content_hash,
                size,
                duplicate_action,
                replacement_uid,
            ]
        ).encode()
    ).hexdigest()
    stored = None
    committed = False
    try:
        with DbSession.atomic() as db:
            lock_policy_mutation(db)
            # Reserve SQLite's writer before decision reads; PostgreSQL follows native
            # governance order: policy -> current actor -> project -> card.
            if DbEngine.get_main_engine().dialect.name == "sqlite":
                db.exec(update(Card).where(Card.id == InfraHelper.convert_id(card)).values(id=Card.id))
            current_user = db.exec(SqlBuilder.select.table(User).where(User.id == user.id).with_for_update()).first()
            current_project = db.exec(
                SqlBuilder.select.table(Project).where(Project.id == InfraHelper.convert_id(project)).with_for_update()
            ).first()
            current_card = db.exec(
                SqlBuilder.select.table(Card).where(Card.id == InfraHelper.convert_id(card)).with_for_update()
            ).first()
            # Keep current account, membership and grant decisions stable until commit.
            if current_project is not None:
                for model in (ProjectAssignedUser, ProjectRole):
                    db.exec(
                        SqlBuilder.select.table(model)
                        .where(model.project_id == current_project.id, model.user_id == user.id)
                        .with_for_update()
                    ).all()
            if current_user is None:
                return None
            user = current_user
            if (
                current_project is None
                or current_card is None
                or current_card.project_id != current_project.id
                or current_card.is_linked_resource
                or not _grant(db, user, current_project, ProjectRoleAction.Read)
            ):
                return None
            from .factory.CardService import CardService

            cards = service._get_service(CardService)
            if cards.resolve_readable_card(current_project, current_card, user, channel) is None:
                return None
            column = db.exec(
                SqlBuilder.select.table(ProjectColumn)
                .where(
                    ProjectColumn.id == current_card.project_column_id,
                    ProjectColumn.project_id == current_project.id,
                )
                .with_for_update()
            ).first()
            if column is None:
                return None
            require_card_app_mutation(db, user, current_card, current_project)
            receipts = db.exec(
                SqlBuilder.select.table(ProjectActivity)
                .where(
                    ProjectActivity.project_id == current_project.id,
                    ProjectActivity.card_id == current_card.id,
                    ProjectActivity.user_id == user.id,
                    ProjectActivity.activity_type == ProjectActivityType.CardAttachmentUploaded,
                    ProjectActivity.activity_history["upload_request_uid"].as_string() == request_uid,
                )
                .limit(1)
            ).all()
            if receipts:
                receipt = receipts[0].activity_history
                if receipt.get("upload_identity") != identity:
                    raise ValueError("Upload request UUID already belongs to another operation")
                result = db.exec(
                    SqlBuilder.select.table(CardAttachment).where(
                        CardAttachment.id == InfraHelper.convert_id(receipt["attachment_uid"]),
                        CardAttachment.card_id == current_card.id,
                    )
                ).first()
                if result is None:
                    return None
                return {**_attachment_response(db, result, user), "replayed": True, "request_uid": request_uid}
            if duplicate_action == "cancel":
                return {"cancelled": True, "request_uid": request_uid}
            conflicts = db.exec(
                SqlBuilder.select.table(CardAttachment)
                .where(
                    CardAttachment.card_id == current_card.id,
                    or_(CardAttachment.filename == filename, CardAttachment.content_sha256 == content_hash),
                )
                .order_by(CardAttachment.id)
                .with_for_update()
                .limit(101)
            ).all()
            if len(conflicts) > 100:
                raise ValueError("Too many matching attachments; resolve existing duplicates before uploading")
            state = {
                "request_uid": request_uid,
                "identity": [str(user.id), str(current_project.id), str(current_card.id)],
                "filename": filename,
                "content_hash": content_hash,
                "size": size,
                "conflicts": [row.model_dump(mode="json") for row in conflicts],
                "visibility": [current_card.visibility, str(current_card.owner_user_id)],
            }
            token = hmac_new(Env.COMMON_SECRET_KEY.encode(), dumps(state, sort_keys=True).encode(), sha256).hexdigest()
            target = next((row for row in conflicts if row.get_uid() == replacement_uid), None)
            can_update = _grant(db, user, current_project, ProjectRoleAction.CardUpdate)

            def can_replace(row):
                return row.user_id == user.id or user.is_admin or can_update

            confirmed = bool(confirmation_token and compare_digest(token, confirmation_token))
            if conflicts and (
                not confirmed
                or duplicate_action not in {"add", "replace"}
                or (duplicate_action == "replace" and target is None)
            ):
                return {
                    "confirmation_required": True,
                    "confirmation_token": token,
                    "request_uid": request_uid,
                    "conflicts": [
                        {
                            "uid": row.get_uid(),
                            "name": row.filename,
                            "same_name": row.filename == filename,
                            "same_content": row.content_sha256 == content_hash,
                            "can_replace": can_replace(row),
                        }
                        for row in conflicts
                    ],
                }
            # A stale decision may never silently become a new upload after its conflict disappeared.
            if (
                not conflicts
                and (duplicate_action is not None or confirmation_token or replacement_uid)
                and not (duplicate_action == "add" and confirmed)
            ):
                return {
                    "confirmation_required": True,
                    "confirmation_token": token,
                    "request_uid": request_uid,
                    "conflicts": [],
                }
            if duplicate_action == "replace" and not can_replace(target):
                raise PermissionError("Attachment replacement requires ownership or CardUpdate")
            before = target.api_response() if duplicate_action == "replace" else None
            target_hash = target.content_sha256 if target is not None else None
            if duplicate_action == "replace" and target_hash is None:
                # Only the explicitly selected, confirmed legacy object is read; no backfill scan.
                owner = service.resolve_file_owner(*_key(target.file))
                if owner is None or owner.id != target.id:
                    raise ValueError("Attachment provenance is missing or ambiguous")
                target_hash = _legacy_hash(target.file)
            same_content = duplicate_action == "replace" and target is not None and target_hash == content_hash
            if not same_content:
                stored = Storage.upload_named(
                    upload.file, StorageName.CardAttachment, "ref_" + uuid4().hex + Path(filename).suffix, filename
                )
                if stored is None:
                    raise RuntimeError("Attachment storage unavailable")
                if service.resolve_file_owner(*_key(stored)) is not None:
                    # A broken provider must never allow cleanup to delete another attachment.
                    stored = None
                    raise RuntimeError("Attachment storage did not allocate an independent object")
            if duplicate_action == "replace":
                result = replace_attachment(
                    db, service, current_card, target, filename, content_hash, stored, same_content=same_content
                )
            else:
                result = service.create(
                    user,
                    current_project,
                    current_card,
                    stored,
                    dispatch_effects=False,
                    content_sha256=content_hash,
                )
                if result is None:
                    raise RuntimeError("Attachment creation failed")
            helper = ActivityTaskHelper(ProjectActivity)
            history = {
                **helper.create_project_default_history(current_project, card=current_card),
                "attachment": ActivityHistoryHelper.create_card_attachment_history(result),
                "attachment_uid": result.get_uid(),
                "upload_request_uid": request_uid,
                "upload_identity": identity,
                "upload_action": duplicate_action or "create",
            }
            if before:
                history["changes"] = convert_python_data(
                    {"before": before, "after": result.api_response()}, recursive=True
                )
            activity = helper.record(
                user,
                history,
                **helper.get_card_activity_params(
                    ProjectActivityType.CardAttachmentUploaded, current_project, current_card
                ),
            )
            db.after_commit(lambda: record_project_activity(user, activity))
            service.dispatch_created(
                user,
                current_project,
                current_card,
                result,
                include_activity=False,
                include_document_processing=not same_content,
            )
            response = {**_attachment_response(db, result, user), "request_uid": request_uid}
        committed = True
        return response
    finally:
        if stored is not None and not committed:
            Storage.delete(stored)


def replace_attachment(db, service, current_card, target, filename, content_hash, stored, *, same_content):
    """Replace a locked, authorized native attachment; caller owns storage rollback."""
    from .factory.DoclingMetadataService import DoclingMetadataService

    # Reject ambiguous original provenance before changing either bytes or pointers.
    owner = service.resolve_file_owner(*_key(target.file))
    if owner is None or owner.id != target.id or target.replaced_by_id is not None:
        raise ValueError("Attachment provenance is missing or ambiguous")
    if not same_content:
        old_file = target.file
        historical = CardAttachment(
            user_id=target.user_id,
            card_id=target.card_id,
            filename=target.filename,
            file=old_file,
            content_sha256=target.content_sha256,
            replaced_by_id=target.id,
            order=target.order,
        )
        db.insert(historical)
        db.delete(historical)
        service._get_service(DoclingMetadataService).supersede_document(
            current_card, target, replacement=stored, filename=filename, content_hash=content_hash,
        )
        target.file = stored
        target.filename = filename
        # Historical native embed URLs retain their own logical reference.
    target.content_sha256 = content_hash
    db.update(target)
    service._mark_card_changed_for_unread(current_card, "attachment", target.id)
    return target
