"""Native Cloud decisions with immutable objects and durable terminal receipts."""

from hashlib import sha256
from hmac import compare_digest
from hmac import new as hmac_new
from json import dumps, loads
from pathlib import Path
from uuid import UUID, uuid4
from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import defer
from ...core.db import DbSession, SqlBuilder
from ...core.db.DbEngine import DbEngine
from ...core.security.CollaborationChannel import CollaborationChannel
from ...core.storage import Storage, StorageName
from ...core.utils.Converter import convert_python_data
from ...Env import Env
from ...helpers import InfraHelper
from ..models import (
    Card,
    CardAttachment,
    Project,
    ProjectAssignedUser,
    ProjectAttachment,
    ProjectAttachmentUploadReceipt,
    ProjectRole,
    ProjectWiki,
    ProjectWikiAttachment,
    User,
)
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
    replacement_source=None,
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
    if replacement_source not in {None, "project", "card", "wiki"}:
        raise ValueError("Invalid attachment source")
    filename, content_hash, size = _fingerprint(upload)
    project_id = InfraHelper.convert_id(project)
    # Preserve identities of pre-source project receipts across the contract upgrade.
    identity = [
        str(user.id),
        str(project_id),
        filename,
        content_hash,
        size,
        folder_id,
        duplicate_action,
        replacement_uid,
    ]
    if replacement_source in {"card", "wiki"}:
        identity.append(replacement_source)
    fingerprint = sha256(dumps(identity).encode()).hexdigest()
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
                    if replacement_source in {None, "project"}:
                        previous = db.exec(
                            SqlBuilder.select.table(ProjectAttachment, with_deleted=True).where(
                                ProjectAttachment.id == InfraHelper.convert_id(replacement_uid),
                                ProjectAttachment.project_id == board.id,
                            )
                        ).first()
                        allowed = previous is not None and replacement_allowed(authority, previous)
                    else:
                        original = _selected_source(
                            db, service, authority, channel, replacement_source, replacement_uid
                        )
                        allowed = original is not None and original[3]
                        if allowed:
                            previous_source = receipt.result.get("source", {})
                            source_key = "card_uid" if replacement_source == "card" else "wiki_uid"
                            allowed = previous_source.get(source_key) == original[2].get(source_key)
                    if not allowed:
                        raise PermissionError("Attachment replacement requires current authority")
                return {**receipt.result, "replayed": True}
            if duplicate_action == "cancel":
                response = {"cancelled": True, "request_uid": request_uid}
            else:
                conflicts = _conflicts(db, service, authority, channel, filename, content_hash)
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
                    [[kind, row.model_dump(mode="json"), source, allowed] for kind, row, source, allowed in conflicts],
                ]
                token = hmac_new(
                    Env.COMMON_SECRET_KEY.encode(), dumps(state, sort_keys=True).encode(), sha256
                ).hexdigest()
                confirmed = bool(confirmation_token and compare_digest(token, confirmation_token))
                selected = next(
                    (
                        item
                        for item in conflicts
                        if item[1].get_uid() == replacement_uid and item[0] == (replacement_source or "project")
                    ),
                    None,
                )
                target = selected[1] if selected else None
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
                                "can_replace": allowed,
                                "source": source,
                            }
                            for kind, row, source, allowed in conflicts
                        ],
                    }
                if duplicate_action == "replace" and not selected[3]:
                    raise PermissionError("Attachment replacement requires ownership or Update")
                same_content = (
                    target is not None and duplicate_action == "replace" and target.content_sha256 == content_hash
                )
                if duplicate_action == "replace" and selected[0] != "project":
                    from .CardAttachmentUploadOperations import _legacy_hash

                    if target.content_sha256 is None:
                        same_content = _legacy_hash(target.file) == content_hash
                    if not same_content:
                        storage_name = StorageName.CardAttachment if selected[0] == "card" else StorageName.Wiki
                        stored = Storage.upload_named(
                            upload.file, storage_name, "ref_" + uuid4().hex + Path(filename).suffix, filename
                        )
                        if stored is None:
                            raise RuntimeError("Attachment storage unavailable")
                        model = type(target)
                        if selected[0] == "wiki":
                            from .factory.ProjectWikiAttachmentService import wiki_file_identity

                            collision = db.exec(
                                SqlBuilder.select.table(model, with_deleted=True)
                                .where(*wiki_file_identity(stored))
                                .limit(1)
                            ).first()
                        else:
                            from .factory.CardAttachmentService import CardAttachmentService

                            collision = service._get_service(CardAttachmentService).resolve_file_owner(
                                stored.storage_type, stored.storage_name, stored.filename
                            )
                        if collision is not None:
                            stored = None
                            raise RuntimeError("Attachment storage did not allocate an independent object")
                    result = _replace_native(
                        db, service, actor, board, selected, stored, filename, content_hash, same_content
                    )
                    response = {
                        **result.api_response(),
                        "project_uid": board.get_uid(),
                        "folder_id": folder_id,
                        "source": selected[2],
                        "request_uid": request_uid,
                        "replayed": False,
                    }
                else:
                    if duplicate_action == "replace":
                        owner = service.resolve_file_owner(
                            target.file.storage_type, target.file.storage_name, target.file.filename
                        )
                        if owner is None or owner.id != target.id:
                            raise ValueError("Attachment provenance is missing or ambiguous")
                    if not same_content:
                        stored = Storage.upload_named(
                            upload.file,
                            StorageName.ProjectAttachment,
                            "ref_" + uuid4().hex + Path(filename).suffix,
                            filename,
                        )
                        if stored is None:
                            raise RuntimeError("Attachment storage unavailable")
                        # A provider collision must not let rollback erase someone else's bytes.
                        if _storage_key_exists(db, stored):
                            stored = None
                            raise RuntimeError("Attachment storage did not allocate an independent object")
                    if duplicate_action == "replace":
                        if not same_content:
                            from ...core.types import SafeDateTime
                            from .factory.ProjectDocumentService import ProjectDocumentService

                            # Keep exact provenance and bytes for existing native embed URLs.
                            db.insert(
                                ProjectAttachment(
                                    project_id=target.project_id,
                                    user_id=target.user_id,
                                    filename=target.filename,
                                    file=target.file,
                                    content_sha256=target.content_sha256,
                                    folder_id=target.folder_id,
                                    replaced_by_id=target.id,
                                    deleted_at=SafeDateTime.now(),
                                )
                            )
                            target.file = stored
                        target.filename = filename
                        target.content_sha256 = content_hash
                        target.folder_id = folder_id
                        db.update(target)
                        if not same_content:
                            service._get_service(ProjectDocumentService).supersede(db, target)
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
                    if not same_content:
                        from .factory.ProjectDocumentService import ProjectDocumentService

                        db.after_commit(
                            lambda: service._get_service(ProjectDocumentService).request(
                                actor, board, result, automatic=True, force=duplicate_action == "replace"
                            )
                        )
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


