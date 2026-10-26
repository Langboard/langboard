"""Real native SQL and vector stores; external conversion and inference are fixtures."""

import asyncio
from hashlib import sha256
from io import BytesIO
from json import dumps
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from langboard_shared.core.broker import Broker
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.storage import Storage, StorageName
from langboard_shared.core.storage.LocalStorage import LocalStorage
from langboard_shared.domain.models import (
    AttachmentStorageObject,
    AttachmentStorageReference,
    DocumentEmbeddingCache,
    DocumentEmbeddingCacheReference,
    DocumentExtractionCache,
    DocumentExtractionCacheReference,
    DocumentVectorCleanup,
    ProjectAssignedUser,
    ProjectAttachment,
    ProjectAttachmentDocument,
    ProjectWiki,
    ProjectWikiAssignedUser,
    ProjectWikiAttachment,
    WikiAttachmentDocument,
)
from langboard_shared.domain.models.InternalBot import InternalBotType
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services import WikiDocumentQuery as query
from langboard_shared.domain.services.factory.InternalBotService import InternalBotService
from langboard_shared.Env import Env
from langboard_shared.tasks.docling import DocumentEmbedding
from langboard_shared.tasks.docling import ProjectDocumentTask as task
from langboard_shared.tasks.docling.DocumentCleanupTask import process_cleanup
from langchain_core.embeddings import Embeddings
from sqlalchemy import delete, update
from starlette.datastructures import UploadFile


pytestmark = pytest.mark.parametrize("mirror_board", ["sqlite", "postgresql-test"], indirect=True)


class Provider(Embeddings):
    def __init__(self):
        self.calls = 0
        self.during = lambda: None

    def embed_documents(self, texts):
        assert not DbSession.has_active_transaction()
        self.calls += 1
        self.during()
        return [[1.0, 2.0, 3.0] for _ in texts]

    async def aembed_documents(self, texts):
        return self.embed_documents(texts)

    def embed_query(self, text):
        self.during()
        return [1.0, 2.0, 3.0]


@pytest.fixture
def wiki(mirror_board, monkeypatch, tmp_path):
    b = mirror_board
    for model in (AttachmentStorageObject, AttachmentStorageReference):
        model.__table__.create(b.engine, checkfirst=True)
    for model in (
        ProjectWiki,
        ProjectWikiAssignedUser,
        ProjectWikiAttachment,
        WikiAttachmentDocument,
        ProjectAttachment,
        ProjectAttachmentDocument,
        DocumentEmbeddingCache,
        DocumentEmbeddingCacheReference,
        DocumentExtractionCache,
        DocumentExtractionCacheReference,
        DocumentVectorCleanup,
    ):
        model.__table__.create(b.engine)
    monkeypatch.setattr(type(Env), "LOCAL_STORAGE_DIR", property(lambda _: tmp_path / "objects"))
    monkeypatch.setattr(type(Env), "DATA_DIR", property(lambda _: tmp_path))
    monkeypatch.setattr(Storage, "_storages", {"local": LocalStorage()})
    b.enabled = True
    b.config = dumps(
        dict(
            agent_llm="OpenAI Compatible",
            model_name="fixture",
            base_url="https://fixture.invalid",
            api_key="private-secret",
            retrieval=dict(enabled=True, store="sqlite", dimensions=3, timeout_seconds=2),
        )
    )
    b.vision = SimpleNamespace(value='{"model_name":"fixture"}', get_uid=lambda: "vision-fixture")
    b.binding = SimpleNamespace(value=b.config, get_uid=lambda: "binding", bot_type=InternalBotType.DocumentEmbedding)
    monkeypatch.setattr(InternalBotService, "is_document_processing_enabled", lambda _: b.enabled)
    monkeypatch.setattr(InternalBotService, "get_document_vision_binding", lambda _: b.vision)
    monkeypatch.setattr(InternalBotService, "get_document_embedding_binding", lambda _: b.binding)
    monkeypatch.setattr(InternalBotService, "get_current_by_id_like", lambda *_: b.binding)
    b.sent = Mock()
    monkeypatch.setattr(Broker.celery, "send_task", b.sent)
    monkeypatch.setattr(task, "_current_vision_config", lambda *_: {"keyword_languages": ["en"]})
    b.convert = Mock(return_value="Shared private wiki alpha text")
    monkeypatch.setattr(task, "_convert_to_markdown", b.convert)
    b.provider = Provider()
    monkeypatch.setattr(task, "create_document_embeddings", lambda *_: b.provider)
    monkeypatch.setattr(DocumentEmbedding, "create_document_embeddings", lambda *_: b.provider)
    b.domain = DomainService()
    b.docs = b.domain.wiki_document
    with DbSession.atomic() as db:
        b.wiki = ProjectWiki(project_id=b.source_board.id, title="Private wiki", is_public=False)
        db.insert(b.wiki)
        member = db.exec(
            SqlBuilder.select.table(ProjectAssignedUser).where(ProjectAssignedUser.user_id == b.actor.id)
        ).first()
        b.assignment = ProjectWikiAssignedUser(
            project_wiki_id=b.wiki.id, project_assigned_id=member.id, user_id=b.actor.id
        )
        db.insert(b.assignment)
    yield b
    b.domain.close()


