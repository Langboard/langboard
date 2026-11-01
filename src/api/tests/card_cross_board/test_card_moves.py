"""Persisted same-identity moves, old-source fences and current location authorization."""

import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from langboard_shared.core.db import DbSession, EditorContentModel
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.core.storage import Storage, StorageName
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import (
    Card,
    CardAppOwnership,
    CardAssignedProjectLabel,
    CardAssignedUser,
    CardAttachment,
    CardComment,
    CardDecisionGate,
    CardMetadata,
    CardRelationship,
    DecisionCase,
    DocumentExtractionCache,
    DocumentExtractionCacheReference,
    DocumentKeywordIndex,
    DocumentVectorCleanup,
    GlobalCardRelationshipType,
    GlobalLabel,
    KeywordSearchConfiguration,
    ProjectActivity,
    ProjectAssignedUser,
    ProjectColumn,
    ProjectLabel,
    ProjectRole,
    WhisperThread,
    WorkflowRule,
    WorkflowStageDefinition,
)
from langboard_shared.domain.models.ProjectActivity import ProjectActivityType
from langboard_shared.domain.services.CardDependencyState import card_dependency_state
from langboard_shared.domain.services.factory.CardRelationshipService import CardRelationshipService
from langboard_shared.domain.services.factory.DoclingMetadataService import DoclingMetadataService
from langboard_shared.domain.services.factory.WorkflowStagePolicyService import WorkflowStagePolicyService
from langboard_shared.infrastructure.repositories.factory.CardRelationshipRepository import CardRelationshipRepository
from langboard_shared.infrastructure.repositories.factory.MetadataRepository import MetadataRepository
from langboard_shared.publishers import CardPublisher, ProjectPublisher
from langboard_shared.tasks.activities import CardActivityTask
from sqlalchemy import delete, update
from starlette.requests import Request
from test_card_clones import add_reader, clone_board, rows  # noqa: F401
from test_card_mirrors import mirror_board  # noqa: F401


@pytest.fixture
def move_board(clone_board, monkeypatch):  # noqa: F811
    b = clone_board
    with b.engine.begin() as connection:
        # Keep schema setup atomic in SQLite legacy mode as well as PostgreSQL.
        if b.engine.dialect.name == "sqlite":
            connection.exec_driver_sql("BEGIN")
        for model in (DecisionCase, CardDecisionGate):
            model.__table__.create(connection)
        for model in (
            CardMetadata,
            DocumentKeywordIndex,
            DocumentVectorCleanup,
            WhisperThread,
            KeywordSearchConfiguration,
            DocumentExtractionCache,
            DocumentExtractionCacheReference,
            WorkflowRule,
        ):
            model.__table__.create(connection)
    b.service.repo.metadata = MetadataRepository(None, None)
    b.service.repo.card_relationship = CardRelationshipRepository(None, None)
    b.metadata = DoclingMetadataService(lambda _: None, lambda _: None, b.service.repo)
    b.relationships = CardRelationshipService(lambda _: b.service, lambda _: None, b.service.repo)
    b.workflow = WorkflowStagePolicyService(lambda _: None, lambda _: None, b.service.repo)
    b.execution = Mock()
    if b.engine.dialect.name == "sqlite":
        from langboard_shared.domain.services import CardMoveOperations

        @contextmanager
        def recording_readiness():
            yield b.execution

        monkeypatch.setattr(CardMoveOperations, "execution_readiness_uow", recording_readiness)
    original = b.service._raw_get_service
    b.service._raw_get_service = lambda kind: (
        b.metadata
        if kind is DoclingMetadataService
        else b.relationships
        if kind is CardRelationshipService
        else b.workflow
        if kind is WorkflowStagePolicyService
        else original(kind)
    )
    b.removed = Mock()
    monkeypatch.setattr(CardPublisher, "deleted", b.removed)
    b.refreshed = Mock()
    monkeypatch.setattr(ProjectPublisher, "updated", b.refreshed)
    with DbSession.use(readonly=False) as db:
        db.exec(
            update(ProjectRole)
            .where(ProjectRole.project_id == b.source_board.id)
            .values(actions=["read", "card_update"])
        )
    return b


def move(b, **kwargs):
    return b.service.move_cross_board_card(
        b.actor, b.source_board, b.source.get_uid(), b.destination, b.column, request_uid=b.request_uid, **kwargs
    )


