"""Persisted CLONE behavior, independent file provenance and current authority."""

from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4
import pytest
from langboard_shared.core.db import DbSession, EditorContentModel, SqlBuilder
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.core.storage import Storage, StorageName
from langboard_shared.core.storage.LocalStorage import LocalStorage
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import (
    AttachmentStorageObject,
    AttachmentStorageReference,
    Card,
    CardAppOwnership,
    CardAssignedProjectLabel,
    CardAssignedUser,
    CardAttachment,
    CardComment,
    CardContentBlock,
    CardRelationship,
    Checkitem,
    CheckitemTimerRecord,
    Checklist,
    GlobalCardRelationshipType,
    GlobalLabel,
    ProjectActivity,
    ProjectAssignedUser,
    ProjectLabel,
    ProjectRole,
    User,
)
from langboard_shared.domain.models.Checkitem import CheckitemStatus
from langboard_shared.domain.models.ProjectActivity import ProjectActivityType
from langboard_shared.domain.services.factory.CardAttachmentService import CardAttachmentService
from langboard_shared.Env import Env
from langboard_shared.tasks.activities import CardActivityTask
from sqlalchemy import delete, update
from test_card_mirrors import mirror_board as mirror_board


@pytest.fixture
def clone_board(mirror_board, monkeypatch, tmp_path):  # noqa: F811
    b = mirror_board
    with b.engine.begin() as connection:
        # Keep schema setup atomic in SQLite legacy mode as well as PostgreSQL.
        if b.engine.dialect.name == "sqlite":
            connection.exec_driver_sql("BEGIN")
        for model in (
            Checklist,
            Checkitem,
            CardAttachment,
            CardContentBlock,
            CardAssignedUser,
            GlobalLabel,
            GlobalCardRelationshipType,
            ProjectLabel,
            CardAssignedProjectLabel,
            CheckitemTimerRecord,
            CardComment,
            CardRelationship,
        ):
            model.__table__.create(connection)
    monkeypatch.setattr(type(Env), "LOCAL_STORAGE_DIR", property(lambda _: tmp_path / "objects"))
    monkeypatch.setattr(Storage, "_storages", {"local": LocalStorage()})
    resolver = CardAttachmentService(lambda _: None, lambda _: None, None)
    b.attachment_service = SimpleNamespace(dispatch_created=Mock(), resolve_file_owner=resolver.resolve_file_owner)
    original_get_service = b.service._raw_get_service
    b.service._raw_get_service = (
        lambda kind: b.attachment_service if kind is CardAttachmentService else original_get_service(kind)
    )
    b.request_uid = str(uuid4())
    return b


def clone(b, **kwargs):
    return b.service.clone_cross_board_card(
        b.actor, b.source_board, b.source, b.destination, b.column, request_uid=b.request_uid, **kwargs
    )


def rows(model, *conditions):
    with DbSession.use(readonly=False) as db:
        return db.exec(SqlBuilder.select.table(model).where(*conditions)).all()


def add_reader(b, *, source=False):
    with DbSession.use(readonly=False) as db:
        user = User(
            firstname="New",
            lastname="Reader",
            email="new@example.invalid",
            password="test-only",
            activated_at=SafeDateTime.now(),
        )
        db.insert(user)
        for board in [b.destination] + ([b.source_board] if source else []):
            db.insert(ProjectAssignedUser(project_id=board.id, user_id=user.id))
            db.insert(ProjectRole(project_id=board.id, user_id=user.id, actions=["read"]))
    return user