def rows(model):
    with DbSession.use(readonly=False) as db:
        return db.exec(SqlBuilder.select.table(model)).all()


def upload(b, data=b"source"):
    file = Storage.upload(UploadFile(BytesIO(data), filename="report.pdf"), StorageName.Wiki)
    return b.domain.project_wiki.upload_attachment(b.actor, b.source_board, b.wiki, file)


def indexed(b, attachment):
    generation = b.docs.load_source(attachment.get_uid())[2].generation
    task._index_project_attachment(b.domain, attachment.get_uid(), generation, documents=b.docs)
    asyncio.run(
        task._embed_project_attachment(
            b.domain, attachment.get_uid(), generation, documents=b.docs, source_factory=b.docs.vector_source
        )
    )
    state = b.docs.get_readable(b.actor, b.source_board, attachment)[2]
    assert state.status == "indexed" and state.embedding["status"] == "indexed"
    return state


def test_native_upload_worker_shared_calculation_independent_generations_search_and_cleanup(wiki):
    b = wiki
    first = upload(b)
    assert first.content_sha256 == sha256(b"source").hexdigest()
    assert "content_sha256" not in first.api_response()
    assert b.sent.call_count == 1
    before = b.docs.load_source(first.get_uid())[2]
    assert before.api_response() == {"generation": before.generation, "status": "pending"}
    one = indexed(b, first)
    second = upload(b)
    two = indexed(b, second)
    assert one.embedding["pointer"]["chunk_ids"] != two.embedding["pointer"]["chunk_ids"]
    assert b.convert.call_count == 1 and b.provider.calls == 1
    assert len(rows(DocumentExtractionCache)) == len(rows(DocumentEmbeddingCache)) == 1
    result = query.read_wiki_document(
        b.domain, b.actor, b.source_board.get_uid(), b.wiki.get_uid(), first.get_uid(), limit=8
    )
    assert result.text == "Shared p" and result.next_offset == 8
    assert result.source.source_type == "wiki_attachment" and result.source.wiki_uid == b.wiki.get_uid()
    found = query.search_wiki_document(
        b.domain, b.actor, b.source_board.get_uid(), b.wiki.get_uid(), first.get_uid(), "alpha"
    )
    assert found.matches and found.matches[0].source.wiki_uid == b.wiki.get_uid()
    with DbSession.atomic() as db:
        b.docs.invalidate(db, first)
        db.delete(first)
    assert len(rows(DocumentExtractionCacheReference)) == 1
    event = rows(DocumentVectorCleanup)[0]
    assert event.pointer["wiki_uid"] == b.wiki.get_uid()
    assert process_cleanup(int(event.id))
    assert len(rows(DocumentEmbeddingCacheReference)) == 1
    assert query.search_wiki_document(
        b.domain, b.actor, b.source_board.get_uid(), b.wiki.get_uid(), second.get_uid(), "alpha"
    ).matches
    b.docs.record_scope_embedding_cleanup(b.source_board, b.wiki)
    for event in rows(DocumentVectorCleanup):
        assert process_cleanup(int(event.id))
    assert not rows(WikiAttachmentDocument) and not rows(DocumentExtractionCache) and not rows(DocumentEmbeddingCache)


def test_new_embedding_settings_partition_paid_work_without_reupload_or_automatic_reindex(wiki, monkeypatch):
    from json import loads

    b = wiki
    physical = Mock(wraps=Storage._storages["local"].upload_immutable)
    monkeypatch.setattr(Storage._storages["local"], "upload_immutable", physical)
    first = upload(b)
    original = indexed(b, first)
    settings = loads(b.binding.value)
    settings["model_name"] = "changed-embedding-model"
    b.binding.value = dumps(settings)
    second = upload(b)
    changed = indexed(b, second)
    assert physical.call_count == len(rows(AttachmentStorageObject)) == 1
    assert b.convert.call_count == len(rows(DocumentExtractionCache)) == 1
    assert b.provider.calls == len(rows(DocumentEmbeddingCache)) == 2
    assert original.generation != changed.generation
    assert original.embedding["pointer"]["embedding_fingerprint"] != changed.embedding["pointer"]["embedding_fingerprint"]
    retained = next(row for row in rows(WikiAttachmentDocument) if row.attachment_id == first.id)
    assert retained.generation == original.generation and retained.embedding == original.embedding
    assert retained.embedding_config == original.embedding_config
    assert len(rows(DocumentExtractionCacheReference)) == len(rows(DocumentEmbeddingCacheReference)) == 2