def test_move_preserves_identity_history_relations_and_whispers(move_board):
    b = move_board
    with DbSession.use(readonly=False) as db:
        peer = Card(
            project_id=b.source_board.id,
            project_column_id=b.source.project_column_id,
            title="Peer",
            visibility="SHARED",
        )
        db.insert(peer)
        kind = GlobalCardRelationshipType(parent_name="Blocks", child_name="Blocked by", machine_semantic="blocks")
        db.insert(kind)
        edge = CardRelationship(card_id_parent=b.source.id, card_id_child=peer.id, relationship_type_id=kind.id)
        db.insert(edge)
        comment = CardComment(
            card_id=b.source.id, user_id=b.actor.id, content=EditorContentModel(content="Preserved comment")
        )
        db.insert(comment)
        history = ProjectActivity(
            project_id=b.source_board.id,
            project_column_id=b.source.project_column_id,
            card_id=b.source.id,
            user_id=b.actor.id,
            activity_type=ProjectActivityType.CardCreated,
            activity_history={"original": "immutable history"},
        )
        db.insert(history)
        thread = WhisperThread(project_id=b.source_board.id, card_id=b.source.id, creator_id=b.actor.id)
        db.insert(thread)
    original_id, original_body = b.source.id, b.source.description
    result = move(b)
    assert result["moved"] and result["card"]["uid"] == b.source.get_uid()
    current = rows(Card, Card.id == original_id)[0]
    assert current.project_id == b.destination.id and current.project_column_id == b.column.id
    assert current.description == original_body and current.created_by_user_id == b.outsider.id
    assert rows(CardComment)[0].id == comment.id
    assert rows(CardRelationship)[0].id == edge.id
    assert rows(WhisperThread)[0].project_id == b.destination.id
    migrated = rows(ProjectActivity, ProjectActivity.id == history.id)[0]
    assert migrated.project_id == b.destination.id and migrated.activity_history == {"original": "immutable history"}
    assert migrated.project_column_id == history.project_column_id
    assert [card.id for card in b.service.get_by_project(b.source_board)] == [peer.id]
    assert [card.id for card in b.service.get_by_project(b.destination)] == [original_id]
    again = move(b)
    assert not again["moved"] and again["card"]["uid"] == result["card"]["uid"]
    assert len(rows(ProjectActivity)) == 3
    b.published.assert_not_called()
    b.removed.assert_not_called()
    assert b.refreshed.call_count == 2
    b.execution.watch_card_and_dependents.assert_called_once_with(original_id)
    assert all(call.args[1] == {"card_visibility_changed": True} for call in b.refreshed.call_args_list)
    source_event = rows(ProjectActivity, ProjectActivity.project_id == b.source_board.id)[0]
    assert source_event.card_id is None and set(source_event.activity_history) == {"moved_out", "recorder"}


def test_audience_confirmation_is_current_and_only_for_added_readers(move_board):
    b = move_board
    reader = add_reader(b)
    preview = move(b)
    assert preview["confirmation_required"] and preview["new_reader_uids"] == [reader.get_uid()]
    assert rows(Card)[0].project_id == b.source_board.id
    with DbSession.use(readonly=False) as db:
        db.exec(update(Card).where(Card.id == b.source.id).values(title="Changed while confirming"))
    refreshed = move(b, confirmation_token=preview["confirmation_token"])
    assert refreshed["confirmation_required"] and refreshed["confirmation_token"] != preview["confirmation_token"]
    assert move(b, confirmation_token=refreshed["confirmation_token"])["moved"]


@pytest.mark.parametrize(
    "revoke", ["source_update", "source_read", "source_member", "target_member", "target_write", "app", "column"]
)
def test_move_denies_current_revocation_and_no_partial_writes(move_board, revoke):
    b = move_board
    with DbSession.use(readonly=False) as db:
        if revoke.startswith("source_"):
            if revoke == "source_member":
                db.exec(delete(ProjectAssignedUser).where(ProjectAssignedUser.project_id == b.source_board.id))
            else:
                db.exec(
                    update(ProjectRole)
                    .where(ProjectRole.project_id == b.source_board.id)
                    .values(actions=["read"] if revoke == "source_update" else ["card_update"])
                )
        elif revoke == "target_member":
            db.exec(
                update(type(b.destination))
                .where(type(b.destination).id == b.destination.id)
                .values(owner_id=b.outsider.id)
            )
        elif revoke == "target_write":
            db.exec(
                update(type(b.destination))
                .where(type(b.destination).id == b.destination.id)
                .values(owner_id=b.outsider.id)
            )
            db.insert(ProjectAssignedUser(project_id=b.destination.id, user_id=b.actor.id))
            db.insert(ProjectRole(project_id=b.destination.id, user_id=b.actor.id, actions=["read"]))
        elif revoke == "app":
            db.insert(CardAppOwnership(card_id=b.source.id, app_key="owned"))
        else:
            db.exec(update(type(b.column)).where(type(b.column).id == b.column.id).values(is_archive=True))
    assert move(b) is None
    assert rows(Card)[0].project_id == b.source_board.id and rows(ProjectActivity) == []


@pytest.mark.parametrize("mirror_board", ["sqlite", "postgresql-test"], indirect=True)
@pytest.mark.parametrize("revoke_read", [False, True])
def test_family_move_rechecks_read_grant_with_native_visibility(move_board, monkeypatch, revoke_read):
    b = move_board
    WorkflowStageDefinition.__table__.create(b.engine)
    with DbSession.atomic() as db:
        db.insert(WorkflowStageDefinition(key="family-target", name="Family target", confirm_descendant_move=True))
        target = ProjectColumn(project_id=b.source_board.id, name="Family target", workflow_stage="family-target")
        db.insert(target)
        child = Card(
            project_id=b.source_board.id,
            project_column_id=b.source.project_column_id,
            title="Readable child before revocation",
            visibility="SHARED",
        )
        db.insert(child)
        kind = GlobalCardRelationshipType(
            parent_name="Contains", child_name="Part of", machine_semantic="contains", is_system_default=True
        )
        db.insert(kind)
        db.insert(CardRelationship(card_id_parent=b.source.id, card_id_child=child.id, relationship_type_id=kind.id))
    preview = b.service.change_order(b.actor, b.source_board, b.source, 0, target)
    assert preview["confirmation_required"]
    if revoke_read:
        with DbSession.atomic() as db:
            db.exec(
                update(ProjectRole)
                .where(ProjectRole.project_id == b.source_board.id, ProjectRole.user_id == b.actor.id)
                .values(actions=["card_update"])
            )
    admitted = Mock(return_value=True)
    monkeypatch.setattr(b.service, "_change_order", admitted)
    if not revoke_read:
        assert b.service.change_order(
            b.actor, b.source_board, b.source, 0, target,
            move_descendants=True, confirmation_revision=preview["confirmation_revision"],
        ) is True
        assert admitted.call_count == 2
        return
    with pytest.raises(ValueError, match="permission|complete card family"):
        b.service.change_order(
            b.actor, b.source_board, b.source, 0, target,
            move_descendants=True, confirmation_revision=preview["confirmation_revision"],
        )
    admitted.assert_not_called()