def _selected_source(db, service, authority, channel, kind, uid):
    """Only return metadata after current native source and exact locator authorization."""
    from ..models.ProjectRole import ProjectRoleAction
    from .CardCrossBoardOperations import _grant
    from .factory.CardAttachmentService import CardAttachmentService
    from .factory.CardService import CardService
    from .factory.ProjectWikiService import ProjectWikiService

    actor, board, role = authority
    model = {"project": ProjectAttachment, "card": CardAttachment, "wiki": ProjectWikiAttachment}[kind]
    row = db.exec(
        SqlBuilder.select.table(model)
        .where(model.id == InfraHelper.convert_id(uid))
        .with_for_update()
        .execution_options(populate_existing=True)
    ).first()
    if row is None or row.replaced_by_id is not None:
        return None
    expected_storage = {
        "project": StorageName.ProjectAttachment,
        "card": StorageName.CardAttachment,
        "wiki": StorageName.Wiki,
    }[kind]
    if row.file.storage_name != expected_storage.value:
        return None
    board_url = f"/board/{board.get_uid()}"
    source = {"kind": kind, "project_uid": board.get_uid(), "title": board.title, "url": board_url}
    if kind == "project":
        if row.project_id != board.id:
            return None
        native = service.resolve_readable_file(actor, row.file.storage_type, row.file.storage_name, row.file.filename)
        allowed = replacement_allowed(authority, row)
    elif kind == "card":
        parent = db.exec(
            SqlBuilder.select.table(Card)
            .where(Card.id == row.card_id, Card.project_id == board.id)
            .options(defer(Card.description, raiseload=True), defer(Card.ai_description, raiseload=True))
            .with_for_update()
            .execution_options(populate_existing=True)
        ).first()
        if parent is None or parent.is_linked_resource:
            return None
        cards = service._get_service(CardService)
        if cards.resolve_readable_card(board, parent, actor, channel, metadata_only=True) is None:
            return None
        native = service._get_service(CardAttachmentService).resolve_file_owner(
            row.file.storage_type, row.file.storage_name, row.file.filename
        )
        source.update(card_uid=parent.get_uid(), title=parent.title, url=f"{board_url}/{parent.get_uid()}")
        allowed = bool(
            row.user_id == actor.id or actor.is_admin or _grant(db, actor, board, ProjectRoleAction.CardUpdate)
        )
    else:
        from ..models import ProjectWikiAssignedUser

        parent = db.exec(
            SqlBuilder.select.table(ProjectWiki)
            .where(ProjectWiki.id == row.project_wiki_id, ProjectWiki.project_id == board.id)
            .options(defer(ProjectWiki.content, raiseload=True))
            .with_for_update()
            .execution_options(populate_existing=True)
        ).first()
        if parent is None:
            return None
        db.exec(
            SqlBuilder.select.table(ProjectWikiAssignedUser)
            .where(
                ProjectWikiAssignedUser.project_wiki_id == parent.id,
                ProjectWikiAssignedUser.user_id == actor.id,
            )
            .with_for_update()
        ).all()
        wikis = service._get_service(ProjectWikiService)
        native = wikis.resolve_readable_file(
            actor, row.file.storage_type, row.file.storage_name, row.file.filename, metadata_only=True
        )
        source.update(wiki_uid=parent.get_uid(), title=parent.title, url=f"{board_url}/wiki/{parent.get_uid()}")
        # Wiki upload is native read-scoped; replacing someone else's file additionally needs Update.
        allowed = bool(
            wikis.can_upload_attachment(actor, board, parent)
            and (
                row.user_id == actor.id
                or actor.is_admin
                or board.owner_id == actor.id
                or role
                and role.is_granted(ProjectRoleAction.Update)
            )
        )
    if native is None or native.id != row.id:
        return None
    # Native resolution can refresh identity-map objects; parent and locator must remain exact.
    if kind == "card" and native.card_id != parent.id or kind == "wiki" and native.project_wiki_id != parent.id:
        return None
    return kind, row, source, allowed