@pytest.mark.parametrize("change", ["vision", "runtime"])
def test_changed_extraction_settings_partition_identical_chunks_and_keep_published_search(wiki, monkeypatch, change):
    b = wiki
    first = upload(b)
    original = indexed(b, first)
    second = upload(b)
    indexed(b, second)
    assert b.convert.call_count == b.provider.calls == 1
    # A global change schedules nothing and leaves published generations readable.
    sent = b.sent.call_count
    if change == "vision":
        b.vision.value = dumps({"model_name": "fixture", "top_p": 0.4})
    else:
        monkeypatch.setattr(
            "langboard_shared.tasks.docling.DocumentExtractionCache.metadata.version", lambda *_: "future-runtime"
        )
    assert b.sent.call_count == sent
    assert b.docs.load_source(first.get_uid())[2].embedding == original.embedding
    third = upload(b)
    changed = indexed(b, third)
    assert changed.markdown == original.markdown
    assert b.convert.call_count == b.provider.calls == 2
    assert len(rows(DocumentExtractionCache)) == len(rows(DocumentEmbeddingCache)) == 2
    assert len(rows(DocumentEmbeddingCacheReference)) == 3
    assert b.docs.load_source(first.get_uid())[2].embedding == original.embedding
    for attachment in (first, second, third):
        assert query.search_wiki_document(
            b.domain, b.actor, b.source_board.get_uid(), b.wiki.get_uid(), attachment.get_uid(), "alpha"
        ).matches
    with DbSession.atomic() as db:
        b.docs.invalidate(db, third)
        db.delete(third)
    for event in rows(DocumentVectorCleanup):
        assert process_cleanup(int(event.id))
    assert len(rows(DocumentExtractionCache)) == len(rows(DocumentEmbeddingCache)) == 1
    assert len(rows(DocumentEmbeddingCacheReference)) == 2
    assert query.search_wiki_document(
        b.domain, b.actor, b.source_board.get_uid(), b.wiki.get_uid(), first.get_uid(), "alpha"
    ).matches


def test_queued_embedding_without_committed_lineage_requires_reprocess_and_retains_search(wiki):
    b = wiki
    attachment = upload(b)
    published = indexed(b, attachment)
    with DbSession.atomic() as db:
        db.exec(delete(DocumentExtractionCacheReference))
    asyncio.run(task._embed_project_attachment(
        b.domain, attachment.get_uid(), published.generation, documents=b.docs, source_factory=b.docs.vector_source,
    ))
    current = b.docs.load_source(attachment.get_uid())[2]
    assert current.embedding["status"] == "failed"
    assert "explicitly reprocess" in current.embedding["error"]
    assert current.embedding["pointer"] == published.embedding["pointer"]
    assert b.provider.calls == 1
    assert query.search_wiki_document(
        b.domain, b.actor, b.source_board.get_uid(), b.wiki.get_uid(), attachment.get_uid(), "alpha"
    ).matches


def test_metadata_default_legacy_lazy_hash_and_exact_wiki_scope(wiki):
    b = wiki
    b.enabled = False
    attachment = upload(b)
    assert not rows(WikiAttachmentDocument) and not b.sent.called
    with DbSession.atomic() as db:
        db.exec(
            update(ProjectWikiAttachment).where(ProjectWikiAttachment.id == attachment.id).values(content_sha256=None)
        )
    receipt = query.request_wiki_document_processing(
        b.domain, b.actor, b.source_board.get_uid(), b.wiki.get_uid(), attachment.get_uid()
    )
    assert receipt.status == "pending" and b.sent.call_count == 1
    assert rows(ProjectWikiAttachment)[0].content_sha256 == sha256(b"source").hexdigest()
    for operation in (query.read_wiki_document, query.request_wiki_document_processing):
        with pytest.raises(ValueError):
            operation(b.domain, b.actor, b.source_board.get_uid(), "wrong-wiki", attachment.get_uid())
    assert b.docs.request(b.actor, b.destination, attachment) is None


@pytest.mark.parametrize("phase", ["before", "conversion", "embedding", "search"])
def test_private_assignment_revocation_never_exposes_text(wiki, phase):
    b = wiki
    attachment = upload(b)
    generation = b.docs.load_source(attachment.get_uid())[2].generation

    def revoke():
        with DbSession.atomic() as db:
            db.exec(delete(ProjectWikiAssignedUser).where(ProjectWikiAssignedUser.id == b.assignment.id))

    if phase == "before":
        revoke()
        assert b.docs.claim(attachment.get_uid(), generation) is None
        assert b.docs.request(b.actor, b.source_board, attachment) is None
    elif phase == "conversion":
        b.convert.side_effect = lambda *_args, **_kwargs: (revoke() or "Unauthorized secret")
        task._index_project_attachment(b.domain, attachment.get_uid(), generation, documents=b.docs)
        assert rows(WikiAttachmentDocument)[0].markdown == ""
    elif phase == "embedding":
        task._index_project_attachment(b.domain, attachment.get_uid(), generation, documents=b.docs)
        b.provider.during = revoke
        asyncio.run(
            task._embed_project_attachment(
                b.domain, attachment.get_uid(), generation, documents=b.docs, source_factory=b.docs.vector_source
            )
        )
        assert not rows(WikiAttachmentDocument)[0].embedding.get("pointer")
    else:
        indexed(b, attachment)
        b.provider.during = revoke
        with pytest.raises(ValueError):
            query.search_wiki_document(
                b.domain, b.actor, b.source_board.get_uid(), b.wiki.get_uid(), attachment.get_uid(), "alpha"
            )
    assert b.docs.get_readable(b.actor, b.source_board, attachment) is None
    assert b.docs.load_source(attachment.get_uid(), generation) is None