def test_retry_rechecks_source_rights_and_current_destination_readers(move_board):
    b = move_board
    assert move(b)["moved"]
    with DbSession.use(readonly=False) as db:
        db.exec(update(ProjectRole).where(ProjectRole.project_id == b.source_board.id).values(actions=["read"]))
    assert move(b) is None
    assert len(rows(ProjectActivity)) == 2


def test_old_url_returns_only_current_authorized_location(move_board):
    from fastapi.dependencies.utils import get_dependant
    from langboard.routes.board.BoardCardApi import get_card_details
    from langboard_shared.core.routing import ApiException

    b = move_board
    assert move(b)["moved"]
    request = Request({"type": "http", "query_string": b"", "headers": []})
    service = SimpleNamespace(card=b.service)
    with DbSession.use(readonly=False) as db:
        db.exec(delete(ProjectAssignedUser).where(ProjectAssignedUser.project_id == b.source_board.id))
        db.exec(delete(ProjectRole).where(ProjectRole.project_id == b.source_board.id))
    response = get_card_details(b.source_board.get_uid(), b.source.get_uid(), request, b.actor, service, True)
    assert json.loads(response.body) == {
        "canonical_project_uid": b.destination.get_uid(),
        "card_uid": b.source.get_uid(),
    }
    assert response.headers["cache-control"] == "no-store"
    same = get_card_details(b.destination.get_uid(), b.source.get_uid(), request, b.actor, service, True)
    assert json.loads(same.body) == json.loads(response.body)
    assert b.service.resolve_canonical_card(b.source_board, b.source, b.outsider) is None
    assert b.service.resolve_canonical_card(b.column.get_uid(), b.source, b.actor) is None
    with pytest.raises(ApiException.NotFound_404) as denied:
        get_card_details(b.source_board.get_uid(), b.source.get_uid(), request, b.outsider, service, True)
    assert denied.value.status_code == 404 and denied.value.headers == {"Cache-Control": "no-store"}
    assert b.destination.get_uid() not in json.dumps(denied.value.detail)
    # Exercise the native FastAPI field generated by the actual route signature.
    location = next(
        field
        for field in get_dependant(path="/{project_uid}/{card_uid}", call=get_card_details).query_params
        if field.name == "location_only"
    )
    assert location.validate("false", {}, loc=("query", "location_only")) == (False, [])
    assert location.validate("true", {}, loc=("query", "location_only")) == (True, [])
    assert location.validate("invalid", {}, loc=("query", "location_only"))[1]


def test_old_worker_results_fenced_and_search_cleanup_durable(move_board):
    b = move_board
    document = {
        "attachment_uid": b.source.get_uid(),
        "status": "indexed",
        "generation": "old-generation",
        "content_hash": "hash",
        "content": {"markdown": "Retained text"},
        "embedding": {
            "status": "indexed",
            "pointer": {
                "board_uid": b.source_board.get_uid(),
                "card_uid": b.source.get_uid(),
                "chunk_ids": ["old-chunk"],
                "generation": "old-generation",
            },
        },
    }
    b.metadata.save_documents(CardMetadata, b.source, [document])
    with DbSession.use(readonly=False) as db:
        job = DocumentKeywordIndex(
            attachment_id=b.source.id,
            card_id=b.source.id,
            project_id=b.source_board.id,
            destination_fingerprint="test",
            destination={"endpoint": "test", "index": "test"},
            source_generation="old-generation",
            claim_token="in-flight",
        )
        db.insert(job)
    assert move(b)["moved"]
    live = b.metadata.load_documents(CardMetadata, b.source)[0]
    assert live["generation"] != "old-generation" and live["content"] == document["content"]
    assert live["embedding"]["status"] == "pending"
    assert live["embedding"]["pointer"] == document["embedding"]["pointer"]
    assert not b.metadata.upsert_document(CardMetadata, b.source, document, expected_generation="old-generation")
    assert not b.metadata.claim_document(CardMetadata, b.source, b.source.get_uid(), "old.pdf")
    assert rows(DocumentVectorCleanup) == []  # Old cache ownership survives until replacement publication.
    keyword = rows(DocumentKeywordIndex)[0]
    assert keyword.operation == "remove" and keyword.claim_token is None and keyword.version == 2


def test_relationship_locations_require_both_current_boards(move_board):
    b = move_board
    with DbSession.use(readonly=False) as db:
        peer = Card(
            project_id=b.source_board.id,
            project_column_id=b.source.project_column_id,
            title="Peer",
            visibility="SHARED",
        )
        db.insert(peer)
        kind = GlobalCardRelationshipType(
            parent_name="Blocks", child_name="Blocked by", machine_semantic="blocks", affects_readiness=True
        )
        db.insert(kind)
        edge = CardRelationship(card_id_parent=b.source.id, card_id_child=peer.id, relationship_type_id=kind.id)
        db.insert(edge)
    assert move(b)["moved"]
    context = b.service.resolve_visibility_context(b.destination, b.actor)[1]
    projection = b.relationships.get_api_list_by_card(b.source, context=context)[0]
    assert (
        projection["uid"] == edge.get_uid()
        and projection["machine_semantic"] == "blocks"
        and projection["affects_readiness"]
    )
    assert projection["parent_location"]["project_uid"] == b.destination.get_uid()
    assert projection["child_location"]["project_title"] == b.source_board.title
    with DbSession.use(readonly=False) as db:
        db.exec(delete(ProjectRole).where(ProjectRole.project_id == b.source_board.id))
    assert b.relationships.get_api_list_by_card(b.source, context=context) == []


