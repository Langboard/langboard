"""Native Cloud decisions with immutable objects and durable terminal receipts."""

from hashlib import sha256
from hmac import compare_digest
from hmac import new as hmac_new
from json import dumps, loads
from pathlib import Path
from uuid import UUID, uuid4
from sqlalchemy import or_, update
from ...core.db import DbSession, SqlBuilder
from ...core.db.DbEngine import DbEngine
from ...core.security.CollaborationChannel import CollaborationChannel
from ...core.storage import Storage, StorageName
from ...core.utils.Converter import convert_python_data
from ...Env import Env
from ...helpers import InfraHelper
from ..models import Project, ProjectAssignedUser, ProjectAttachment, ProjectAttachmentUploadReceipt, ProjectRole, User
from .AppGovernance import lock_policy_mutation
from .CardAttachmentUploadOperations import _fingerprint
from .factory.ProjectAttachmentService import current_authority, replacement_allowed


def upload_attachment(
    service,
    user,
    project,
    upload,
    *,
    channel=CollaborationChannel.Api,
    duplicate_action=None,
    confirmation_token=None,
    replacement_uid=None,
    request_uid=None,
    folder_id="inbox",
):
    if not isinstance(user, User):
        return None
    if channel not in set(CollaborationChannel):
        raise ValueError("A server-authenticated collaboration channel is required")
    if DbSession.has_active_transaction():
        raise ValueError("Attachment upload must own its storage transaction")
    if duplicate_action not in {None, "add", "replace", "cancel"}:
        raise ValueError("Choose add, replace, or cancel")
    if folder_id not in {"inbox", "documents", "media"}:
        raise ValueError("Invalid Cloud folder")
    try:
        request_uid = str(UUID(request_uid))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("A valid upload request UUID is required") from None
    filename, content_hash, size = _fingerprint(upload)
    project_id = InfraHelper.convert_id(project)
    fingerprint = sha256(
        dumps(
            [str(user.id), str(project_id), filename, content_hash, size, folder_id, duplicate_action, replacement_uid]
        ).encode()
    ).hexdigest()
    stored = None
    committed = False
    try:
        with DbSession.atomic() as db:
            lock_policy_mutation(db)
            if DbEngine.get_main_engine().dialect.name == "sqlite":
                db.exec(update(Project).where(Project.id == project_id).values(id=Project.id))
            db.exec(SqlBuilder.select.table(User).where(User.id == user.id).with_for_update()).first()
            db.exec(SqlBuilder.select.table(Project).where(Project.id == project_id).with_for_update()).first()
            for model in (ProjectAssignedUser, ProjectRole):
                db.exec(
                    SqlBuilder.select.table(model)
                    .where(model.project_id == project_id, model.user_id == user.id)
                    .with_for_update()
                ).all()
            authority = current_authority(db, user, project_id)
            if authority is None:
                return None
            actor, board, _ = authority
            receipt = db.exec(
                SqlBuilder.select.table(ProjectAttachmentUploadReceipt)
                .where(
                    ProjectAttachmentUploadReceipt.user_id == actor.id,
                    ProjectAttachmentUploadReceipt.project_id == board.id,
                    ProjectAttachmentUploadReceipt.request_uid == request_uid,
                )
                .with_for_update()
            ).first()
            if receipt is not None:
                if not compare_digest(receipt.fingerprint, fingerprint):
                    raise ValueError("Upload request UUID already belongs to another operation")
                # Current replacement authority is required even when replaying immutable results.
                if duplicate_action == "replace":
                    original = db.exec(
                        SqlBuilder.select.table(ProjectAttachment, with_deleted=True).where(
                            ProjectAttachment.id == InfraHelper.convert_id(replacement_uid),
                            ProjectAttachment.project_id == board.id,
                        )
                    ).first()
                    if original is None or not replacement_allowed(authority, original):
                        raise PermissionError("Attachment replacement requires current authority")
                return {**receipt.result, "replayed": True}
            if duplicate_action == "cancel":
                response = {"cancelled": True, "request_uid": request_uid}
            else:
                conflicts = db.exec(
                    SqlBuilder.select.table(ProjectAttachment)
                    .where(
                        ProjectAttachment.project_id == board.id,
                        or_(ProjectAttachment.filename == filename, ProjectAttachment.content_sha256 == content_hash),
                    )
                    .order_by(ProjectAttachment.id)
                    .with_for_update()
                    .limit(101)
                ).all()
                if len(conflicts) > 100:
                    raise ValueError("Too many matching attachments; resolve existing duplicates before uploading")
                state = [
                    str(actor.id),
                    str(board.id),
                    request_uid,
                    filename,
                    content_hash,
                    size,
                    folder_id,
                    [row.model_dump(mode="json") for row in conflicts],
                    [replacement_allowed(authority, row) for row in conflicts],
                ]
                token = hmac_new(
                    Env.COMMON_SECRET_KEY.encode(), dumps(state, sort_keys=True).encode(), sha256
                ).hexdigest()
                confirmed = bool(confirmation_token and compare_digest(token, confirmation_token))
                target = next((row for row in conflicts if row.get_uid() == replacement_uid), None)
                if (
                    conflicts
                    and (
                        not confirmed
                        or duplicate_action not in {"add", "replace"}
                        or duplicate_action == "replace"
                        and target is None
                    )
                ) or (
                    not conflicts
                    and (duplicate_action is not None or confirmation_token or replacement_uid)
                    and not (duplicate_action == "add" and confirmed)
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
                                "can_replace": replacement_allowed(authority, row),
                            }
                            for row in conflicts
                        ],
                    }
                if duplicate_action == "replace" and not replacement_allowed(authority, target):
                    raise PermissionError("Attachment replacement requires ownership or Update")
                same_content = (
                    target is not None and duplicate_action == "replace" and target.content_sha256 == content_hash
                )
                if duplicate_action == "replace":
                    owner = service.resolve_file_owner(
                        target.file.storage_type, target.file.storage_name, target.file.filename
                    )
                    if owner is None or owner.id != target.id:
                        raise ValueError("Attachment provenance is missing or ambiguous")
                if not same_content:
                    stored = Storage.upload_named(
                        upload.file, StorageName.ProjectAttachment, uuid4().hex + Path(filename).suffix, filename
                    )
                    if stored is None:
                        raise RuntimeError("Attachment storage unavailable")
                    # A provider collision must not let rollback erase someone else's bytes.
                    if _storage_key_exists(db, stored):
                        stored = None
                        raise RuntimeError("Attachment storage did not allocate an independent object")
                if duplicate_action == "replace":
                    if not same_content:
                        old_file = target.file
                        target.file = stored
                        db.after_commit(lambda: Storage.delete(old_file))
                    target.filename = filename
                    target.content_sha256 = content_hash
                    target.folder_id = folder_id
                    db.update(target)
                    result = target
                else:
                    result = ProjectAttachment(
                        project_id=board.id,
                        user_id=actor.id,
                        filename=filename,
                        file=stored,
                        content_sha256=content_hash,
                        folder_id=folder_id,
                    )
                    db.insert(result)
                response = {**result.api_response(), "request_uid": request_uid, "replayed": False}
            # Store JSON-safe final result, never resolve a later mutable asset on retry.
            response = loads(dumps(convert_python_data(response, recursive=True)))
            db.insert(
                ProjectAttachmentUploadReceipt(
                    user_id=actor.id,
                    project_id=board.id,
                    request_uid=request_uid,
                    fingerprint=fingerprint,
                    result=response,
                )
            )
        committed = True
        return response
    finally:
        if stored is not None and not committed:
            Storage.delete(stored)


def _storage_key_exists(db, file):
    from .factory.ProjectAttachmentService import file_identity

    return (
        db.exec(
            SqlBuilder.select.table(ProjectAttachment, with_deleted=True)
            .where(*file_identity(file.storage_type, file.storage_name, file.filename))
            .limit(1)
        ).first()
        is not None
    )