def test_copy_is_native_independent_reset_and_retry_safe(clone_board):
    b = clone_board
    with DbSession.use(readonly=False) as db:
        b.source.next_action = "Request evidence"
        db.update(b.source)
        checklist = Checklist(card_id=b.source.id, title="Steps", order=7, is_checked=True)
        db.insert(checklist)
        db.insert(
            Checkitem(
                checklist_id=checklist.id,
                title="Description and item",
                order=3,
                is_checked=True,
                user_id=b.actor.id,
                cardified_id=b.source.id,
                accumulated_seconds=130,
                status=CheckitemStatus.Started,
            )
        )
        db.insert(
            CardContentBlock(
                card_id=b.source.id,
                block_type="paragraph",
                order=2,
                payload={"content": "Structured description"},
                revision=6,
            )
        )
    result = clone(b)
    assert result["created"] is True
    copied = next(card for card in rows(Card) if card.id != b.source.id)
    assert copied.project_column_id == b.column.id and copied.project_id == b.destination.id
    assert copied.title == b.source.title and copied.description == b.source.description
    assert copied.next_action == result["card"]["next_action"] == "Request evidence"
    assert copied.source_type is None and copied.source_uid is None and copied.visibility == "SHARED"
    copied_checklist = rows(Checklist, Checklist.card_id == copied.id)[0]
    assert copied_checklist.title == "Steps" and copied_checklist.order == 7 and not copied_checklist.is_checked
    item = rows(Checkitem, Checkitem.checklist_id == copied_checklist.id)[0]
    assert item.title == "Description and item" and item.order == 3
    assert not item.is_checked and item.user_id is None and item.cardified_id is None
    assert item.accumulated_seconds == 0 and item.status == CheckitemStatus.Stopped
    assert rows(CheckitemTimerRecord) == [] and rows(CardRelationship) == [] and rows(CardComment) == []
    block = rows(CardContentBlock, CardContentBlock.card_id == copied.id)[0]
    assert block.payload == {"content": "Structured description"} and block.order == 2 and block.revision == 1
    activities = rows(ProjectActivity)
    assert len(activities) == 1 and activities[0].activity_type == ProjectActivityType.CardCloned
    history = activities[0].activity_history
    assert history["source"] == {
        "type": "project_card",
        "uid": b.source.get_uid(),
        "project_uid": b.source_board.get_uid(),
        "url": f"/board/{b.source_board.get_uid()}/{b.source.get_uid()}",
    }
    assert "project" in history and "card" in history
    again = clone(b)
    assert again["created"] is False and again["card"]["uid"] == result["card"]["uid"]
    assert len(rows(Card)) == 2 and len(rows(ProjectActivity)) == 1
    b.published.assert_called_once()
    b.request_uid = str(uuid4())
    assert clone(b)["created"] is True and len(rows(Card)) == 3


def test_same_audience_processes_immediately_and_new_reader_requires_bound_confirmation(clone_board):
    b = clone_board
    user = add_reader(b)
    preview = clone(b)
    assert preview["confirmation_required"] is True and preview["new_reader_uids"] == [user.get_uid()]
    assert len(rows(Card)) == 1 and rows(ProjectActivity) == []
    assert clone(b, confirmation_token="invented")["confirmation_required"] is True
    with b.engine.begin() as db:
        db.execute(update(Card).where(Card.id == b.source.id).values(title="Current body state"))
    refreshed = clone(b, confirmation_token=preview["confirmation_token"])
    assert refreshed["confirmation_required"] and refreshed["confirmation_token"] != preview["confirmation_token"]
    assert clone(b, confirmation_token=refreshed["confirmation_token"])["created"]


@pytest.mark.parametrize("state", ["membership", "role", "source_visibility", "attachment", "checklist"])
def test_confirmation_changes_when_current_reader_or_copy_state_changes(clone_board, state):
    b = clone_board
    user = add_reader(b)
    if state == "source_visibility":
        b.service._resolve_internal_access = lambda _: True
    preview = clone(b)
    with DbSession.use(readonly=False) as db:
        if state == "membership":
            db.insert(ProjectAssignedUser(project_id=b.source_board.id, user_id=user.id))
            db.insert(ProjectRole(project_id=b.source_board.id, user_id=user.id, actions=["read"]))
        elif state == "role":
            db.exec(update(ProjectRole).where(ProjectRole.project_id == b.destination.id).values(actions=[]))
        elif state == "source_visibility":
            db.exec(update(Card).where(Card.id == b.source.id).values(visibility="INTERNAL"))
        elif state == "attachment":
            file = Storage.upload_named(BytesIO(b"bytes"), StorageName.CardAttachment, "one.bin", "one.bin")
            db.insert(CardAttachment(card_id=b.source.id, user_id=b.actor.id, filename="one", file=file))
        else:
            db.insert(Checklist(card_id=b.source.id, title="new checklist"))
    current = clone(b, confirmation_token=preview["confirmation_token"])
    if state in ("membership", "role"):
        assert current["created"] is True  # No new readers remain; no confirmation is necessary.
    else:
        assert current["confirmation_required"] and current["confirmation_token"] != preview["confirmation_token"]
        assert len(rows(Card)) == 1