def test_concurrent_same_request_has_one_durable_outcome(move_board):
    b = move_board
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: move(b), range(2)))
    assert sorted(result["moved"] for result in results) == [False, True]
    assert len(rows(Card)) == 1 and len(rows(ProjectActivity)) == 2


def test_http_move_preserves_identity_and_checks_current_source_and_target_permissions(move_board):
    from langboard.routes.board.BoardCardCrossBoardApi import CrossBoardCardForm, cross_board_card
    from langboard_shared.core.routing import ApiException

    b = move_board
    form = CrossBoardCardForm(
        source_project_uid=b.source_board.get_uid(),
        source_card_uid=b.source.get_uid(),
        destination_column_uid=b.column.get_uid(),
        operation="move",
        request_uid=b.request_uid,
    )

    def call():
        return cross_board_card(
            b.destination.get_uid(),
            form,
            Request({"type": "http"}),
            b.actor,
            SimpleNamespace(card=b.service),
        )

    # Cached route admission cannot replace the transaction's native current grant.
    with DbSession.use(readonly=False) as db:
        db.exec(update(ProjectRole).where(ProjectRole.project_id == b.source_board.id).values(actions=["read"]))
    with pytest.raises(ApiException.NotFound_404):
        call()
    assert rows(Card)[0].project_id == b.source_board.id
    with DbSession.use(readonly=False) as db:
        db.exec(
            update(ProjectRole)
            .where(ProjectRole.project_id == b.source_board.id)
            .values(actions=["read", "card_update"])
        )
    response = call()
    assert response.status_code == 200
    assert json.loads(response.body)["moved"] is True
    assert json.loads(response.body)["card"]["uid"] == b.source.get_uid()
    assert json.loads(call().body)["moved"] is False and len(rows(ProjectActivity)) == 2
    with DbSession.use(readonly=False) as db:
        db.exec(
            update(type(b.destination)).where(type(b.destination).id == b.destination.id).values(owner_id=b.outsider.id)
        )
    with pytest.raises(ApiException.NotFound_404):
        call()
    assert rows(Card)[0].project_id == b.destination.id and len(rows(ProjectActivity)) == 2


def test_attachment_provenance_and_compatible_mappings_survive_without_model_calls(move_board, monkeypatch):
    from langboard_shared.core.broker import Broker

    b = move_board
    file = Storage.upload_named(
        BytesIO(b"Preserved original bytes"), StorageName.CardAttachment, "original.bin", "original.bin"
    )
    with DbSession.use(readonly=False) as db:
        attachment = CardAttachment(
            card_id=b.source.id,
            user_id=b.outsider.id,
            filename="Original",
            file=file,
            document_text="Original extracted text",
        )
        db.insert(attachment)
        source_member = rows(ProjectAssignedUser)[0]
        target_member = ProjectAssignedUser(project_id=b.destination.id, user_id=b.actor.id)
        db.insert(target_member)
        assignment = CardAssignedUser(card_id=b.source.id, user_id=b.actor.id, project_assigned_id=source_member.id)
        db.insert(assignment)
        other_member = ProjectAssignedUser(project_id=b.source_board.id, user_id=b.outsider.id)
        db.insert(other_member)
        db.insert(CardAssignedUser(card_id=b.source.id, user_id=b.outsider.id, project_assigned_id=other_member.id))
        global_label = GlobalLabel(name="Global", color="#ffffff", description="Global")
        db.insert(global_label)
        original_global = ProjectLabel(
            project_id=b.source_board.id,
            global_label_id=global_label.id,
            name="Global",
            color="#ffffff",
            description="Global",
        )
        target_global = ProjectLabel(
            project_id=b.destination.id,
            global_label_id=global_label.id,
            name="Global",
            color="#ffffff",
            description="Global",
        )
        local = ProjectLabel(project_id=b.source_board.id, name="Local", color="#000000", description="Local")
        for label in (original_global, target_global, local):
            db.insert(label)
        link = CardAssignedProjectLabel(card_id=b.source.id, project_label_id=original_global.id)
        db.insert(link)
        db.insert(CardAssignedProjectLabel(card_id=b.source.id, project_label_id=local.id))
    send = Mock(side_effect=AssertionError("Move must not enqueue model computation"))
    monkeypatch.setattr(Broker.celery, "send_task", send)
    result = move(b)
    assert result["excluded"] == {"member_uids": [b.outsider.get_uid()], "label_uids": [local.get_uid()]}
    current_attachment = rows(CardAttachment)[0]
    assert current_attachment.id == attachment.id and current_attachment.user_id == b.outsider.id
    assert current_attachment.file == file and Storage.get_file(file) == b"Preserved original bytes"
    assert current_attachment.document_text == "Original extracted text"
    assert (
        rows(CardAssignedUser)[0].id == assignment.id
        and rows(CardAssignedUser)[0].project_assigned_id == target_member.id
    )
    assert (
        rows(CardAssignedProjectLabel)[0].id == link.id
        and rows(CardAssignedProjectLabel)[0].project_label_id == target_global.id
    )
    assert len(rows(ProjectLabel)) == 3 and len(rows(ProjectAssignedUser)) == 3
    send.assert_not_called()