def test_force_generation_fences_stale_worker_and_transaction_rollback(wiki):
    b = wiki
    attachment = upload(b)
    generation = b.docs.load_source(attachment.get_uid())[2].generation
    assert b.docs.claim(attachment.get_uid(), generation)
    newer = b.docs.request(b.actor, b.source_board, attachment, force=True)
    assert newer["generation"] != generation
    assert not b.docs.publish_extraction(
        attachment.get_uid(), generation, markdown="stale", keywords={}, document_json=None
    )
    before = b.sent.call_count
    with pytest.raises(RuntimeError):
        with DbSession.atomic():
            b.docs.request(b.actor, b.source_board, attachment, force=True)
            raise RuntimeError("rollback")
    assert b.sent.call_count == before
    assert b.docs.load_source(attachment.get_uid())[2].generation == newer["generation"]


def test_project_wiki_share_conversion_embedding_without_shared_acl_or_vectors(wiki):
    b = wiki
    attachment = upload(b)
    wiki_state = indexed(b, attachment)
    file = Storage.upload(UploadFile(BytesIO(b"source"), filename="report.pdf"), StorageName.ProjectAttachment)
    with DbSession.atomic() as db:
        project_attachment = ProjectAttachment(
            project_id=b.source_board.id,
            user_id=b.actor.id,
            filename="report.pdf",
            file=file,
            content_sha256=attachment.content_sha256,
        )
        db.insert(project_attachment)
    receipt = b.domain.project_document.request(b.actor, b.source_board, project_attachment)
    task._index_project_attachment(b.domain, project_attachment.get_uid(), receipt["generation"])
    asyncio.run(task._embed_project_attachment(b.domain, project_attachment.get_uid(), receipt["generation"]))
    project_state = b.domain.project_document.get_readable(b.actor, b.source_board, project_attachment)[2]
    assert project_state.embedding["status"] == "indexed"
    assert set(project_state.embedding["pointer"]["chunk_ids"]).isdisjoint(wiki_state.embedding["pointer"]["chunk_ids"])
    assert b.provider.calls == b.convert.call_count == 1
    assert len(rows(DocumentExtractionCacheReference)) == len(rows(DocumentEmbeddingCacheReference)) == 2
    with DbSession.atomic() as db:
        db.exec(delete(ProjectWikiAssignedUser).where(ProjectWikiAssignedUser.id == b.assignment.id))
    assert b.docs.get_readable(b.actor, b.source_board, attachment) is None
    assert b.domain.project_document.get_readable(b.actor, b.source_board, project_attachment)[2].markdown
    b.docs.record_scope_embedding_cleanup(b.source_board, b.wiki)
    assert b.domain.project_document.get_readable(b.actor, b.source_board, project_attachment)[2].markdown
    assert len(rows(DocumentExtractionCacheReference)) == 1


def test_additive_migration_preserves_historical_upload_and_blocks_populated_downgrade(wiki):
    from importlib.util import module_from_spec, spec_from_file_location
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text

    b = wiki
    WikiAttachmentDocument.__table__.drop(b.engine)
    with b.engine.begin() as connection:
        connection.execute(text("ALTER TABLE project_wiki_attachment DROP COLUMN content_sha256"))
        connection.execute(
            text(
                'INSERT INTO project_wiki_attachment (id, created_at, updated_at, user_id, project_wiki_id, filename, file, "order") '
                "VALUES (:id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, :user, :wiki, 'legacy.pdf', :file, 0)"
            ),
            {"id": 123, "user": int(b.actor.id), "wiki": int(b.wiki.id), "file": '"legacy-storage-reference"'},
        )
        path = Path(__file__).parents[2] / "langboard/migrations/versions/20261022210000-8359ef07db21.py"
        spec = spec_from_file_location("wiki_migration", path)
        migration = module_from_spec(spec)
        spec.loader.exec_module(migration)
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        preserved = connection.execute(
            text("SELECT filename, content_sha256 FROM project_wiki_attachment WHERE id=123")
        ).first()
        assert preserved == ("legacy.pdf", None)
        connection.execute(
            text("UPDATE project_wiki_attachment SET content_sha256=:hash WHERE id=123"), {"hash": "a" * 64}
        )
        with pytest.raises(RuntimeError, match="Cannot discard"):
            migration.downgrade()
        connection.execute(text("UPDATE project_wiki_attachment SET content_sha256=NULL WHERE id=123"))
        migration.downgrade()
        assert connection.execute(text("SELECT count(*) FROM project_wiki_attachment WHERE id=123")).scalar() == 1


