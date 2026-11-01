"""Internal same-identity moves. HTTP exposure requires canonical URL support."""

from hashlib import sha256
from hmac import compare_digest
from hmac import new as hmac_new
from json import dumps
from uuid import UUID
from sqlalchemy import update
from ...core.db import DbSession, SqlBuilder
from ...core.db.DbEngine import DbEngine
from ...core.security.CollaborationChannel import CollaborationChannel
from ...Env import Env
from ...helpers import InfraHelper
from ...publishers import ProjectPublisher
from ...tasks.activities import CardActivityTask
from ...tasks.webhooks.ExecutionReadinessUow import execution_readiness_uow
from ..models import (
    Card,
    CardAssignedProjectLabel,
    CardAssignedUser,
    CardAttachment,
    CardComment,
    CardContentBlock,
    CardRelationship,
    Checkitem,
    Checklist,
    Project,
    ProjectActivity,
    ProjectAssignedUser,
    ProjectColumn,
    ProjectLabel,
    ProjectRole,
    User,
    WhisperThread,
)
from ..models.ProjectActivity import ProjectActivityType
from ..models.ProjectRole import ProjectRoleAction
from .AppGovernance import lock_policy_mutation
from .CardCloneOperations import _readers, _rows
from .CardCrossBoardOperations import _grant, readable_original
from .DecisionGate import DecisionGate
from .factory.WorkflowStagePolicyService import WorkflowStagePolicyService


