"""Independent native card copies, with current authority and explicit audience consent."""

from copy import deepcopy
from hashlib import sha256
from hmac import compare_digest
from hmac import new as hmac_new
from json import dumps
from tempfile import TemporaryFile
from uuid import UUID, uuid4
from sqlalchemy import update
from ...core.db import DbSession, EditorContentModel, SqlBuilder
from ...core.security.CollaborationChannel import CollaborationChannel
from ...core.storage import Storage, StorageName
from ...Env import Env
from ...helpers import InfraHelper
from ...publishers import CardPublisher
from ...tasks.activities import CardActivityTask
from ..models import (
    Card,
    CardAssignedProjectLabel,
    CardAssignedUser,
    CardAttachment,
    CardContentBlock,
    Checkitem,
    Checklist,
    Project,
    ProjectActivity,
    ProjectAssignedUser,
    ProjectColumn,
    ProjectLabel,
    ProjectRole,
    User,
)
from ..models.ProjectActivity import ProjectActivityType
from ..models.ProjectRole import ProjectRoleAction
from .CardCrossBoardOperations import _grant, readable_original
from .CardVisibilityPolicy import CardVisibilityContext


def _rows(db, model, *conditions):
    return db.exec(
        SqlBuilder.select.table(model).where(*conditions).order_by(model.id).execution_options(populate_existing=True)
    ).all()


def _readers(project, source, users, memberships, roles, internal_access):
    readers = set()
    for user in users:
        member = user.id == project.owner_id or user.id in memberships
        role = roles.get((project.id, user.id))
        granted = user.id == project.owner_id or user.is_admin or (role and role.is_granted(ProjectRoleAction.Read))
        if not member or not granted:
            continue
        context = CardVisibilityContext(
            CollaborationChannel.HumanUI, True, True, internal_access[user.id], actor_user_id=int(user.id)
        )
        if context.can_read_card(source.visibility, owner_user_id=source.owner_user_id):
            readers.add(user.id)
    return readers


def _payload(card, assigned, labels):
    return card.board_api_response(0, assigned, [], labels)