@pytest.mark.parametrize(
    "state,expected",
    [
        ("completed", "clear"),
        ("incomplete", "blocked"),
        ("archived", "blocked"),
        ("deleted_board", "blocked"),
        ("wrong_column", "blocked"),
    ],
)
def test_cross_board_dependency_uses_current_prerequisite_location(move_board, state, expected):
    b = move_board
    WorkflowStageDefinition.__table__.create(b.engine)
    with DbSession.use(readonly=False) as db:
        stage = WorkflowStageDefinition(key="complete", name="Completed", counts_as_completed=True)
        db.insert(stage)
        db.exec(update(ProjectColumn).where(ProjectColumn.id == b.column.id).values(workflow_stage=stage.key))
        child = Card(
            project_id=b.source_board.id,
            project_column_id=b.source.project_column_id,
            title="Dependent",
            visibility="SHARED",
        )
        db.insert(child)
        kind = GlobalCardRelationshipType(parent_name="Blocks", child_name="Blocked by", machine_semantic="blocks")
        db.insert(kind)
        db.insert(CardRelationship(card_id_parent=b.source.id, card_id_child=child.id, relationship_type_id=kind.id))
    assert move(b)["moved"]
    with DbSession.use(readonly=False) as db:
        if state == "incomplete":
            db.exec(
                update(WorkflowStageDefinition)
                .where(WorkflowStageDefinition.id == stage.id)
                .values(counts_as_completed=False)
            )
        elif state == "archived":
            db.exec(update(Card).where(Card.id == b.source.id).values(archived_at=SafeDateTime.now()))
        elif state == "deleted_board":
            db.exec(
                update(type(b.destination))
                .where(type(b.destination).id == b.destination.id)
                .values(deleted_at=SafeDateTime.now())
            )
        elif state == "wrong_column":
            db.exec(update(ProjectColumn).where(ProjectColumn.id == b.column.id).values(project_id=b.source_board.id))
        result = card_dependency_state(db, b.source_board.id, child.id)
    assert result["state"] == expected


def test_failed_receipt_rolls_back_location_and_generation(move_board, monkeypatch):
    b = move_board
    document = {"attachment_uid": b.source.get_uid(), "generation": "before", "status": "processing"}
    b.metadata.save_documents(CardMetadata, b.source, [document])
    monkeypatch.setattr(
        CardActivityTask, "record_card_cross_board_moved", Mock(side_effect=RuntimeError("receipt failed"))
    )
    with pytest.raises(RuntimeError, match="receipt failed"):
        move(b)
    assert rows(Card)[0].project_id == b.source_board.id and rows(ProjectActivity) == []
    assert b.metadata.load_documents(CardMetadata, b.source) == [document]
    b.refreshed.assert_not_called()


@pytest.mark.parametrize(
    "visibility,channel,allowed",
    [
        ("PRIVATE", CollaborationChannel.Api, False),
        ("PRIVATE", CollaborationChannel.HumanUI, True),
        ("INTERNAL", CollaborationChannel.HumanUI, True),
    ],
)
def test_move_preserves_visibility_channel_policy(move_board, visibility, channel, allowed):
    b = move_board
    b.service._resolve_internal_access = lambda _: True
    with DbSession.use(readonly=False) as db:
        db.exec(
            update(Card)
            .where(Card.id == b.source.id)
            .values(
                visibility=visibility,
                owner_user_id=b.actor.id if visibility == "PRIVATE" else None,
                created_by_user_id=b.actor.id,
            )
        )
    result = move(b, channel=channel)
    assert bool(result and result.get("moved")) == allowed
    current = rows(Card)[0]
    assert current.visibility == visibility
    assert current.owner_user_id == (b.actor.id if visibility == "PRIVATE" else None)