def test_wiki_deletion_atomically_invalidates_and_retains_independent_source(wiki, monkeypatch):
    from langboard_shared.domain.services.factory.GraphApprovalRequestService import GraphApprovalRequestService
    from langboard_shared.domain.services.factory.ProjectWikiService import ProjectWikiService
    from langboard_shared.publishers import ProjectWikiPublisher
    from langboard_shared.tasks.activities import ProjectWikiActivityTask
    from langboard_shared.tasks.bots import ProjectWikiBotTask

    b = wiki
    attachment = upload(b)
    indexed(b, attachment)
    monkeypatch.setattr(GraphApprovalRequestService, "cancel_pending_by_scope", lambda *_a, **_k: None)
    monkeypatch.setattr(ProjectWikiService, "_publish_linked_card_changed", lambda *_: None)
    monkeypatch.setattr(ProjectWikiPublisher, "deleted", lambda *_: None)
    monkeypatch.setattr(ProjectWikiActivityTask, "project_wiki_deleted", lambda *_: None)
    monkeypatch.setattr(ProjectWikiBotTask, "project_wiki_deleted", lambda *_: None)
    with pytest.raises(RuntimeError):
        with DbSession.atomic():
            assert b.domain.project_wiki.delete(b.actor, b.source_board, b.wiki)
            raise RuntimeError("rollback")
    assert b.docs.get_readable(b.actor, b.source_board, attachment)
    assert rows(DocumentExtractionCacheReference) and not rows(DocumentVectorCleanup)
    assert b.domain.project_wiki.delete(b.actor, b.source_board, b.wiki)
    assert b.docs.get_readable(b.actor, b.source_board, attachment) is None
    assert not rows(WikiAttachmentDocument) and not rows(DocumentExtractionCacheReference)
    assert len(rows(DocumentVectorCleanup)) == 1


@pytest.mark.parametrize("failure", ["empty", "settings", "queue"])
def test_optional_processing_failures_preserve_native_upload(wiki, monkeypatch, failure):
    b = wiki
    if failure == "settings":
        monkeypatch.setattr(
            InternalBotService,
            "get_document_embedding_binding",
            lambda _: (_ for _ in ()).throw(ValueError("private-secret")),
        )
    elif failure == "queue":
        b.sent.side_effect = RuntimeError("private-secret")
    attachment = upload(b, data=b"" if failure == "empty" else b"source")
    assert attachment is not None
    assert rows(ProjectWikiAttachment)[0].content_sha256 == sha256(b"" if failure == "empty" else b"source").hexdigest()
    if failure == "settings":
        assert not rows(WikiAttachmentDocument)
    elif failure == "queue":
        assert rows(WikiAttachmentDocument)[0].status == "failed"
        assert "private-secret" not in str(rows(WikiAttachmentDocument)[0].api_response())
    else:
        generation = b.docs.load_source(attachment.get_uid())[2].generation
        task._index_project_attachment(b.domain, attachment.get_uid(), generation, documents=b.docs)
        assert rows(WikiAttachmentDocument)[0].status == "failed"