def clone_card(
    service,
    actor,
    source_project,
    source_card,
    destination_project,
    destination_column,
    *,
    request_uid,
    confirmation_token=None,
    channel=CollaborationChannel.Api,
):
    """A request UUID identifies one copy; a new UUID explicitly requests another copy."""
    if not isinstance(actor, User):
        return None
    # Required operation identity provides retry safety without marking a clone as a mirror.
    request_uid = str(UUID(request_uid))
    if DbSession.has_active_transaction():
        raise RuntimeError("Clone must own its storage and database transaction")
    copied_files = []
    committed = False
    try:
        with DbSession.atomic() as db:
            from ...core.db.DbEngine import DbEngine

            project_ids = sorted({InfraHelper.convert_id(source_project), InfraHelper.convert_id(destination_project)})
            if DbEngine.get_main_engine().dialect.name == "sqlite":
                # SQLite has no row locks: reserve its writer before reading the request receipt.
                db.exec(
                    update(Project)
                    .where(Project.id.in_(project_ids))
                    .values(owner_id=Project.owner_id, updated_at=Project.updated_at)
                )
            projects = db.exec(
                SqlBuilder.select.table(Project)
                .where(Project.id.in_(project_ids))
                .order_by(Project.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).all()
            by_id = {p.id: p for p in projects if p.deleted_at is None}
            source_board = by_id.get(InfraHelper.convert_id(source_project))
            destination = by_id.get(InfraHelper.convert_id(destination_project))
            if not source_board or not destination or source_board.id == destination.id:
                return None
            source = db.exec(
                SqlBuilder.select.table(Card)
                .where(Card.id == InfraHelper.convert_id(source_card))
                .with_for_update()
                .execution_options(populate_existing=True)
            ).first()
            column = db.exec(
                SqlBuilder.select.table(ProjectColumn)
                .where(ProjectColumn.id == InfraHelper.convert_id(destination_column))
                .with_for_update()
                .execution_options(populate_existing=True)
            ).first()
            if (
                not source
                or source.project_id != source_board.id
                or source.archived_at
                or not column
                or column.deleted_at is not None
                or column.is_archive
                or column.project_id != destination.id
            ):
                return None
            original = readable_original(service, actor, source.get_uid(), channel)
            context = service.resolve_visibility_context(destination, actor, channel)
            if (
                original is None
                or context is None
                or not context[1].project_member
                or not _grant(db, actor, destination, ProjectRoleAction.CardWrite)
                or not _grant(db, actor, destination, ProjectRoleAction.Read)
                or not context[1].can_read_card(source.visibility, owner_user_id=source.owner_user_id)
            ):
                return None
            scope = [
                str(actor.id),
                str(source_board.id),
                str(source.id),
                str(destination.id),
                str(column.id),
                request_uid,
            ]
            identity = sha256(dumps(scope).encode()).hexdigest()
            receipts = db.exec(
                SqlBuilder.select.table(ProjectActivity)
                .where(
                    ProjectActivity.project_id == destination.id,
                    ProjectActivity.user_id == actor.id,
                    ProjectActivity.activity_type == ProjectActivityType.CardCloned,
                    ProjectActivity.activity_history["clone_request_uid"].as_string() == request_uid,
                )
                .limit(1)
            ).all()
            for receipt in receipts:
                if receipt.activity_history.get("clone_request_uid") != request_uid:
                    continue
                if receipt.activity_history.get("clone_identity") != identity:
                    raise ValueError("Clone request UUID already belongs to another operation")
                clone = db.exec(SqlBuilder.select.table(Card).where(Card.id == receipt.card_id)).first()
                if clone is None or service.resolve_readable_card(destination, clone, actor, channel) is None:
                    return None
                return {
                    "card": clone.api_response(),
                    "created": False,
                    "excluded": receipt.activity_history["excluded"],
                }

            checklists = _rows(db, Checklist, Checklist.card_id == source.id, Checklist.deleted_at.is_(None))
            items = _rows(
                db,
                Checkitem,
                Checkitem.checklist_id.in_([row.id for row in checklists]),
                Checkitem.deleted_at.is_(None),
            )
            attachments = _rows(
                db, CardAttachment, CardAttachment.card_id == source.id, CardAttachment.deleted_at.is_(None)
            )
            blocks = _rows(db, CardContentBlock, CardContentBlock.card_id == source.id)
            assignments = _rows(db, CardAssignedUser, CardAssignedUser.card_id == source.id)
            label_assignments = _rows(db, CardAssignedProjectLabel, CardAssignedProjectLabel.card_id == source.id)
            source_labels = _rows(
                db, ProjectLabel, ProjectLabel.id.in_([row.project_label_id for row in label_assignments])
            )
            destination_labels = _rows(db, ProjectLabel, ProjectLabel.project_id == destination.id)
            destination_members = _rows(db, ProjectAssignedUser, ProjectAssignedUser.project_id == destination.id)
            # Only destination owner/members can be new readers; unrelated accounts are irrelevant.
            candidate_ids = {destination.owner_id, *(row.user_id for row in destination_members)}
            users = _rows(
                db, User, User.id.in_(candidate_ids), User.deleted_at.is_(None), User.activated_at.is_not(None)
            )
            source_members = _rows(
                db,
                ProjectAssignedUser,
                ProjectAssignedUser.project_id == source_board.id,
                ProjectAssignedUser.user_id.in_(candidate_ids),
            )
            roles = _rows(
                db, ProjectRole, ProjectRole.project_id.in_(project_ids), ProjectRole.user_id.in_(candidate_ids)
            )
            role_map = {(row.project_id, row.user_id): row for row in roles}
            active_ids = {row.id for row in users}
            compatible_members = {row.user_id: row for row in destination_members if row.user_id in active_ids}
            # Board owners have no ProjectAssignedUser row; preserve only native assignment links.
            kept_assignments = [row for row in assignments if row.user_id in compatible_members]
            global_labels = {row.global_label_id: row for row in destination_labels if row.global_label_id is not None}
            kept_labels = [
                global_labels[row.global_label_id]
                for row in source_labels
                if row.global_label_id is not None and row.global_label_id in global_labels
            ]
            excluded = {
                "member_uids": [
                    InfraHelper.convert_uid(row.user_id) for row in assignments if row.user_id not in compatible_members
                ],
                "label_uids": [
                    row.get_uid()
                    for row in source_labels
                    if row.global_label_id is None or row.global_label_id not in global_labels
                ],
            }
            internal_access = {user.id: service._resolve_internal_access(user) for user in users}
            source_readers = _readers(
                source_board, source, users, {row.user_id for row in source_members}, role_map, internal_access
            )
            destination_readers = _readers(
                destination, source, users, {row.user_id for row in destination_members}, role_map, internal_access
            )
            new_readers = destination_readers - source_readers
            state = {
                "scope": scope,
                "source": source.model_dump(mode="json"),
                "source_project": source_board.model_dump(mode="json"),
                "destination_project": destination.model_dump(mode="json"),
                "destination_column": column.model_dump(mode="json"),
                "source_readers": sorted(map(str, source_readers)),
                "destination_readers": sorted(map(str, destination_readers)),
                "rows": [
                    [row.model_dump(mode="json") for row in group]
                    for group in (
                        checklists,
                        items,
                        attachments,
                        blocks,
                        assignments,
                        source_labels,
                        destination_labels,
                        destination_members,
                    )
                ],
                "excluded": excluded,
            }
            token = hmac_new(Env.COMMON_SECRET_KEY.encode(), dumps(state, sort_keys=True).encode(), sha256).hexdigest()
            if new_readers and (not confirmation_token or not compare_digest(token, confirmation_token)):
                return {
                    "confirmation_required": True,
                    "confirmation_token": token,
                    "new_reader_uids": [InfraHelper.convert_uid(uid) for uid in sorted(new_readers)],
                    "excluded": excluded,
                    "created": False,
                }

            clone = Card(
                project_id=destination.id,
                project_column_id=column.id,
                title=source.title,
                next_action=source.next_action,
                priority=source.priority,
                description=deepcopy(source.description),
                visibility=source.visibility,
                owner_user_id=source.owner_user_id,
                created_by_user_id=actor.id,
                order=service.repo.card.get_next_order(column, {"project_id": destination.id}),
            )
            db.insert(clone)
            paths = {}
            from .factory.CardAttachmentService import CardAttachmentService

            attachment_service = service._get_service(CardAttachmentService) if attachments else None
            for attachment in attachments:
                owner = attachment_service.resolve_file_owner(
                    attachment.file.storage_type, attachment.file.storage_name, attachment.file.filename
                )
                if owner is None or owner.id != attachment.id:
                    raise ValueError("Source attachment provenance is missing or ambiguous")
                # A distinct object identity is mandatory: the native resolver rejects ambiguity.
                with TemporaryFile() as file:
                    if not Storage.download_file(attachment.file, file):
                        raise ValueError("Source attachment cannot be read")
                    file.seek(0)
                    new_file = Storage.upload_named(
                        file,
                        StorageName.CardAttachment,
                        uuid4().hex + "." + attachment.file.filename.rsplit(".", 1)[-1],
                        attachment.file.original_filename,
                    )
                if new_file is None:
                    raise ValueError("Attachment copy failed")
                copied_files.append(new_file)
                if (new_file.storage_type, new_file.storage_name, new_file.filename) == (
                    attachment.file.storage_type,
                    attachment.file.storage_name,
                    attachment.file.filename,
                ):
                    copied_files.pop()
                    raise ValueError("Attachment copy did not allocate a new object identity")
                paths[attachment.file.path] = new_file.path
                copied_attachment = CardAttachment(
                    card_id=clone.id,
                    user_id=actor.id,
                    filename=attachment.filename,
                    file=new_file,
                    order=attachment.order,
                    document_text=attachment.document_text,
                )
                db.insert(copied_attachment)
                db.after_commit(
                    lambda copied=copied_attachment: attachment_service.dispatch_created(
                        actor, destination, clone, copied, include_bot=False
                    )
                )

            def rewrite(value):
                if isinstance(value, str):
                    for old, new in paths.items():
                        value = value.replace(old, new)
                    return value
                if isinstance(value, dict):
                    return {key: rewrite(item) for key, item in value.items()}
                if isinstance(value, list):
                    return [rewrite(item) for item in value]
                return value

            clone.description = EditorContentModel(content=rewrite(source.description.content))
            db.update(clone)
            for block in blocks:
                db.insert(
                    CardContentBlock(
                        card_id=clone.id,
                        block_type=block.block_type,
                        order=block.order,
                        payload=rewrite(deepcopy(block.payload)),
                    )
                )
            checklist_ids = {}
            for checklist in checklists:
                copied = Checklist(
                    card_id=clone.id, title=checklist.title, order=checklist.order, is_system=checklist.is_system
                )
                db.insert(copied)
                checklist_ids[checklist.id] = copied.id
            for item in items:
                db.insert(
                    Checkitem(
                        checklist_id=checklist_ids[item.checklist_id], title=rewrite(item.title), order=item.order
                    )
                )
            for assignment in kept_assignments:
                db.insert(
                    CardAssignedUser(
                        card_id=clone.id,
                        user_id=assignment.user_id,
                        project_assigned_id=compatible_members[assignment.user_id].id,
                    )
                )
            for label in kept_labels:
                db.insert(CardAssignedProjectLabel(card_id=clone.id, project_label_id=label.id))
            CardActivityTask.record_card_cloned(
                actor,
                destination,
                clone,
                source_board,
                source,
                request_uid=request_uid,
                identity=identity,
                excluded=excluded,
            )
            payload = _payload(
                clone,
                [InfraHelper.convert_uid(row.user_id) for row in kept_assignments],
                [row.api_response() for row in kept_labels],
            )
            # Socket publication uses the native scope publisher after the database commit.
            db.after_commit(lambda: CardPublisher.created(destination, column, {"card": payload}))
        committed = True
        return {"card": payload, "created": True, "excluded": excluded}
    finally:
        if not committed:
            for file in copied_files:
                Storage.delete(file)