def test_move_recovery_reuses_native_vectors_and_releases_old_pointer_after_publication(
    move_board, monkeypatch, tmp_path
):
    import asyncio
    from hashlib import sha256
    from uuid import uuid4
    from langboard_shared.core.broker import Broker
    from langboard_shared.domain.models import (
        CardDocumentArtifact,
        DocumentEmbeddingCache,
        DocumentEmbeddingCacheReference,
    )
    from langboard_shared.domain.models.InternalBot import InternalBotType
    from langboard_shared.helpers import InfraHelper
    from langboard_shared.tasks.docling import DocumentEmbeddingTask as task
    from langboard_shared.tasks.docling.DocumentExtractionCache import cached_document_extraction
    from langboard_shared.tasks.docling.DocumentRetrievalSettings import DocumentRetrievalSettings
    from langboard_shared.tasks.docling.DocumentVectorStore import open_document_vector_store
    from langchain_core.embeddings import Embeddings

    b = move_board
    for model in (CardDocumentArtifact, DocumentEmbeddingCache, DocumentEmbeddingCacheReference):
        model.__table__.create(b.engine)
    text = "Existing attachment alpha content"
    file = Storage.upload_named(BytesIO(text.encode()), StorageName.CardAttachment, "cached.bin", "cached.bin")
    with DbSession.use(readonly=False) as db:
        attachment = CardAttachment(card_id=b.source.id, user_id=b.actor.id, filename="cached.bin", file=file)
        db.insert(attachment)

    class Provider(Embeddings):
        calls = 0

        def embed_documents(self, texts):
            self.calls += 1
            return [[1.0, 0.0, 0.5] for _ in texts]

        def embed_query(self, text):
            return [1.0, 0.0, 0.5]

    provider = Provider()
    settings = DocumentRetrievalSettings(enabled=True, store="sqlite", dimensions=3)
    snapshot = {"binding_uid": "server-binding", "retrieval": settings.model_dump(mode="json")}
    generation = uuid4().hex
    document = {
        "attachment_uid": attachment.get_uid(),
        "status": "indexed",
        "generation": generation,
        "content_hash": sha256(text.encode()).hexdigest(),
        "content": {"markdown": text},
        "embedding_config": snapshot,
    }
    b.metadata.save_documents(CardMetadata, b.source, [document])
    with cached_document_extraction(
        binding_uid="fixture-vision",
        content_hash=document["content_hash"],
        vision_config={},
        filename=attachment.filename,
        timeout_seconds=1,
    ) as extraction:
        content = extraction.get_or_convert(lambda: {"markdown": text})
        assert b.metadata.mark_document_indexed(
            CardMetadata,
            b.source,
            attachment.get_uid(),
            "markdown",
            content_hash=document["content_hash"],
            content=content,
            generation=generation,
            extraction_cache_token=extraction.token,
        )
        extraction.retain()
    service = SimpleNamespace(
        card=b.service,
        docling_metadata=b.metadata,
        card_attachment=SimpleNamespace(
            get_by_id_like=lambda uid: rows(CardAttachment, CardAttachment.id == InfraHelper.convert_id(uid))[0]
        ),
        project=SimpleNamespace(
            get_by_id_like=lambda uid: rows(type(b.destination), type(b.destination).id == InfraHelper.convert_id(uid))[
                0
            ]
        ),
        internal_bot=SimpleNamespace(
            get_current_by_id_like=lambda _: SimpleNamespace(
                bot_type=InternalBotType.DocumentEmbedding, value="private"
            )
        ),
    )
    monkeypatch.setattr(
        task, "Env", SimpleNamespace(DATA_DIR=tmp_path, get_from_env=lambda *_: "https://provider.invalid")
    )
    monkeypatch.setattr(task, "resolve_embedding_snapshot", lambda *_: "private")
    monkeypatch.setattr(
        task,
        "validate_embedding_config",
        lambda _: ({"base_url": "https://provider.invalid", "model_name": "test"}, settings),
    )
    monkeypatch.setattr(task, "create_document_embeddings", lambda *_: provider)
    monkeypatch.setattr(b.metadata, "publish_update", Mock())
    send = Mock()
    monkeypatch.setattr(Broker.celery, "send_task", send)
    asyncio.run(task.embed_transcription(service, attachment.get_uid(), generation))
    original = b.metadata.load_documents(CardMetadata, b.source)[0]
    assert original["embedding"]["status"] == "indexed", original["embedding"]
    old_pointer = original["embedding"]["pointer"]
    assert provider.calls == 1 and old_pointer["board_uid"] == b.source_board.get_uid()
    assert all(ref.expires_at is None for ref in rows(DocumentEmbeddingCacheReference))
    send.side_effect = RuntimeError("broker unavailable")
    assert move(b)["moved"]
    pending = b.metadata.load_documents(CardMetadata, b.source)[0]
    assert pending["location_reindex_pending"] is True and pending["embedding"]["pointer"] == old_pointer
    assert rows(DocumentVectorCleanup) == [] and len(rows(DocumentEmbeddingCache)) == 1
    assert all(ref.expires_at is None for ref in rows(DocumentEmbeddingCacheReference))
    # The old worker cannot replace the current source. Recovery dispatch is
    # bounded, and the real native worker reuses the retained inference cache.
    assert not b.metadata.publish_document_embedding(
        b.source,
        attachment.get_uid(),
        generation,
        original["content_hash"],
        {"status": "indexed", "pointer": old_pointer},
    )
    send.side_effect = None
    send.reset_mock()
    assert b.metadata.recover_card_location_embeddings(limit=1) == 1
    send.assert_called_once()
    assert "DocumentEmbeddingTask.index_transcribed_attachment" in send.call_args.args[0]
    asyncio.run(task.embed_transcription(service, attachment.get_uid(), pending["generation"]))
    current = b.metadata.load_documents(CardMetadata, b.source)[0]
    assert "location_reindex_pending" not in current
    new_pointer = current["embedding"]["pointer"]
    assert new_pointer["board_uid"] == b.destination.get_uid() and new_pointer != old_pointer
    assert provider.calls == 1
    assert b.metadata.recover_card_location_embeddings(limit=1) == 0
    cleanup = rows(DocumentVectorCleanup)
    assert len(cleanup) == 1 and cleanup[0].pointer["board_uid"] == b.source_board.get_uid()
    refs = rows(DocumentEmbeddingCacheReference)
    assert len(refs) == 2 and all(ref.expires_at is None for ref in refs)
    with open_document_vector_store(
        settings, provider, new_pointer["embedding_fingerprint"], tmp_path / "document-retrieval", set(), create=False
    ) as store:
        assert store.get_by_ids(new_pointer["chunk_ids"])[0].page_content == text