@pytest.mark.parametrize(
    "revoked",
    [
        "source_role",
        "source_membership",
        "destination_write",
        "destination_read",
        "destination_member",
        "account",
        "deleted",
        "app",
        "mirror",
        "column",
    ],
)
def test_rechecks_current_authority_and_app_exclusion_before_writing(clone_board, revoked):
    b = clone_board
    with DbSession.use(readonly=False) as db:
        if revoked == "source_role":
            db.exec(delete(ProjectRole).where(ProjectRole.project_id == b.source_board.id))
        elif revoked == "source_membership":
            db.exec(delete(ProjectAssignedUser).where(ProjectAssignedUser.project_id == b.source_board.id))
        elif revoked.startswith("destination"):
            db.exec(
                update(type(b.destination))
                .where(type(b.destination).id == b.destination.id)
                .values(owner_id=b.outsider.id)
            )
            if revoked != "destination_member":
                db.insert(ProjectAssignedUser(project_id=b.destination.id, user_id=b.actor.id))
                db.insert(
                    ProjectRole(
                        project_id=b.destination.id,
                        user_id=b.actor.id,
                        actions=["read"] if revoked == "destination_write" else ["card_write"],
                    )
                )
        elif revoked == "account":
            db.exec(update(User).where(User.id == b.actor.id).values(activated_at=None))
        elif revoked == "deleted":
            db.exec(update(Card).where(Card.id == b.source.id).values(deleted_at=SafeDateTime.now()))
        elif revoked == "app":
            db.insert(CardAppOwnership(card_id=b.source.id, app_key="owned"))
        elif revoked == "mirror":
            db.exec(update(Card).where(Card.id == b.source.id).values(source_type="project_card", source_uid="other"))
        else:
            db.exec(update(type(b.column)).where(type(b.column).id == b.column.id).values(is_archive=True))
    assert clone(b) is None
    assert clone(b, channel=CollaborationChannel.HumanUI) is None
    assert len(rows(Card)) == (0 if revoked == "deleted" else 1) and rows(ProjectActivity) == []
    b.published.assert_not_called()


def test_private_and_internal_visibility_are_preserved_without_widening(clone_board):
    b = clone_board
    with b.engine.begin() as db:
        db.execute(
            update(Card)
            .where(Card.id == b.source.id)
            .values(visibility="PRIVATE", owner_user_id=b.actor.id, created_by_user_id=b.actor.id)
        )
    assert clone(b) is None
    result = clone(b, channel=CollaborationChannel.HumanUI)
    assert result["created"] and result["card"]["visibility"] == "PRIVATE"
    copied = next(card for card in rows(Card) if card.id != b.source.id)
    assert copied.owner_user_id == b.actor.id


