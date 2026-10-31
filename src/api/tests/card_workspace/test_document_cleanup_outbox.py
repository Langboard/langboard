"""Cleanup request durability, bounded recovery and lease fencing on SQLite/PostgreSQL."""

import asyncio
import runpy
from datetime import timedelta
from unittest.mock import Mock
import pytest
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import CardAttachment, DocumentVectorCleanup
from langboard_shared.tasks.docling import DocumentCleanupTask as outbox
from langboard_shared.tasks.docling import DocumentEmbeddingTask as embedding
from sqlalchemy import update
from test_document_mcp_read import binding_store  # noqa: F401


@pytest.fixture
def cleanup_store(binding_store, monkeypatch):  # noqa: F811
    DocumentVectorCleanup.__table__.create(binding_store)
    send = Mock(side_effect=RuntimeError("broker unavailable fixture-secret"))
    monkeypatch.setattr(outbox.Broker.celery, "send_task", send)
    return send


def pointer():
    return {
        "embedding_fingerprint": "a" * 64,
        "generation": "old",
        "chunk_ids": ["old-1"],
        "storage": {
            "type": "qdrant",
            "endpoint": "https://fixture.invalid",
            "binding_uid": "binding",
            "dimensions": 3,
            "collection_prefix": "recorded_documents",
        },
    }


def pending():
    with DbSession.use(readonly=False) as db:
        return db.exec(SqlBuilder.select.table(DocumentVectorCleanup)).all()


def record():
    with DbSession.atomic() as db:
        outbox.record_cleanup(db, pointer())
    return pending()[0]


def test_commit_survives_broker_failure_and_rollback_never_dispatches(cleanup_store):
    with pytest.raises(RuntimeError, match="rollback"):
        with DbSession.atomic() as db:
            outbox.record_cleanup(db, pointer())
            cleanup_store.assert_not_called()
            raise RuntimeError("rollback")
    assert pending() == []
    cleanup_store.assert_not_called()
    event = record()
    assert event.pointer == pointer() and event.attempt_count == 0
    cleanup_store.assert_called_once_with(outbox.TASK_NAME, args=[int(event.id)])
    cleanup_store.side_effect = None
    assert outbox.recover_cleanup() == 1
    assert cleanup_store.call_count == 2
    assert outbox.process_cleanup(0) is False


def test_module_entrypoint_recovers_committed_requests(cleanup_store):
    event = record()
    cleanup_store.reset_mock(side_effect=True)
    with pytest.warns(RuntimeWarning, match="found in sys.modules"):
        runpy.run_module(outbox.__name__, run_name="__main__")
    cleanup_store.assert_called_once_with(outbox.TASK_NAME, args=[int(event.id)])
    assert len(pending()) == 1


def test_failure_backoff_keeps_pointer_and_sanitizes_error(cleanup_store, monkeypatch):
    event = record()
    remove = Mock(side_effect=RuntimeError("fixture-secret private document"))
    monkeypatch.setattr(embedding, "remove_recorded_embedding", remove)
    before = SafeDateTime.now()
    assert outbox.process_cleanup(int(event.id)) is False
    retry = pending()[0]
    assert retry.pointer == pointer() and retry.attempt_count == 1 and retry.claim_token is None
    assert retry.last_error == "cleanup_failed_check_original_connection"
    assert retry.due_at.replace(tzinfo=before.tzinfo) > before
    assert outbox.process_cleanup(int(event.id)) is False
    assert outbox.recover_cleanup() == 0
    remove.assert_called_once()
    with DbSession.atomic() as db:
        db.exec(
            update(DocumentVectorCleanup)
            .where(DocumentVectorCleanup.id == event.id)
            .values(due_at=before - timedelta(seconds=1))
        )
    remove.side_effect = None
    assert outbox.process_cleanup(int(event.id)) is True
    assert pending() == []
    assert outbox.process_cleanup(int(event.id)) is False
    assert remove.call_count == 2