def move_card(
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
    """Own one transaction and a durable receipt; replays recheck current rights."""
    if not isinstance(actor, User):
        return None
    request_uid = str(UUID(request_uid))
    if DbSession.has_active_transaction():
        raise RuntimeError("Move must own its database transaction")
    with DbSession.atomic() as db, execution_readiness_uow() as execution:
        # Match native policy mutation ordering before project, card and column locks.
        lock_policy_mutation(db, exclusive=True)
        project_ids = sorted({InfraHelper.convert_id(source_project), InfraHelper.convert_id(destination_project)})
        if DbEngine.get_main_engine().dialect.name == "sqlite":
            db.exec(
                update(Project)
                .where(Project.id.in_(project_ids))
                .values(owner_id=Project.owner_id, updated_at=Project.updated_at)
            )
        actor = db.exec(
            SqlBuilder.select.table(User)
            .where(User.id == actor.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).first()
        if actor is None or actor.deleted_at is not None or not actor.activated_at:
            return None
        projects = db.exec(
            SqlBuilder.select.table(Project)
            .where(Project.id.in_(project_ids))
            .order_by(Project.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
        by_id = {row.id: row for row in projects if row.deleted_at is None}
        source_board = by_id.get(InfraHelper.convert_id(source_project))
        destination = by_id.get(InfraHelper.convert_id(destination_project))
        if not source_board or not destination or source_board.id == destination.id:
            return None
        card = db.exec(
            SqlBuilder.select.table(Card)
            .where(Card.id == InfraHelper.convert_id(source_card))
            .with_for_update()
            .execution_options(populate_existing=True)
        ).first()
        if card is None or card.deleted_at is not None:
            return None
        columns = db.exec(
            SqlBuilder.select.table(ProjectColumn)
            .where(ProjectColumn.id.in_(sorted({card.project_column_id, InfraHelper.convert_id(destination_column)})))
            .order_by(ProjectColumn.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
        column_map = {row.id: row for row in columns if row.deleted_at is None}
        column = column_map.get(InfraHelper.convert_id(destination_column))
        if column is None or column.project_id != destination.id or column.is_archive:
            return None
        # Source board rights remain required even after the card has moved.
        source_context = service.resolve_visibility_context(source_board, actor, channel)
        target_context = service.resolve_visibility_context(destination, actor, channel)
        if (
            source_context is None
            or not source_context[1].project_member
            or target_context is None
            or not target_context[1].project_member
            or not _grant(db, actor, source_board, ProjectRoleAction.Read)
            or not _grant(db, actor, source_board, ProjectRoleAction.CardUpdate)
            or not _grant(db, actor, destination, ProjectRoleAction.CardWrite)
            or not _grant(db, actor, destination, ProjectRoleAction.Read)
            or not target_context[1].can_read_card(card.visibility, owner_user_id=card.owner_user_id)
        ):
            return None
        scope = [str(actor.id), str(source_board.id), str(card.id), str(destination.id), str(column.id), request_uid]
        identity = sha256(dumps(scope).encode()).hexdigest()
        receipts = db.exec(
            SqlBuilder.select.table(ProjectActivity).where(
                ProjectActivity.user_id == actor.id,
                ProjectActivity.activity_type == ProjectActivityType.CardMoved,
                ProjectActivity.activity_history["move_request_uid"].as_string() == request_uid,
            )
        ).all()
        if receipts:
            receipt = receipts[0]
            if receipt.activity_history.get("move_identity") != identity:
                raise ValueError("Move request UUID already belongs to another operation")
            if card.project_id != destination.id or readable_original(service, actor, card.get_uid(), channel) is None:
                return None
            return {"card": card.api_response(), "moved": False, "excluded": receipt.activity_history["excluded"]}
        from_column = column_map.get(card.project_column_id)
        if (
            card.project_id != source_board.id
            or card.archived_at
            or from_column is None
            or from_column.project_id != source_board.id
            or readable_original(service, actor, card.get_uid(), channel) is None
        ):
            return None
        assignments = _rows(db, CardAssignedUser, CardAssignedUser.card_id == card.id)
        label_links = _rows(db, CardAssignedProjectLabel, CardAssignedProjectLabel.card_id == card.id)
        source_labels = _rows(db, ProjectLabel, ProjectLabel.id.in_([row.project_label_id for row in label_links]))
        target_labels = _rows(db, ProjectLabel, ProjectLabel.project_id == destination.id)
        target_members = _rows(db, ProjectAssignedUser, ProjectAssignedUser.project_id == destination.id)
        candidate_ids = {destination.owner_id, *(row.user_id for row in target_members)}
        users = _rows(db, User, User.id.in_(candidate_ids), User.deleted_at.is_(None), User.activated_at.is_not(None))
        source_members = _rows(
            db,
            ProjectAssignedUser,
            ProjectAssignedUser.project_id == source_board.id,
            ProjectAssignedUser.user_id.in_(candidate_ids),
        )
        roles = _rows(db, ProjectRole, ProjectRole.project_id.in_(project_ids), ProjectRole.user_id.in_(candidate_ids))
        attachments = _rows(db, CardAttachment, CardAttachment.card_id == card.id)
        blocks = _rows(db, CardContentBlock, CardContentBlock.card_id == card.id)
        comments = _rows(db, CardComment, CardComment.card_id == card.id)
        checklists = _rows(db, Checklist, Checklist.card_id == card.id)
        items = _rows(db, Checkitem, Checkitem.checklist_id.in_([row.id for row in checklists]))
        role_map = {(row.project_id, row.user_id): row for row in roles}
        internal = {user.id: service._resolve_internal_access(user) for user in users}
        old_readers = _readers(source_board, card, users, {row.user_id for row in source_members}, role_map, internal)
        new_readers = _readers(destination, card, users, {row.user_id for row in target_members}, role_map, internal)
        active_ids = {row.id for row in users}
        members = {row.user_id: row for row in target_members if row.user_id in active_ids}
        globals_by_id = {row.global_label_id: row for row in target_labels if row.global_label_id is not None}
        labels_by_id = {row.id: row for row in source_labels}
        excluded = {
            "member_uids": [InfraHelper.convert_uid(row.user_id) for row in assignments if row.user_id not in members],
            "label_uids": [
                row.get_uid()
                for row in source_labels
                if row.global_label_id is None or row.global_label_id not in globals_by_id
            ],
        }
        state = {
            "scope": scope,
            "source": card.model_dump(mode="json"),
            "source_project": source_board.model_dump(mode="json"),
            "destination_project": destination.model_dump(mode="json"),
            "column": column.model_dump(mode="json"),
            "source_readers": sorted(map(str, old_readers)),
            "target_readers": sorted(map(str, new_readers)),
            "rows": [
                [row.model_dump(mode="json") for row in group]
                for group in (
                    assignments,
                    source_labels,
                    target_labels,
                    target_members,
                    attachments,
                    blocks,
                    comments,
                    checklists,
                    items,
                )
            ],
            "excluded": excluded,
        }
        token = hmac_new(Env.COMMON_SECRET_KEY.encode(), dumps(state, sort_keys=True).encode(), sha256).hexdigest()
        if new_readers - old_readers and (not confirmation_token or not compare_digest(token, confirmation_token)):
            return {
                "confirmation_required": True,
                "confirmation_token": token,
                "moved": False,
                "new_reader_uids": [InfraHelper.convert_uid(uid) for uid in sorted(new_readers - old_readers)],
                "excluded": excluded,
            }
        DecisionGate(service).require(db, actor, source_board, card, from_column, column, channel=channel)
        # Keep attachment identities and source bytes; invalidate only derived locations.
        from .factory.DoclingMetadataService import DoclingMetadataService

        execution.watch_card_and_dependents(card.id)
        service._get_service(DoclingMetadataService).invalidate_card_location(source_board, card)
        for assignment in assignments:
            if assignment.user_id in members:
                assignment.project_assigned_id = members[assignment.user_id].id
                db.update(assignment)
            else:
                db.exec(SqlBuilder.delete.table(CardAssignedUser).where(CardAssignedUser.id == assignment.id))
        for link in label_links:
            label = labels_by_id.get(link.project_label_id)
            target_label = globals_by_id.get(label.global_label_id) if label is not None else None
            if target_label is None:
                db.exec(SqlBuilder.delete.table(CardAssignedProjectLabel).where(CardAssignedProjectLabel.id == link.id))
            else:
                link.project_label_id = target_label.id
                db.update(link)
        # Relocate the authorization/query scope while preserving original
        # column identity and the immutable historical payload.
        db.exec(update(ProjectActivity).where(ProjectActivity.card_id == card.id).values(project_id=destination.id))
        incident = SqlBuilder.select.column(CardRelationship.id).where(
            (CardRelationship.card_id_parent == card.id) | (CardRelationship.card_id_child == card.id)
        )
        db.exec(
            update(WhisperThread)
            .where(
                (WhisperThread.card_id == card.id)
                | ((WhisperThread.project_id == source_board.id) & WhisperThread.relationship_id.in_(incident))
            )
            .values(project_id=destination.id)
        )
        old_order = card.order
        card.project_id = destination.id
        card.project_column_id = column.id
        card.order = service.repo.card.get_next_order(column, {"project_id": destination.id})
        db.update(card)
        service._get_service(WorkflowStagePolicyService).apply_transition(actor, destination, card, from_column, column)
        service._get_service(DoclingMetadataService).relocate_card_documents(card)
        service.repo.card.reoder_after_delete(from_column.id, old_order)
        CardActivityTask.record_card_cross_board_moved(
            actor,
            source_board,
            destination,
            card,
            from_column,
            request_uid=request_uid,
            identity=identity,
            excluded=excluded,
        )
        service._invalidate_source_card_mirrors(card)
        # Reuse the existing board visibility refresh. A delete event would erase
        # the same UID from a second board pane and requires a deleted source row.
        for board in (source_board, destination):
            db.after_commit(lambda board=board: ProjectPublisher.updated(board, {"card_visibility_changed": True}))
        payload = card.board_api_response(
            0,
            [InfraHelper.convert_uid(row.user_id) for row in assignments if row.user_id in members],
            [],
            [
                globals_by_id[row.global_label_id].api_response()
                for row in source_labels
                if row.global_label_id in globals_by_id
            ],
        )
        return {"card": payload, "moved": True, "excluded": excluded}