def test_keyword_location_uses_native_durable_generation_and_current_board(move_board, monkeypatch):
    from langboard_shared.core.broker import Broker
    from langboard_shared.domain.services import KeywordSearchSettings
    from langboard_shared.tasks.docling import DocumentKeywordTask as keyword

    b = move_board
    file = Storage.upload_named(BytesIO(b"retained text"), StorageName.CardAttachment, "keyword.bin", "keyword.bin")
    with DbSession.use(readonly=False) as db:
        attachment = CardAttachment(
            card_id=b.source.id, user_id=b.actor.id, filename="keyword.bin", file=file, document_text="retained text"
        )
        db.insert(attachment)
    monkeypatch.setattr(
        keyword,
        "index_configuration",
        lambda: {"endpoint": "https://keyword.invalid", "index": "cards", "connection_id": None},
    )
    monkeypatch.setattr(KeywordSearchSettings, "enabled", lambda _: True)
    send = Mock(side_effect=RuntimeError("broker unavailable"))
    monkeypatch.setattr(Broker.celery, "send_task", send)
    with DbSession.atomic() as db:
        job_uid = keyword.prepare_index(db, b.source, attachment.get_uid(), "source-generation")
    b.metadata.save_documents(
        CardMetadata,
        b.source,
        [
            {
                "attachment_uid": attachment.get_uid(),
                "status": "indexed",
                "generation": "source-generation",
                "content_hash": "hash",
                "content": {"markdown": "retained text"},
                "keyword_index_job_uid": job_uid,
            }
        ],
    )
    assert move(b)["moved"]
    document = b.metadata.load_documents(CardMetadata, b.source)[0]
    job = rows(DocumentKeywordIndex)[0]
    assert job.get_uid() == job_uid and job.project_id == b.destination.id and job.operation == "index"
    assert job.source_generation == document["generation"] != "source-generation"
    assert job.due_at is not None and job.claim_token is None
    assert job.version > 1 and rows(CardAttachment)[0].document_text == "retained text"
    assert document["keyword_index_job_uid"] == job_uid


@pytest.mark.parametrize("status", ["pending", "processing"])
def test_move_recovers_inflight_conversion_at_current_location(move_board, monkeypatch, tmp_path, status):
    from tempfile import NamedTemporaryFile
    from langboard_shared.core.broker import Broker
    from langboard_shared.domain.models import (
        CardDocumentArtifact,
    )
    from langboard_shared.domain.models.InternalBot import InternalBotType
    from langboard_shared.helpers import InfraHelper
    from langboard_shared.tasks.docling import DoclingMetadataTask as task

    b = move_board
    CardDocumentArtifact.__table__.create(b.engine)
    file = Storage.upload_named(BytesIO(b"pending bytes"), StorageName.CardAttachment, "pending.pdf", "pending.pdf")
    with DbSession.use(readonly=False) as db:
        attachment = CardAttachment(card_id=b.source.id, user_id=b.actor.id, filename="pending.pdf", file=file)
        db.insert(attachment)
    snapshot = {"binding_uid": "vision", "base_url": "https://vision.invalid"}
    b.metadata.save_documents(
        CardMetadata,
        b.source,
        [
            {
                "attachment_uid": attachment.get_uid(),
                "status": status,
                "generation": "old-processing",
                "vision_config": snapshot,
                "content": {},
            }
        ],
    )
    send = Mock(side_effect=RuntimeError("broker unavailable"))
    monkeypatch.setattr(Broker.celery, "send_task", send)
    monkeypatch.setattr(b.metadata, "publish_update", Mock())
    assert move(b)["moved"]
    current = b.metadata.load_documents(CardMetadata, b.source)[0]
    assert current["status"] == "pending" and current["location_reindex_pending"]
    assert current["generation"] != "old-processing" and current["vision_config"] == snapshot
    assert not b.metadata.claim_document(
        CardMetadata, b.source, attachment.get_uid(), attachment.filename, generation="old-processing"
    )
    send.side_effect = None
    send.reset_mock()
    assert b.metadata.recover_card_location_embeddings(limit=1) == 1
    assert send.call_args.args[0] == "langboard_shared.tasks.docling.DoclingMetadataTask.index_card_attachment"
    convert = Mock(return_value="Recovered at destination")
    monkeypatch.setattr(task, "_convert_to_markdown", convert)
    monkeypatch.setattr(task, "find_spec", lambda _: True)
    monkeypatch.setattr(task, "NamedTemporaryFile", lambda **kwargs: NamedTemporaryFile(dir=tmp_path, **kwargs))
    service = SimpleNamespace(
        card=b.service,
        docling_metadata=b.metadata,
        card_attachment=SimpleNamespace(
            get_by_id_like=lambda uid: rows(CardAttachment, CardAttachment.id == InfraHelper.convert_id(uid))[0]
        ),
        project=SimpleNamespace(
            get_by_id_like=lambda uid: rows(type(b.destination), type(b.destination).id == InfraHelper.convert_id(uid))[
                0
            ]
        ),
        internal_bot=SimpleNamespace(
            get_current_by_id_like=lambda _: SimpleNamespace(
                bot_type=InternalBotType.DocumentVision, value=json.dumps({"base_url": "https://vision.invalid"})
            )
        ),
    )
    task._index_card_attachment(service, attachment, generation="old-processing")
    convert.assert_not_called()
    task._index_card_attachment(service, attachment, generation=current["generation"])
    completed = b.metadata.load_documents(CardMetadata, b.source)[0]
    assert completed["status"] == "indexed" and "location_reindex_pending" not in completed
    assert completed["content"]["markdown"] == "Recovered at destination"
    assert rows(Card, Card.id == b.source.id)[0].project_id == b.destination.id
    assert rows(CardAttachment)[0].document_text == "Recovered at destination"
    task._index_card_attachment(service, attachment, generation=current["generation"])
    convert.assert_called_once()
    assert b.metadata.recover_card_location_embeddings(limit=1) == 0