def test_negative_vector_delete_ack_preserves_cleanup_until_retry(cleanup_store, monkeypatch):
    from langboard_shared.tasks.docling.DocumentVectorStore import delete_vector_generation
    from langchain_qdrant import QdrantVectorStore
    from qdrant_client.models import UpdateResult, UpdateStatus

    # Exercise the official integration's bool return for a valid but incomplete
    # provider operation, then its completed retry, through the durable outbox.
    client = Mock()
    client.delete.side_effect = [
        UpdateResult(operation_id=1, status=UpdateStatus.ACKNOWLEDGED),
        UpdateResult(operation_id=1, status=UpdateStatus.COMPLETED),
    ]
    store = QdrantVectorStore(
        client=client, collection_name="recorded_documents", validate_embeddings=False, validate_collection_config=False
    )
    monkeypatch.setattr(embedding, "remove_recorded_embedding", lambda value: delete_vector_generation(store, value))
    event = record()
    assert outbox.process_cleanup(int(event.id)) is False
    retry = pending()[0]
    assert retry.pointer == pointer() and retry.attempt_count == 1 and retry.claim_token is None
    assert retry.last_error == "cleanup_failed_check_original_connection"
    with DbSession.atomic() as db:
        db.exec(
            update(DocumentVectorCleanup)
            .where(DocumentVectorCleanup.id == event.id)
            .values(due_at=SafeDateTime.now() - timedelta(seconds=1))
        )
    assert outbox.process_cleanup(int(event.id)) is True
    assert pending() == []
    assert client.delete.call_count == 2
    for call in client.delete.call_args_list:
        assert call.kwargs == {"collection_name": "recorded_documents", "points_selector": ["old-1"]}


def test_lease_fences_duplicate_delivery_and_recovers_worker_loss(cleanup_store, monkeypatch):
    event = record()
    now = SafeDateTime.now()
    with DbSession.atomic() as db:
        db.exec(
            update(DocumentVectorCleanup)
            .where(DocumentVectorCleanup.id == event.id)
            .values(claim_token="lost-worker", due_at=now + timedelta(seconds=120))
        )
    remove = Mock()
    monkeypatch.setattr(embedding, "remove_recorded_embedding", remove)
    assert outbox.process_cleanup(int(event.id)) is False
    remove.assert_not_called()
    assert outbox.recover_cleanup() == 0
    with DbSession.atomic() as db:
        db.exec(
            update(DocumentVectorCleanup)
            .where(DocumentVectorCleanup.id == event.id)
            .values(due_at=now - timedelta(seconds=1))
        )
    assert outbox.process_cleanup(int(event.id)) is True
    remove.assert_called_once_with(pointer())
    assert pending() == []


def test_expired_worker_cannot_acknowledge_new_claim(cleanup_store, monkeypatch):
    event = record()

    def supersede(_pointer):
        with DbSession.atomic() as db:
            db.exec(
                update(DocumentVectorCleanup)
                .where(DocumentVectorCleanup.id == event.id)
                .values(claim_token="replacement-worker")
            )

    monkeypatch.setattr(embedding, "remove_recorded_embedding", supersede)
    assert outbox.process_cleanup(int(event.id)) is False
    assert pending()[0].claim_token == "replacement-worker"


def test_recovery_is_bounded_and_pointer_never_contains_credentials(cleanup_store):
    supplied = {**pointer(), "api_key": "fixture-secret"}
    supplied["storage"] = {**supplied["storage"], "external_api_key": "fixture-vector-secret"}
    with DbSession.atomic() as db:
        for _ in range(3):
            outbox.record_cleanup(db, supplied)
    cleanup_store.side_effect = None
    cleanup_store.reset_mock()
    assert outbox.recover_cleanup(limit=2) == 2
    assert cleanup_store.call_count == 2
    assert all(event.pointer == pointer() for event in pending())
    for value in (0, 101, True):
        with pytest.raises(ValueError, match="limit"):
            outbox.recover_cleanup(limit=value)