def test_card_project_wiki_native_workers_share_one_paid_calculation(wiki, monkeypatch):
    from langboard_shared.domain.models import (
        CardAttachment,
        CardDocumentArtifact,
        CardMetadata,
        DocumentKeywordIndex,
        KeywordSearchConfiguration,
    )
    from langboard_shared.domain.services.factory.DoclingMetadataService import DoclingMetadataService
    from langboard_shared.tasks.docling import DoclingMetadataTask as card_task
    from langboard_shared.tasks.docling import DocumentEmbeddingTask as card_embedding

    b = wiki
    for model in (CardAttachment, CardMetadata, CardDocumentArtifact, KeywordSearchConfiguration, DocumentKeywordIndex):
        model.__table__.create(b.engine)
    physical = Mock(wraps=Storage._storages["local"].upload_immutable)
    monkeypatch.setattr(Storage._storages["local"], "upload_immutable", physical)
    wiki_attachment = upload(b)
    wiki_state = indexed(b, wiki_attachment)
    file = Storage.upload(UploadFile(BytesIO(b"source"), filename="report.pdf"), StorageName.ProjectAttachment)
    with DbSession.atomic() as db:
        project_attachment = ProjectAttachment(
            project_id=b.source_board.id,
            user_id=b.actor.id,
            filename="report.pdf",
            file=file,
            content_sha256=wiki_attachment.content_sha256,
        )
        db.insert(project_attachment)
    receipt = b.domain.project_document.request(b.actor, b.source_board, project_attachment)
    task._index_project_attachment(b.domain, project_attachment.get_uid(), receipt["generation"])
    asyncio.run(task._embed_project_attachment(b.domain, project_attachment.get_uid(), receipt["generation"]))
    card_file = Storage.upload(UploadFile(BytesIO(b"source"), filename="report.pdf"), StorageName.CardAttachment)
    with DbSession.atomic() as db:
        card_attachment = CardAttachment(
            card_id=b.source.id,
            user_id=b.actor.id,
            filename="report.pdf",
            file=card_file,
            content_sha256=wiki_attachment.content_sha256,
        )
        db.insert(card_attachment)
    monkeypatch.setattr(DoclingMetadataService, "publish_update", lambda *_: None)
    monkeypatch.setattr(card_task, "find_spec", lambda _: True)
    monkeypatch.setattr(card_task, "_current_vision_config", lambda *_: {"keyword_languages": ["en"]})
    monkeypatch.setattr(card_task, "_convert_to_markdown", b.convert)
    monkeypatch.setattr(card_embedding, "create_document_embeddings", lambda *_: b.provider)
    assert b.domain.docling_metadata.queue_document(
        CardMetadata,
        b.source,
        card_attachment.get_uid(),
        "report.pdf",
        vision_config=wiki_state.vision_config,
        embedding_config=wiki_state.embedding_config,
    )
    generation = b.domain.docling_metadata.get_document_by_attachment_uid(
        CardMetadata, b.source, card_attachment.get_uid()
    )["generation"]
    card_task._index_card_attachment(b.domain, card_attachment, generation=generation)
    generation = b.domain.docling_metadata.get_document_by_attachment_uid(
        CardMetadata, b.source, card_attachment.get_uid()
    )["generation"]
    asyncio.run(card_embedding.embed_transcription(b.domain, card_attachment.get_uid(), generation))
    card_state = b.domain.docling_metadata.get_document_by_attachment_uid(
        CardMetadata, b.source, card_attachment.get_uid()
    )
    assert card_state["status"] == "indexed" and card_state["embedding"]["status"] == "indexed"
    assert b.convert.call_count == 1 and b.provider.calls == 1
    assert physical.call_count == len(rows(AttachmentStorageObject)) == 1
    assert len(rows(AttachmentStorageReference)) == 3
    assert len({wiki_attachment.file.path, project_attachment.file.path, card_attachment.file.path}) == 3
    assert len(rows(DocumentExtractionCacheReference)) == len(rows(DocumentEmbeddingCacheReference)) == 3
    assert len(rows(DocumentExtractionCache)) == len(rows(DocumentEmbeddingCache)) == 1
    pointers = [
        wiki_state.embedding["pointer"],
        card_state["embedding"]["pointer"],
        b.domain.project_document.get_readable(b.actor, b.source_board, project_attachment)[2].embedding["pointer"],
    ]
    assert len({pointer["generation"] for pointer in pointers}) == 3
    b.docs.record_scope_embedding_cleanup(b.source_board, b.wiki)
    for event in rows(DocumentVectorCleanup):
        assert process_cleanup(int(event.id))
    assert len(rows(DocumentExtractionCacheReference)) == len(rows(DocumentEmbeddingCacheReference)) == 2
    assert (
        b.domain.project_document.get_readable(b.actor, b.source_board, project_attachment)[2].embedding["status"]
        == "indexed"
    )
    assert (
        b.domain.docling_metadata.get_document_by_attachment_uid(CardMetadata, b.source, card_attachment.get_uid())[
            "embedding"
        ]["status"]
        == "indexed"
    )