def _conflicts(db, service, authority, channel, filename, content_hash):
    from ..models import ProjectWikiAssignedUser
    from .CardVisibilityPolicy import card_visibility_scope
    from .factory.CardService import CardService

    actor, board, _ = authority
    context = service._get_service(CardService).resolve_visibility_context(board, actor, channel)
    card_scope = (
        and_(card_visibility_scope(context[1]), or_(Card.source_type.is_(None), Card.source_uid.is_(None)))
        if context
        else False
    )
    assigned = (
        select(ProjectWikiAssignedUser.id)
        .join(ProjectAssignedUser, ProjectAssignedUser.id == ProjectWikiAssignedUser.project_assigned_id)
        .where(
            ProjectWikiAssignedUser.project_wiki_id == ProjectWiki.id,
            ProjectWikiAssignedUser.user_id == actor.id,
            ProjectAssignedUser.project_id == board.id,
            ProjectAssignedUser.user_id == actor.id,
        )
        .exists()
    )
    wiki_scope = or_(ProjectWiki.is_public.is_(True), actor.is_admin, board.owner_id == actor.id, assigned)
    conflicts = []
    for kind, model, parent, relation in (
        ("project", ProjectAttachment, None, None),
        ("card", CardAttachment, Card, CardAttachment.card_id == Card.id),
        ("wiki", ProjectWikiAttachment, ProjectWiki, ProjectWikiAttachment.project_wiki_id == ProjectWiki.id),
    ):
        query = SqlBuilder.select.table(model).where(
            or_(model.filename == filename, model.content_sha256 == content_hash)
        )
        if parent is None:
            query = query.where(model.project_id == board.id)
        else:
            query = query.join(parent, relation).where(
                parent.project_id == board.id, parent.deleted_at.is_(None), card_scope if kind == "card" else wiki_scope
            )
        # Iterate matching metadata only; source authorization precedes all response projections.
        after_id = -1
        while True:
            batch = db.exec(query.where(model.id > after_id).order_by(model.id).limit(100)).all()
            if not batch:
                break
            for row in batch:
                candidate = _selected_source(db, service, authority, channel, kind, row.get_uid())
                if candidate is not None:
                    conflicts.append(candidate)
                    if len(conflicts) > 100:
                        return conflicts
            after_id = batch[-1].id
    return conflicts


def _replace_native(db, service, actor, board, selected, stored, filename, content_hash, same_content):
    kind, target, source, _ = selected
    if kind == "card":
        from .CardAttachmentUploadOperations import replace_attachment
        from .factory.CardAttachmentService import CardAttachmentService

        native = service._get_service(CardAttachmentService)
        card = db.exec(SqlBuilder.select.table(Card).where(Card.id == target.card_id)).first()
        result = replace_attachment(db, native, card, target, filename, content_hash, stored, same_content=same_content)
        native.dispatch_created(actor, board, card, result, include_document_processing=not same_content)
        return result
    from .factory.ProjectWikiAttachmentService import replace_attachment
    from .factory.WikiDocumentService import WikiDocumentService

    documents = service._get_service(WikiDocumentService)
    result = replace_attachment(db, documents, target, filename, content_hash, stored, same_content=same_content)
    if not same_content:
        db.after_commit(lambda: documents.request(actor, board, result, automatic=True, force=True))
    return result