@pytest.mark.parametrize("kind", ["cleanup", "keyword"])
@pytest.mark.parametrize("delivery", ["accepted", "rejected", "claimed"])
def test_recovery_rotates_unclaimed_prefix_without_changing_worker_lease(
    cleanup_store,
    binding_store,  # noqa: F811
    monkeypatch,
    kind,
    delivery,
):
    from langboard_shared.domain.models import DocumentKeywordIndex
    from langboard_shared.tasks.docling import DocumentKeywordTask

    model, recover, enqueue = (
        (DocumentVectorCleanup, outbox.recover_cleanup, "enqueue_cleanup")
        if kind == "cleanup"
        else (DocumentKeywordIndex, DocumentKeywordTask.recover_keyword, "enqueue")
    )
    model.__table__.create(binding_store, checkfirst=True)
    due = SafeDateTime.now() - timedelta(hours=1)
    leased_until = SafeDateTime.now() + timedelta(hours=1)
    with DbSession.atomic() as db:
        for position in range(3):
            row = (
                model(pointer=pointer(), due_at=due)
                if kind == "cleanup"
                else model(
                    attachment_id=100 + position,
                    card_id=200,
                    project_id=300,
                    destination_fingerprint="fixture",
                    destination={"endpoint": "https://example.invalid", "index": "documents"},
                    source_generation="fixture-generation",
                    due_at=due,
                )
            )
            db.insert(row)
        db.exec(update(model).values(updated_at=due))
    dispatched = []

    def dispatch(uid):
        dispatched.append(uid)
        if delivery == "claimed":
            with DbSession.atomic() as db:
                db.exec(
                    update(model)
                    .where(model.id == uid)
                    .values(due_at=leased_until, claim_token="worker", attempt_count=1, updated_at=due)
                )
        return delivery != "rejected"

    monkeypatch.setattr(outbox if kind == "cleanup" else DocumentKeywordTask, enqueue, dispatch)
    for _ in range(3):
        assert recover(limit=1) == int(delivery != "rejected")
    with DbSession.use(readonly=False) as db:
        rows = db.exec(SqlBuilder.select.table(model).order_by(model.id)).all()
    assert dispatched == ([int(rows[0].id)] * 3 if delivery == "rejected" else [int(row.id) for row in rows])
    for row in rows:
        actual_due = row.due_at if row.due_at.tzinfo else row.due_at.replace(tzinfo=due.tzinfo)
        actual_updated = row.updated_at if row.updated_at.tzinfo else row.updated_at.replace(tzinfo=due.tzinfo)
        assert actual_due == (leased_until if delivery == "claimed" else due)
        assert (actual_updated > due) == (delivery == "accepted")
    assert all(row.claim_token == ("worker" if delivery == "claimed" else None) for row in rows)
    assert all(row.attempt_count == int(delivery == "claimed") for row in rows)