def test_attachment_copy_has_new_identity_equal_bytes_native_owner_and_rewritten_body(clone_board, monkeypatch):
    b = clone_board
    physical = Mock(wraps=Storage._storages["local"].upload_immutable)
    monkeypatch.setattr(Storage._storages["local"], "upload_immutable", physical)
    file = Storage.upload_named(
        BytesIO(b"native attachment bytes"), StorageName.CardAttachment, "original.bin", "report.bin"
    )
    with DbSession.use(readonly=False) as db:
        attachment = CardAttachment(
            card_id=b.source.id,
            user_id=b.outsider.id,
            filename="Display name",
            file=file,
            order=4,
            document_text="Already transcribed text",
        )
        db.insert(attachment)
        db.exec(
            update(Card)
            .where(Card.id == b.source.id)
            .values(description=EditorContentModel(content=f"[report]({file.path})"))
        )
    from langboard_shared.core.broker import Broker

    send = Mock(side_effect=AssertionError("Clone must never invoke document providers"))
    monkeypatch.setattr(Broker.celery, "send_task", send)
    result = clone(b)
    copied = next(row for row in rows(CardAttachment) if row.id != attachment.id)
    assert copied.file.filename != file.filename and copied.file.path != file.path
    assert copied.file.original_filename == "report.bin" and copied.filename == "Display name" and copied.order == 4
    assert Storage.get_file(copied.file) == Storage.get_file(file) == b"native attachment bytes"
    resolver = CardAttachmentService(lambda _: None, lambda _: None, None)
    assert resolver.resolve_file_owner(file.storage_type, file.storage_name, file.filename).id == attachment.id
    assert (
        resolver.resolve_file_owner(copied.file.storage_type, copied.file.storage_name, copied.file.filename).id
        == copied.id
    )
    assert physical.call_count == len(rows(AttachmentStorageObject)) == 1
    assert len(rows(AttachmentStorageReference)) == 2
    assert len(list((Env.LOCAL_STORAGE_DIR / "attachment_objects").iterdir())) == 1
    assert copied.document_text == "Already transcribed text"
    assert result["card"]["description"]["content"] == f"[report]({copied.file.path})"
    send.assert_not_called()


def test_attachment_failure_rolls_back_database_storage_and_socket(clone_board, monkeypatch):
    b = clone_board
    file = Storage.upload_named(BytesIO(b"rollback bytes"), StorageName.CardAttachment, "original.bin", "report.bin")
    with DbSession.use(readonly=False) as db:
        db.insert(CardAttachment(card_id=b.source.id, user_id=b.actor.id, filename="File", file=file))
    monkeypatch.setattr(CardActivityTask, "record_card_cloned", Mock(side_effect=RuntimeError("activity failed")))
    with pytest.raises(RuntimeError, match="activity failed"):
        clone(b)
    assert len(rows(Card)) == 1 and len(rows(CardAttachment)) == 1 and rows(ProjectActivity) == []
    assert len(rows(AttachmentStorageReference)) == len(rows(AttachmentStorageObject)) == 1
    assert len(list((Env.LOCAL_STORAGE_DIR / "attachment_objects").iterdir())) == 1
    assert Storage.get_file(file) == b"rollback bytes"
    b.published.assert_not_called()


def test_incompatible_members_and_local_labels_excluded_without_creating_resources(clone_board):
    b = clone_board
    with DbSession.use(readonly=False) as db:
        source_member = rows(ProjectAssignedUser)[0]
        target_member = ProjectAssignedUser(project_id=b.destination.id, user_id=b.actor.id)
        db.insert(target_member)
        db.insert(CardAssignedUser(card_id=b.source.id, user_id=b.actor.id, project_assigned_id=source_member.id))
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
        for label in (original_global, local):
            db.insert(CardAssignedProjectLabel(card_id=b.source.id, project_label_id=label.id))
    result = clone(b)
    assert result["excluded"] == {"member_uids": [b.outsider.get_uid()], "label_uids": [local.get_uid()]}
    copied_id = next(card.id for card in rows(Card) if card.id != b.source.id)
    assert rows(CardAssignedUser, CardAssignedUser.card_id == copied_id)[0].project_assigned_id == target_member.id
    assert (
        rows(CardAssignedProjectLabel, CardAssignedProjectLabel.card_id == copied_id)[0].project_label_id
        == target_global.id
    )
    assert len(rows(ProjectLabel)) == 3 and len(rows(ProjectAssignedUser)) == 3