@pytest.mark.parametrize("mirror_board", ["postgresql-test"], indirect=True)
def test_postgresql_move_retry_and_native_dependency_readiness_are_one_transaction(move_board, monkeypatch):
    from langboard_shared.tasks.webhooks import ExecutionOutboxTask
    from langboard_shared.tasks.webhooks.ExecutionReadinessUow import _READY
    from sqlalchemy import select, text

    b = move_board
    WorkflowStageDefinition.__table__.create(b.engine)
    with b.engine.begin() as connection:
        for statement in (
            "CREATE TABLE webhook_setting(id bigint PRIMARY KEY, secret_id bigint, events jsonb)",
            """CREATE TABLE project_execution_binding(id bigint PRIMARY KEY, project_id bigint,
                updated_at timestamptz, is_enabled boolean, prerequisite_relationship_type_id bigint,
                events jsonb, webhook_id bigint, column_semantic_ids jsonb, webhook_uid text)""",
            "CREATE TABLE card_execution_generation(card_id bigint PRIMARY KEY, execution_generation bigint)",
            """CREATE TABLE execution_outbox(id uuid PRIMARY KEY, project_id bigint, card_id bigint,
                execution_generation bigint, occurred_at timestamptz, event_type text,
                payload_json jsonb, state text DEFAULT 'pending', last_error text)""",
        ):
            connection.execute(text(statement))
    monkeypatch.setattr(ExecutionOutboxTask, "execution_outbox_task", Mock())
    with DbSession.use(readonly=False) as db:
        db.insert(WorkflowStageDefinition(key="move-ready", name="Ready"))
        db.insert(WorkflowStageDefinition(key="move-completed", name="Completed", counts_as_completed=True))
        db.exec(
            update(ProjectColumn)
            .where(ProjectColumn.id == b.source.project_column_id)
            .values(workflow_stage="move-ready")
        )
        db.exec(update(ProjectColumn).where(ProjectColumn.id == b.column.id).values(workflow_stage="move-completed"))
        kind = GlobalCardRelationshipType(parent_name="Blocks", child_name="Blocked by", machine_semantic="blocks")
        db.insert(kind)
        child = Card(
            project_id=b.source_board.id,
            project_column_id=b.source.project_column_id,
            title="Dependent stays on original board",
            visibility="SHARED",
        )
        db.insert(child)
        db.insert(CardRelationship(card_id_parent=b.source.id, card_id_child=child.id, relationship_type_id=kind.id))
        db.exec(text("INSERT INTO webhook_setting VALUES (1, 1, '[\"io.langboard.work.ready.v1\"]'::jsonb)"))
        db.exec(
            text("""INSERT INTO project_execution_binding VALUES
            (1, :project_id, now(), true, :kind_id, '["io.langboard.work.ready.v1"]'::jsonb,
             1, CAST(:columns AS jsonb), 'test-webhook')"""),
            params={
                "project_id": b.source_board.id,
                "kind_id": kind.id,
                "columns": json.dumps({str(b.source.project_column_id): "ready"}),
            },
        )
        assert not db.exec(_READY, params={"card_id": child.id}).first()[0]
        assert card_dependency_state(db, b.source_board.id, child.id)["state"] == "blocked"
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _: move(b), range(2)))
    assert sorted(result["moved"] for result in outcomes) == [False, True]
    assert len(rows(ProjectActivity)) == 2

    current = rows(Card, Card.id == b.source.id)[0]
    assert current.project_id == b.destination.id and current.workflow_stage_entered_key == "move-completed"
    with DbSession.use(readonly=False) as db:
        assert db.exec(_READY, params={"card_id": child.id}).first()[0]
        assert card_dependency_state(db, b.source_board.id, child.id)["state"] == "clear"
        assert db.exec(
            select(text("execution_generation"))
            .select_from(text("card_execution_generation"))
            .where(text("card_id = :id")),
            params={"id": child.id},
        ).all() == [(1,)]
        events = db.exec(
            select(text("project_id, card_id, execution_generation")).select_from(text("execution_outbox"))
        ).all()
        assert events == [(b.source_board.id, child.id, 1)]
    ExecutionOutboxTask.execution_outbox_task.assert_called_once()


def test_relationship_visible_limit_scans_hidden_prefix_in_bounded_keyset_batches(move_board, monkeypatch):
    b = move_board
    with DbSession.use(readonly=False) as db:
        hidden = Card(
            project_id=b.source_board.id,
            project_column_id=b.source.project_column_id,
            title="Hidden prerequisite",
            visibility="PRIVATE",
            owner_user_id=b.outsider.id,
            created_by_user_id=b.outsider.id,
        )
        visible = Card(
            project_id=b.source_board.id,
            project_column_id=b.source.project_column_id,
            title="Visible prerequisite",
            visibility="SHARED",
        )
        db.insert(hidden)
        db.insert(visible)
        # Multiple edge types/endpoints are valid; distinct rows deliberately
        # place more hidden edges than the native 101-row overflow probe first.
        for index in range(205):
            peer = hidden if index < 103 else visible
            edge_kind = GlobalCardRelationshipType(parent_name=f"Parent {index}", child_name=f"Child {index}")
            db.insert(edge_kind)
            db.insert(
                CardRelationship(card_id_parent=peer.id, card_id_child=b.source.id, relationship_type_id=edge_kind.id)
            )
    repository = b.service.repo.card_relationship
    original = repository.get_incident_by_cards
    sizes = []

    def batch(*args, **kwargs):
        result = original(*args, **kwargs)
        sizes.append((kwargs["limit"], len(result), kwargs["after_id"]))
        return result

    monkeypatch.setattr(repository, "get_incident_by_cards", batch)
    context = b.service.resolve_visibility_context(b.source_board, b.actor)[1]
    result = b.relationships.get_api_list_by_card(b.source, context=context, limit=101)
    assert len(result) == 101  # MCP must observe overflow beyond its 100 visible rows.
    assert all(limit <= 100 and count <= 100 for limit, count, _ in sizes)
    assert len(sizes) == 3 and sizes[0][2] == 0 and sizes[1][2] > sizes[0][2]
    assert all(row["parent_card_uid"] == visible.get_uid() for row in result)