@pytest.fixture
def document_source(cleanup_store, binding_store, monkeypatch):  # noqa: F811
    from langboard_shared.core.db.DbEngine import DbEngine
    from langboard_shared.core.storage import FileModel
    from langboard_shared.domain.models import (
        Card,
        CardDocumentArtifact,
        CardMetadata,
        DocumentKeywordIndex,
        Organization,
        Project,
        ProjectAttachment,
        ProjectAttachmentDocument,
        ProjectColumn,
        User,
    )
    from langboard_shared.domain.services import DomainService

    for model in (
        User,
        Organization,
        Project,
        ProjectColumn,
        ProjectAttachment,
        ProjectAttachmentDocument,
        Card,
        CardAttachment,
        CardDocumentArtifact,
        CardMetadata,
        DocumentKeywordIndex,
    ):
        model.__table__.create(binding_store)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: binding_store)
    with DbSession.atomic() as db:
        owner = User(firstname="Fixture", lastname="Owner", email="cleanup@example.invalid", password="fixture-only")
        db.insert(owner)
        project = Project(owner_id=owner.id, title="Cleanup source")
        db.insert(project)
        column = ProjectColumn(project_id=project.id, name="Todo")
        db.insert(column)
        card = Card(project_id=project.id, project_column_id=column.id, title="Cleanup")
        db.insert(card)
        attachment = CardAttachment(
            user_id=owner.id,
            card_id=card.id,
            filename="source.pdf",
            document_text="retained text",
            file=FileModel(
                storage_type="test",
                storage_name="test",
                original_filename="source.pdf",
                filename="source.pdf",
                path="/fixture",
            ),
        )
        db.insert(attachment)
    service = DomainService()
    doc = {
        "attachment_uid": attachment.get_uid(),
        "filename": "source.pdf",
        "generation": "current",
        "status": "indexed",
        "content_hash": "hash",
        "content": {"markdown": "retained text"},
        "embedding": {"status": "indexed", "source_generation": "current", "pointer": {
            **pointer(), "board_uid": project.get_uid(), "card_uid": card.get_uid(),
            "attachment_uid": attachment.get_uid(), "content_hash": "hash",
        }},
    }
    service.docling_metadata.save_documents(CardMetadata, card, [doc])
    try:
        yield service.docling_metadata, card, attachment, doc
    finally:
        service.close()


@pytest.mark.parametrize("operation", ["replace", "delete"])
def test_source_change_and_cleanup_request_commit_or_rollback_together(document_source, cleanup_store, operation):
    from langboard_shared.domain.models import CardMetadata

    service, card, attachment, document = document_source
    new = {"status": "indexed", "source_generation": "current", "pointer": {
        **document["embedding"]["pointer"], "chunk_ids": ["new-1"], "generation": "new",
    }}

    def change():
        if operation == "replace":
            assert service.publish_document_embedding(
                card, attachment.get_uid(), "current", "hash", new, expected_embedding=document["embedding"]
            )
        else:
            assert service.delete_document_by_attachment_uid(CardMetadata, card, attachment.get_uid())

    with pytest.raises(RuntimeError, match="rollback"):
        with DbSession.atomic():
            change()
            assert len(pending()) == 1
            cleanup_store.assert_not_called()
            raise RuntimeError("rollback")
    assert pending() == []
    assert service.get_document_by_attachment_uid(CardMetadata, card, attachment.get_uid()) == document
    with DbSession.use(readonly=False) as db:
        assert (
            db.exec(SqlBuilder.select.table(CardAttachment).where(CardAttachment.id == attachment.id))
            .first()
            .document_text
            == "retained text"
        )
    change()
    assert len(pending()) == 1 and pending()[0].pointer == document["embedding"]["pointer"]
    cleanup_store.assert_called_once()
    if operation == "replace":
        assert service.get_document_by_attachment_uid(CardMetadata, card, attachment.get_uid())["embedding"] == new
        assert not service.publish_document_embedding(
            card, attachment.get_uid(), "current", "hash", new, expected_embedding=document["embedding"]
        )
        assert len(pending()) == 1
    else:
        assert service.get_document_by_attachment_uid(CardMetadata, card, attachment.get_uid()) is None
        with DbSession.use(readonly=False) as db:
            current = db.exec(SqlBuilder.select.table(CardAttachment).where(CardAttachment.id == attachment.id)).first()
            assert current.document_text == "" and current.file == attachment.file
            assert current.deleted_at is None
        service.delete_document_by_attachment_uid(CardMetadata, card, attachment.get_uid())
        assert len(pending()) == 1