def test_native_concurrent_retries_create_only_one_copy(clone_board):
    b = clone_board
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: clone(b), range(2)))
    assert sorted(result["created"] for result in results) == [False, True]
    assert results[0]["card"]["uid"] == results[1]["card"]["uid"]
    assert len(rows(Card)) == 2 and len(rows(ProjectActivity)) == 1
    b.published.assert_called_once()


def test_api_clone_preview_confirmation_creation_and_retry(clone_board):
    import json
    from langboard.routes.board.BoardCardCrossBoardApi import CrossBoardCardForm, cross_board_card

    b = clone_board
    add_reader(b)
    form = CrossBoardCardForm(
        source_project_uid=b.source_board.get_uid(),
        source_card_uid=b.source.get_uid(),
        destination_column_uid=b.column.get_uid(),
        operation="clone",
        request_uid=b.request_uid,
    )

    def call():
        return cross_board_card(
            b.destination.get_uid(), form, SimpleNamespace(scope={}), b.actor, SimpleNamespace(card=b.service)
        )

    preview = call()
    assert preview.status_code == 409
    form.confirmation_token = json.loads(preview.body)["confirmation_token"]
    created = call()
    assert created.status_code == 201 and call().status_code == 200


@pytest.mark.parametrize("mirror_board", ["postgresql-test"], indirect=True)
def test_postgresql_clone_serializes_native_request_retries(clone_board):
    test_native_concurrent_retries_create_only_one_copy(clone_board)