def test_authenticated_http_native_wiki_routes_and_private_revocation(wiki, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
    from langboard.routes.board import BoardCloudApi as catalog_api
    from langboard.routes.board import BoardCloudWikiDocumentApi as api
    from langboard_shared.core.routing import AppRouter
    from langboard_shared.core.security import AuthSecurity
    from langboard_shared.domain.models import CardAttachment, EmployeeMembershipPolicy

    b = wiki
    attachment = upload(b)
    indexed(b, attachment)
    CardAttachment.__table__.create(b.engine)
    EmployeeMembershipPolicy.__table__.create(b.engine, checkfirst=True)
    app = FastAPI()
    app.include_router(AppRouter.api)
    for route in AppRouter.api.routes:
        if getattr(route, "endpoint", None) in (
            catalog_api.get_board_cloud,
            api.read_wiki_document,
            api.request_wiki_document_processing,
            api.search_wiki_document,
        ):
            for dependency in route.dependant.dependencies:
                if dependency.name == "service":
                    app.dependency_overrides[dependency.call] = lambda: b.domain
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    access, refresh = AuthSecurity.authenticate(b.actor.id)
    path = f"/board/{b.source_board.get_uid()}/cloud/wiki/{b.wiki.get_uid()}/attachment/{attachment.get_uid()}/document"
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get(path).status_code == 401
        client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
        client.headers["Authorization"] = f"Bearer {access}"
        catalog = client.get(f"/board/{b.source_board.get_uid()}/cloud", params={"view": "files"})
        assert catalog.status_code == 200, catalog.text
        item = next(item for item in catalog.json()["items"] if item["attachment_uid"] == attachment.get_uid())
        assert item["source"]["kind"] == "wiki"
        assert item["source"]["wiki_uid"] == b.wiki.get_uid()
        assert item["source"]["project_uid"] == b.source_board.get_uid()
        assert item["source"]["url"] == f"/board/{b.source_board.get_uid()}/wiki/{b.wiki.get_uid()}"
        owner_path = f"/wiki/{item['source']['wiki_uid']}" if item["source"]["kind"] == "wiki" else ""
        catalog_path = (
            f"/board/{item['source']['project_uid']}/cloud{owner_path}"
            f"/attachment/{item['attachment_uid']}/document"
        )
        assert catalog_path == path
        path = catalog_path
        response = client.get(path, params={"limit": 10})
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "private, no-store"
        assert response.json()["source"]["wiki_uid"] == b.wiki.get_uid()
        assert len(response.json()["text"]) == 10
        assert client.post(path + "/process").status_code == 200
        assert client.post(path + "/search", json={"query": "alpha"}).json()["matches"]
        assert client.get(path, params={"limit": 8001}).status_code == 400
        assert client.post(path + "/search", json={"query": "alpha", "max_tokens": "200"}).status_code == 400
        with DbSession.atomic() as db:
            db.exec(delete(ProjectWikiAssignedUser).where(ProjectWikiAssignedUser.id == b.assignment.id))
        revoked_catalog = client.get(f"/board/{b.source_board.get_uid()}/cloud", params={"view": "files"})
        assert revoked_catalog.status_code == 200, revoked_catalog.text
        assert all(item["attachment_uid"] != attachment.get_uid() for item in revoked_catalog.json()["items"])
        for method, url, kwargs in (
            ("get", path, {}),
            ("post", path + "/process", {}),
            ("post", path + "/search", {"json": {"query": "alpha"}}),
        ):
            denied = getattr(client, method)(url, **kwargs)
            assert denied.status_code == 400, denied.text
            assert "Shared private" not in denied.text
            assert denied.headers["cache-control"] == "private, no-store"


@pytest.mark.parametrize(
    "drift", ["membership", "role", "activation", "deleted_wiki", "deleted_project", "provenance", "source_wiki"]
)
def test_current_native_authority_and_provenance_drift_fences_worker(wiki, drift):
    from langboard_shared.core.types import SafeDateTime
    from langboard_shared.domain.models import Project, ProjectRole, User

    b = wiki
    attachment = upload(b)
    generation = b.docs.load_source(attachment.get_uid())[2].generation
    with DbSession.atomic() as db:
        if drift == "membership":
            db.exec(delete(ProjectAssignedUser).where(ProjectAssignedUser.user_id == b.actor.id))
        elif drift == "role":
            db.exec(update(ProjectRole).where(ProjectRole.user_id == b.actor.id).values(actions=[]))
        elif drift == "activation":
            db.exec(update(User).where(User.id == b.actor.id).values(activated_at=None))
        elif drift == "deleted_wiki":
            db.exec(update(ProjectWiki).where(ProjectWiki.id == b.wiki.id).values(deleted_at=SafeDateTime.now()))
        elif drift == "deleted_project":
            db.exec(update(Project).where(Project.id == b.source_board.id).values(deleted_at=SafeDateTime.now()))
        elif drift == "provenance":
            db.insert(
                ProjectWikiAttachment(
                    project_wiki_id=b.wiki.id, user_id=b.actor.id, filename="alias.pdf", file=attachment.file
                )
            )
        else:
            other = ProjectWiki(project_id=b.source_board.id, title="Another wiki")
            db.insert(other)
            db.exec(
                update(ProjectWikiAttachment)
                .where(ProjectWikiAttachment.id == attachment.id)
                .values(project_wiki_id=other.id)
            )
    assert b.docs.load_source(attachment.get_uid(), generation) is None
    assert b.docs.claim(attachment.get_uid(), generation) is None
    assert not b.docs.publish_extraction(
        attachment.get_uid(), generation, markdown="stale", keywords={}, document_json=None
    )


def test_bounded_read_final_assignment_recheck(wiki, monkeypatch):
    b = wiki
    attachment = upload(b)
    indexed(b, attachment)
    original = b.docs.get_readable
    calls = []

    def current(*args):
        calls.append(True)
        if len(calls) == 2:
            with DbSession.atomic() as db:
                db.exec(delete(ProjectWikiAssignedUser).where(ProjectWikiAssignedUser.id == b.assignment.id))
        return original(*args)

    monkeypatch.setattr(b.docs, "get_readable", current)
    with pytest.raises(ValueError):
        query.read_wiki_document(b.domain, b.actor, b.source_board.get_uid(), b.wiki.get_uid(), attachment.get_uid())
    assert len(calls) == 2


@pytest.mark.parametrize("phase", ["conversion", "embedding", "search"])
def test_current_model_binding_drift_never_publishes_or_returns_stale_results(wiki, phase):
    b = wiki
    attachment = upload(b)
    generation = b.docs.load_source(attachment.get_uid())[2].generation
    if phase == "conversion":
        valid = [True]

        def vision(*_):
            if not valid[0]:
                raise ValueError("Binding changed")
            return {"keyword_languages": ["en"]}

        from unittest.mock import patch

        with patch.object(task, "_current_vision_config", vision):
            b.convert.side_effect = lambda *_a, **_k: (valid.__setitem__(0, False) or "Stale conversion")
            task._index_project_attachment(b.domain, attachment.get_uid(), generation, documents=b.docs)
        assert rows(WikiAttachmentDocument)[0].markdown == ""
    elif phase == "embedding":
        task._index_project_attachment(b.domain, attachment.get_uid(), generation, documents=b.docs)
        b.provider.during = lambda: setattr(b.binding, "value", b.config.replace("fixture.invalid", "changed.invalid"))
        asyncio.run(
            task._embed_project_attachment(
                b.domain, attachment.get_uid(), generation, documents=b.docs, source_factory=b.docs.vector_source
            )
        )
        assert not rows(WikiAttachmentDocument)[0].embedding.get("pointer")
    else:
        indexed(b, attachment)
        b.provider.during = lambda: setattr(b.binding, "value", b.config.replace("fixture.invalid", "changed.invalid"))
        with pytest.raises(ValueError):
            query.search_wiki_document(
                b.domain, b.actor, b.source_board.get_uid(), b.wiki.get_uid(), attachment.get_uid(), "alpha"
            )


def test_project_deletion_transaction_invalidates_wiki_documents(wiki, monkeypatch):
    from langboard_shared.domain.services.factory.CardService import CardService
    from langboard_shared.domain.services.factory.DoclingMetadataService import DoclingMetadataService
    from langboard_shared.infrastructure.repositories.factory.CheckitemRepository import CheckitemRepository
    from langboard_shared.publishers import ProjectPublisher
    from langboard_shared.tasks.activities import ProjectActivityTask
    from langboard_shared.tasks.bots import ProjectBotTask

    b = wiki
    attachment = upload(b)
    indexed(b, attachment)
    monkeypatch.setattr(CheckitemRepository, "get_all_started_checkitem_by_project", lambda *_: [])
    monkeypatch.setattr(DoclingMetadataService, "record_scope_embedding_cleanup", lambda *_: None)
    monkeypatch.setattr(CardService, "invalidate_source_project_mirrors", lambda *_: None)
    monkeypatch.setattr(ProjectPublisher, "deleted", lambda *_: None)
    monkeypatch.setattr(ProjectActivityTask, "project_deleted", lambda *_: None)
    monkeypatch.setattr(ProjectBotTask, "project_deleted", lambda *_: None)
    with pytest.raises(RuntimeError):
        with DbSession.atomic():
            assert b.domain.project.delete(b.actor, b.source_board)
            raise RuntimeError("rollback")
    assert b.docs.get_readable(b.actor, b.source_board, attachment)
    assert not rows(DocumentVectorCleanup)
    assert b.domain.project.delete(b.actor, b.source_board)
    assert not b.docs.get_readable(b.actor, b.source_board, attachment)
    assert not rows(WikiAttachmentDocument) and not rows(DocumentExtractionCacheReference)
    assert len(rows(DocumentVectorCleanup)) == 1


@pytest.mark.parametrize("name", ["index_wiki_attachment", "embed_wiki_attachment"])
def test_registered_wiki_broker_entrypoint_dispatches_and_closes(wiki, monkeypatch, name):
    from threading import Event
    from langboard_shared.tasks.docling import WikiDocumentTask as entry

    b = wiki
    attachment = upload(b)
    generation = b.docs.load_source(attachment.get_uid())[2].generation
    closed = Event()
    wrapper = SimpleNamespace(wiki_document=b.docs, internal_bot=b.domain.internal_bot, close=closed.set)
    monkeypatch.setattr(entry, "DomainService", lambda: wrapper)
    if name == "embed_wiki_attachment":
        task._index_project_attachment(b.domain, attachment.get_uid(), generation, documents=b.docs)
    payload = dumps(dict(attachment_uid=attachment.get_uid(), generation=generation))
    if Env.CACHE_TYPE == "in-memory":
        getattr(entry, name)(payload)
    else:
        registered = Broker.celery.tasks["langboard_shared.tasks.docling.WikiDocumentTask." + name]
        registered.run(payload)
    assert closed.wait(5)
    document = b.docs.get_readable(b.actor, b.source_board, attachment)[2]
    assert document.status == "indexed"
    if name == "embed_wiki_attachment":
        assert document.embedding["status"] == "indexed"