def test_cleanup_migration_refuses_populated_downgrade(binding_store):  # noqa: F811
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import delete, insert, select

    path = Path(__file__).parents[2] / "langboard/migrations/versions/20261017070000-a31f9e7b402c.py"
    spec = importlib.util.spec_from_file_location("cleanup_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with binding_store.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            event = DocumentVectorCleanup(id=123, pointer=pointer())
            connection.execute(
                insert(DocumentVectorCleanup.__table__).values(
                    **{column.name: getattr(event, column.name) for column in DocumentVectorCleanup.__table__.columns}
                )
            )
            with pytest.raises(RuntimeError, match="Cannot discard pending"):
                migration.downgrade()
            assert connection.execute(select(DocumentVectorCleanup.id)).scalar() == 123
            connection.execute(delete(DocumentVectorCleanup))
            migration.downgrade()
            migration.upgrade()
            assert connection.execute(select(DocumentVectorCleanup.id)).first() is None


@pytest.mark.parametrize("pointer_format", ["chunk_ids", "namespace"])
def test_durable_cleanup_removes_only_recorded_sqlite_generation(cleanup_store, tmp_path, monkeypatch, pointer_format):
    pytest.importorskip("langchain_core")
    from types import SimpleNamespace
    from langboard_shared.tasks.docling.DocumentSplitter import DocumentSplitterSettings
    from langboard_shared.tasks.docling.DocumentSqliteStore import open_document_store, open_sqlite_vector_store
    from langboard_shared.tasks.docling.DocumentSqliteVectorStore_test import Fixture
    from langboard_shared.tasks.docling.DocumentVectorStore import stage_vector_generation

    monkeypatch.setattr(embedding, "Env", SimpleNamespace(DATA_DIR=tmp_path))
    directory = tmp_path / "document-retrieval"
    directory.mkdir()
    source = {
        "board_uid": "board",
        "card_uid": "card",
        "attachment_uid": "attachment",
        "content_hash": "hash",
        "embedding_fingerprint": "a" * 64,
    }
    path = directory / (source["embedding_fingerprint"] + ".sqlite")
    if pointer_format == "namespace":
        from langboard_shared.tasks.docling.DocumentVectorGeneration import replace_attachment_generation

        legacy_source = {key: value for key, value in source.items() if key != "embedding_fingerprint"}
        with open_document_store(path, Fixture(), dimensions=3) as store:
            old = replace_attachment_generation(
                store,
                **legacy_source,
                fingerprint=source["embedding_fingerprint"],
                text="alpha old",
                splitter=DocumentSplitterSettings(),
                publish_pointer=False,
            )
            new = replace_attachment_generation(
                store,
                **legacy_source,
                fingerprint=source["embedding_fingerprint"],
                text="alpha new",
                splitter=DocumentSplitterSettings(),
                publish_pointer=False,
            )
    else:
        with open_sqlite_vector_store(path, Fixture(), dimensions=3) as store:
            old = stage_vector_generation(
                store, source=source, text="alpha old", splitter=DocumentSplitterSettings(), storage={"type": "sqlite"}
            )
            new = stage_vector_generation(
                store, source=source, text="alpha new", splitter=DocumentSplitterSettings(), storage={"type": "sqlite"}
            )
    with DbSession.atomic() as db:
        outbox.record_cleanup(db, old)
    event = pending()[0]
    assert outbox.process_cleanup(int(event.id)) is True
    assert pending() == []
    if pointer_format == "namespace":
        with open_document_store(path, Fixture(), dimensions=3) as store:
            assert store.get(tuple(old["namespace"]), "0") is None
            assert store.get(tuple(new["namespace"]), "0") is not None
            assert store.conn.execute("SELECT COUNT(*) FROM store_vectors").fetchone()[0] == new["chunk_count"]
    else:
        with open_sqlite_vector_store(path, None, dimensions=3) as store:
            assert store.get_by_ids(old["chunk_ids"]) == []
            assert store.get_by_ids(new["chunk_ids"])
    assert outbox.process_cleanup(int(event.id)) is False


@pytest.mark.parametrize("failure", ["stage", "rejected", "publication-error"])
def test_unpublished_vectors_survive_delete_failure_as_durable_cleanup(cleanup_store, monkeypatch, tmp_path, failure):
    from contextlib import nullcontext
    from langboard_shared.domain.models import DocumentExtractionCache, DocumentExtractionCacheReference
    from langboard_shared.tasks.docling.DocumentEmbeddingTask_test import fixture
    from langboard_shared.tasks.docling.DocumentSplitter import DocumentSplitterSettings
    from langboard_shared.tasks.docling.DocumentSqliteStore import open_sqlite_vector_store
    from langboard_shared.tasks.docling.DocumentSqliteVectorStore_test import Fixture
    from langboard_shared.tasks.docling.DocumentVectorGeneration import embedding_fingerprint
    from langboard_shared.tasks.docling.DocumentVectorStore import delete_vector_generation, stage_vector_generation

    service, document, _ = fixture(monkeypatch, tmp_path)
    generation = "c" * 32
    document["generation"] = generation
    # Embedding requires the exact committed extraction identity before staging.
    with DbSession.atomic() as db:
        extraction = DocumentExtractionCache(cache_key="e" * 64, markdown=document["content"]["markdown"])
        db.insert(extraction)
        db.insert(
            DocumentExtractionCacheReference(
                cache_id=extraction.id,
                use_token="d" * 32,
                source_type="card_attachment",
                source_uid="attachment",
                generation=generation,
            )
        )
    config, settings = embedding.validate_embedding_config.return_value
    settings.splitter = DocumentSplitterSettings()
    fingerprint = embedding_fingerprint(
        provider=config["base_url"], model=config["model_name"], dimensions=3, version="v1"
    )
    directory = tmp_path / "document-retrieval"
    directory.mkdir()
    path = directory / (fingerprint + ".sqlite")
    source = dict(
        board_uid="board-uid",
        card_uid="card-uid",
        attachment_uid="attachment",
        content_hash="hash",
        embedding_fingerprint=fingerprint,
    )
    with open_sqlite_vector_store(path, Fixture(), dimensions=3) as store:
        survivor = stage_vector_generation(
            store, source=source, text="previous generation", splitter=settings.splitter, storage={"type": "sqlite"}
        )
        document["embedding"]["pointer"] = survivor
        add = store.add_documents

        def partial_write(*args, **kwargs):
            result = add(*args, **kwargs)
            if failure == "stage":
                raise RuntimeError("private provider failure fixture-secret")
            return result

        monkeypatch.setattr(store, "add_documents", partial_write)
        monkeypatch.setattr(store, "delete", Mock(side_effect=RuntimeError("private delete failure fixture-secret")))
        monkeypatch.setattr(embedding, "open_document_vector_store", lambda *_: nullcontext(store))
        embedding.create_document_embeddings.return_value = Fixture()
        from langboard_shared.tasks.docling.DocumentVectorStore import astage_vector_generation

        monkeypatch.setattr(embedding, "stage_vector_generation", astage_vector_generation)
        monkeypatch.setattr(embedding, "delete_vector_generation", delete_vector_generation)
        service.docling_metadata.publish_document_embedding.return_value = False
        if failure == "publication-error":
            service.docling_metadata.publish_document_embedding.side_effect = [
                RuntimeError("publication failed"),
                False,
            ]
        asyncio.run(embedding.embed_transcription(service, "attachment", generation))
        events = pending()
        assert len(events) == 1
        staged = events[0].pointer
        store.delete.assert_called_once_with(ids=staged["chunk_ids"])
        assert staged["chunk_ids"] != survivor["chunk_ids"]
        assert store.get_by_ids(staged["chunk_ids"])
        assert store.get_by_ids(survivor["chunk_ids"])
        assert "fixture-secret" not in str(staged) and "private" not in str(staged)
        failed = service.docling_metadata.publish_document_embedding.call_args.args[-1]
        assert failed["status"] == "failed" and failed["pointer"] == survivor
    cleanup_store.assert_called_once()
    assert outbox.process_cleanup(int(events[0].id)) is True
    assert pending() == []
    assert outbox.process_cleanup(int(events[0].id)) is False
    with open_sqlite_vector_store(path, Fixture(), dimensions=3) as store:
        assert not store.get_by_ids(staged["chunk_ids"])
        assert store.get_by_ids(survivor["chunk_ids"])