def test_reader_and_receipt_queries_remain_scoped_with_unrelated_accounts_and_history(clone_board, monkeypatch):
    from sqlalchemy import event

    b = clone_board
    statements = []

    def record(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append((statement, parameters))

    event.listen(b.engine, "before_cursor_execute", record)
    try:
        clone(b)
        baseline_count = len(statements)
        with DbSession.use(readonly=False) as db:
            decoys = [
                User(
                    firstname="Unrelated",
                    lastname=str(i),
                    email=f"decoy{i}@example.invalid",
                    password="test-only",
                    activated_at=SafeDateTime.now(),
                )
                for i in range(40)
            ]
            for user in decoys:
                db.insert(user)
            for _ in range(40):
                db.insert(
                    ProjectActivity(
                        project_id=b.destination.id,
                        card_id=b.source.id,
                        user_id=b.actor.id,
                        activity_type=ProjectActivityType.CardCloned,
                        activity_history={"clone_request_uid": str(uuid4())},
                    )
                )
        classified = []
        monkeypatch.setattr(b.service, "_resolve_internal_access", lambda user: classified.append(user.id) or False)
        statements.clear()
        b.request_uid = str(uuid4())
        assert clone(b)["created"]
        assert len(statements) == baseline_count
        assert not {user.id for user in decoys}.intersection(classified)
        receipt_selects = [sql for sql, _ in statements if "project_activity" in sql]
        assert len(receipt_selects) == 1 and "JSON_EXTRACT" in receipt_selects[0] and "LIMIT" in receipt_selects[0]
    finally:
        event.remove(b.engine, "before_cursor_execute", record)


def test_native_clone_processing_reuses_embedding_cache_with_distinct_search_provenance(
    clone_board, monkeypatch, tmp_path
):
    import asyncio
    import json
    from hashlib import sha256
    from langboard_shared.core.broker import Broker
    from langboard_shared.domain.models import (
        CardDocumentArtifact,
        CardMetadata,
        DocumentEmbeddingCache,
        DocumentEmbeddingCacheReference,
        KeywordSearchConfiguration,
    )
    from langboard_shared.domain.models.DocumentExtractionCache import (
        DocumentExtractionCache,
        DocumentExtractionCacheReference,
    )
    from langboard_shared.domain.models.InternalBot import InternalBotType
    from langboard_shared.domain.services.factory.DoclingMetadataService import DoclingMetadataService
    from langboard_shared.helpers import InfraHelper
    from langboard_shared.infrastructure.repositories.factory.MetadataRepository import MetadataRepository
    from langboard_shared.publishers import CardAttachmentPublisher
    from langboard_shared.tasks.activities import CardAttachmentActivityTask
    from langboard_shared.tasks.docling import DocumentEmbeddingTask as embedding_task
    from langboard_shared.tasks.docling.DocumentExtractionCache import cached_document_extraction
    from langchain_core.embeddings import Embeddings

    b = clone_board
    for model in (
        CardMetadata,
        CardDocumentArtifact,
        DocumentEmbeddingCache,
        DocumentEmbeddingCacheReference,
        DocumentExtractionCache,
        DocumentExtractionCacheReference,
        KeywordSearchConfiguration,
    ):
        model.__table__.create(b.engine)
    monkeypatch.setattr(type(Env), "DATA_DIR", property(lambda _: tmp_path / "data"))
    metadata = DoclingMetadataService(
        lambda _: None, lambda _: None, SimpleNamespace(metadata=MetadataRepository(None, None))
    )
    monkeypatch.setattr(metadata, "publish_update", Mock())
    config = json.dumps(
        {
            "agent_llm": "OpenAI Compatible",
            "base_url": "https://fixture.invalid/v1",
            "model_name": "embedding",
            "retrieval": {"enabled": True, "store": "sqlite", "dimensions": 3},
        }
    )
    binding = SimpleNamespace(
        value=config, get_uid=lambda: "embedding-binding", bot_type=InternalBotType.DocumentEmbedding
    )
    vision = SimpleNamespace(
        value=json.dumps({"base_url": "https://fixture.invalid/v1", "model": "vision"}),
        get_uid=lambda: "vision-binding",
    )
    internal = SimpleNamespace(
        get_document_vision_binding=lambda: vision,
        is_document_processing_enabled=lambda: True,
        get_document_embedding_binding=lambda: binding,
        get_current_by_id_like=lambda _: binding,
    )
    attachment_service = CardAttachmentService(
        lambda kind: metadata if kind is DoclingMetadataService else internal, lambda _: None, None
    )
    b.attachment_service = attachment_service
    queued = Mock()
    monkeypatch.setattr(Broker.celery, "send_task", queued)
    monkeypatch.setattr(CardAttachmentPublisher, "uploaded", Mock())
    monkeypatch.setattr(CardAttachmentActivityTask, "card_attachment_uploaded", Mock())
    file_bytes = b"same file content"
    file = Storage.upload_named(BytesIO(file_bytes), StorageName.CardAttachment, "source.md", "report.md")
    with DbSession.use(readonly=False) as db:
        original = CardAttachment(card_id=b.source.id, user_id=b.actor.id, filename="report.md", file=file)
        db.insert(original)
    attachment_service.dispatch_created(b.actor, b.source_board, b.source, original, include_bot=False)
    assert queued.call_args.args[0].endswith("DoclingMetadataTask.index_card_attachment")

    class Provider(Embeddings):
        def __init__(self):
            self.calls = []

        def embed_documents(self, texts):
            assert not DbSession.has_active_transaction()
            self.calls.append(texts)
            return [[float(len(text)), 2.0, 3.0] for text in texts]

        def embed_query(self, text):
            return [1.0, 2.0, 3.0]

    provider = Provider()
    monkeypatch.setattr(embedding_task, "create_document_embeddings", lambda *_: provider)

    def current_card(value):
        return next(card for card in rows(Card) if card.id == InfraHelper.convert_id(value))

    task_service = SimpleNamespace(
        card_attachment=attachment_service,
        card=SimpleNamespace(get_by_id_like=current_card),
        project=SimpleNamespace(
            get_by_id_like=lambda value: b.source_board
            if InfraHelper.convert_id(value) == b.source_board.id
            else b.destination
        ),
        docling_metadata=metadata,
        internal_bot=internal,
    )

    def transcribe_and_embed(card, attachment):
        # The worker's provider transcription output is a fixture; queueing, metadata,
        # splitting, cache, vector store, generation publication and provenance are native.
        document = metadata.get_document_by_attachment_uid(CardMetadata, card, attachment.get_uid())
        assert document["status"] == "pending"
        with cached_document_extraction(
            binding_uid=vision.get_uid(),
            content_hash=sha256(file_bytes).hexdigest(),
            vision_config=document["vision_config"],
            filename=attachment.filename,
            timeout_seconds=1,
        ) as extraction:
            content = extraction.get_or_convert(lambda: {"markdown": "Identical document transcription"})
            assert metadata.mark_document_indexed(
                CardMetadata,
                card,
                attachment.get_uid(),
                "markdown",
                content_hash=sha256(file_bytes).hexdigest(),
                content=content,
                generation=document["generation"],
                extraction_cache_token=extraction.token,
            )
            extraction.retain()
        asyncio.run(embedding_task.embed_transcription(task_service, attachment.get_uid(), document["generation"]))
        return metadata.get_document_by_attachment_uid(CardMetadata, card, attachment.get_uid())

    original_document = transcribe_and_embed(b.source, original)
    assert original_document["embedding"]["status"] == "indexed", original_document["embedding"]
    assert len(provider.calls) == 1
    queued.reset_mock()
    result = clone(b)
    assert result["created"]
    copied = next(attachment for attachment in rows(CardAttachment) if attachment.id != original.id)
    copied_card = current_card(copied.card_id)
    assert queued.call_count == 1 and queued.call_args.args[0].endswith("DoclingMetadataTask.index_card_attachment")
    copied_document = transcribe_and_embed(copied_card, copied)
    assert copied_document["embedding"]["status"] == "indexed", copied_document["embedding"]
    assert len(provider.calls) == 1
    original_pointer = original_document["embedding"]["pointer"]
    copied_pointer = copied_document["embedding"]["pointer"]
    assert original_pointer["board_uid"] == b.source_board.get_uid()
    assert copied_pointer["board_uid"] == b.destination.get_uid()
    assert original_pointer["attachment_uid"] == original.get_uid()
    assert copied_pointer["attachment_uid"] == copied.get_uid()
    assert original_pointer["card_uid"] != copied_pointer["card_uid"]
    assert original_pointer["storage"]["cache_use_token"] != copied_pointer["storage"]["cache_use_token"]
    assert len(rows(DocumentEmbeddingCache)) == 1 and len(rows(DocumentEmbeddingCacheReference)) == 2


def test_ambiguous_native_source_file_provenance_cannot_be_copied(clone_board):
    b = clone_board
    file = Storage.upload_named(BytesIO(b"ambiguous bytes"), StorageName.CardAttachment, "same.bin", "same.bin")
    with DbSession.use(readonly=False) as db:
        for _ in range(2):
            db.insert(CardAttachment(card_id=b.source.id, user_id=b.actor.id, filename="Same", file=file))
    with pytest.raises(ValueError, match="provenance"):
        clone(b)
    assert len(rows(Card)) == 1 and rows(ProjectActivity) == []
    assert len(list((Env.LOCAL_STORAGE_DIR / "attachment_objects").iterdir())) == 1
    assert len(rows(AttachmentStorageReference)) == 1
    assert Storage.get_file(file) == b"ambiguous bytes"
    b.attachment_service.dispatch_created.assert_not_called()
    b.published.assert_not_called()


def test_destination_owner_without_native_membership_excludes_assignment_without_auto_creation(clone_board):
    b = clone_board
    before = rows(ProjectAssignedUser)
    source_member = next(member for member in before if member.user_id == b.actor.id)
    with DbSession.use(readonly=False) as db:
        db.insert(CardAssignedUser(card_id=b.source.id, user_id=b.actor.id, project_assigned_id=source_member.id))
    result = clone(b)
    assert result["created"]
    assert result["excluded"]["member_uids"] == [b.actor.get_uid()]
    assert result["card"]["member_uids"] == []
    assert len(rows(ProjectAssignedUser)) == len(before)
    assert rows(ProjectAssignedUser, ProjectAssignedUser.project_id == b.destination.id) == []
