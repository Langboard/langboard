# ruff: noqa: F811 - pytest resolves imported native fixtures by parameter name.
"""Cloud duplicate decisions preserve current native source authority and provenance."""

from hashlib import sha256
from io import BytesIO
from unittest.mock import Mock
from uuid import uuid4
import pytest
from fastapi import UploadFile
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.core.storage import Storage, StorageName
from langboard_shared.core.storage.LocalStorage import LocalStorage
from langboard_shared.domain.models import (
    Card,
    CardAttachment,
    ProjectAttachment,
    ProjectAttachmentUploadReceipt,
    ProjectRole,
    ProjectWikiAssignedUser,
    ProjectWikiAttachment,
)
from langboard_shared.domain.services.factory.CardAttachmentService import CardAttachmentService
from langboard_shared.domain.services.factory.CardService import CardService
from langboard_shared.domain.services.factory.DoclingMetadataService import DoclingMetadataService
from langboard_shared.domain.services.factory.ProjectDocumentService import ProjectDocumentService
from langboard_shared.domain.services.factory.ProjectWikiService import ProjectWikiService
from langboard_shared.domain.services.factory.WikiDocumentService import WikiDocumentService
from langboard_shared.Env import Env
from sqlalchemy import delete, update
from src.api.tests.card_visibility.conftest import current_card as current_card
from src.api.tests.card_visibility.test_board_cloud import cloud as base_cloud  # noqa: F401


pytestmark = pytest.mark.parametrize("current_card", ["sqlite://", "postgresql-test"], indirect=True)


@pytest.fixture
def sources(base_cloud, monkeypatch, tmp_path):
    f = base_cloud
    ProjectAttachmentUploadReceipt.__table__.create(f.engine)
    monkeypatch.setattr(type(Env), "LOCAL_STORAGE_DIR", property(lambda _: tmp_path / "objects"))
    monkeypatch.setattr(Storage, "_storages", {"local": LocalStorage()})
    f.documents = Mock()
    services = {
        CardService: f.service.card,
        CardAttachmentService: f.service.card_attachment,
        ProjectWikiService: f.service.project_wiki,
        ProjectDocumentService: f.documents,
        WikiDocumentService: f.documents,
        DoclingMetadataService: f.documents,
    }
    for native in (f.service.project_attachment, f.service.card_attachment):
        monkeypatch.setattr(native, "_get_service", lambda cls: services[cls])
    monkeypatch.setattr(f.service.card_attachment, "dispatch_created", Mock())
    monkeypatch.setattr(f.service.card_attachment, "_mark_card_changed_for_unread", Mock())
    for row, storage in ((f.card_file, StorageName.CardAttachment), (f.wiki_file, StorageName.Wiki)):
        file = Storage.upload_named(BytesIO(b"original"), storage, f"native-{row.get_uid()}.pdf", "same.pdf")
        with DbSession.atomic() as db:
            current = db.exec(SqlBuilder.select.table(type(row)).where(type(row).id == row.id)).first()
            current.file = file
            current.content_sha256 = sha256(b"original").hexdigest()
            db.update(current)
        row.file = file
    return f


def upload(f, content=b"replacement", **kwargs):
    return f.service.project_attachment.upload(
        f.actor,
        f.project,
        UploadFile(BytesIO(content), filename="same.pdf"),
        request_uid=kwargs.pop("request_uid", str(uuid4())),
        channel=CollaborationChannel.HumanUI,
        **kwargs,
    )


def choose(f, preview, kind, **kwargs):
    conflict = next(item for item in preview["conflicts"] if item["source"]["kind"] == kind)
    return upload(
        f,
        request_uid=preview["request_uid"],
        duplicate_action="replace",
        replacement_uid=conflict["uid"],
        replacement_source=kind,
        confirmation_token=preview["confirmation_token"],
        **kwargs,
    )


def test_cloud_duplicate_discovery_includes_native_card_and_wiki(sources):
    result = upload(sources)
    assert result.get("confirmation_required") is True
    assert {item["source"]["kind"] for item in result["conflicts"]} == {"card", "wiki"}
    assert {item["source"]["title"] for item in result["conflicts"]} == {sources.card.title, sources.wiki.title}


@pytest.mark.parametrize("kind", ["card", "wiki"])
def test_native_replacement_keeps_uid_old_bytes_authorized_urls_and_durable_replay(sources, kind):
    f = sources
    row = f.card_file if kind == "card" else f.wiki_file
    old = row.file.model_copy()
    preview = upload(f)
    result = choose(f, preview, kind)
    assert result["uid"] == row.get_uid() and result["source"]["kind"] == kind
    assert Storage.get_file(old) == b"original"
    if kind == "card":
        current = f.service.card_attachment.resolve_file_owner(old.storage_type, old.storage_name, old.filename)
    else:
        current = f.service.project_wiki.resolve_readable_file(
            f.actor, old.storage_type, old.storage_name, old.filename
        )
    assert current.id == row.id and Storage.get_file(current.file) == b"replacement"
    assert choose(f, preview, kind) == {**result, "replayed": True}
    with pytest.raises(ValueError, match="another operation"):
        choose(f, preview, kind, content=b"changed replay bytes")
    with DbSession.use(readonly=False) as db:
        assert len(db.exec(SqlBuilder.select.table(type(row), with_deleted=True)).all()) == (4 if kind == "card" else 2)
        assert len(db.exec(SqlBuilder.select.table(ProjectAttachmentUploadReceipt)).all()) == 1


@pytest.mark.parametrize("kind", ["card", "wiki"])
def test_current_read_revocation_hides_source_and_blocks_receipt_replay(sources, kind):
    f = sources
    preview = upload(f)
    choose(f, preview, kind)
    with f.engine.begin() as db:
        if kind == "card":
            db.execute(update(Card).values(owner_user_id=f.other.id, created_by_user_id=f.other.id))
        else:
            db.execute(delete(ProjectWikiAssignedUser))
    assert all(item["source"]["kind"] != kind for item in upload(f)["conflicts"])
    with pytest.raises(PermissionError):
        choose(f, preview, kind)


@pytest.mark.parametrize("kind", ["card", "wiki"])
def test_unauthorized_replacement_is_explicitly_denied(sources, kind):
    f = sources
    model = CardAttachment if kind == "card" else ProjectWikiAttachment
    with f.engine.begin() as db:
        db.execute(update(model).values(user_id=f.other.id))
    preview = upload(f)
    item = next(item for item in preview["conflicts"] if item["source"]["kind"] == kind)
    assert item["can_replace"] is False
    with pytest.raises(PermissionError):
        choose(f, preview, kind)


@pytest.mark.parametrize("kind", ["card", "wiki"])
def test_stale_target_and_changed_bytes_require_new_confirmation(sources, kind):
    f = sources
    preview = upload(f)
    stale_bytes = choose(f, preview, kind, content=b"different")
    assert stale_bytes["confirmation_required"]
    concurrent = upload(f)
    choose(f, concurrent, kind)
    assert choose(f, preview, kind)["confirmation_required"]


def test_add_preserves_native_files_and_reuses_physical_content(sources, monkeypatch):
    f = sources
    allocator = Mock(wraps=Storage._storages["local"].upload_immutable)
    monkeypatch.setattr(Storage._storages["local"], "upload_immutable", allocator)
    preview = upload(f, content=b"original")
    assert all(item["same_content"] for item in preview["conflicts"])
    result = upload(
        f,
        content=b"original",
        request_uid=preview["request_uid"],
        duplicate_action="add",
        confirmation_token=preview["confirmation_token"],
    )
    assert result["uid"] not in {f.card_file.get_uid(), f.wiki_file.get_uid()}
    assert not allocator.called
    f.documents.request.assert_called_once()
    with DbSession.use(readonly=False) as db:
        assert len(db.exec(SqlBuilder.select.table(ProjectAttachment)).all()) == 1


def test_cross_source_explicit_add_reuses_extraction_and_embedding(sources):
    from langboard_shared.domain.models import (
        DocumentEmbeddingCache,
        DocumentEmbeddingCacheReference,
        DocumentExtractionCache,
        DocumentExtractionCacheReference,
    )
    from langboard_shared.tasks.docling.DocumentEmbeddingCache import cached_document_embeddings
    from langboard_shared.tasks.docling.DocumentExtractionCache import cached_document_extraction
    from langchain_core.embeddings import Embeddings

    f = sources
    for model in (
        DocumentExtractionCache,
        DocumentExtractionCacheReference,
        DocumentEmbeddingCache,
        DocumentEmbeddingCacheReference,
    ):
        model.__table__.create(f.engine)
    preview = upload(f, content=b"original")
    added = upload(
        f,
        content=b"original",
        request_uid=preview["request_uid"],
        duplicate_action="add",
        confirmation_token=preview["confirmation_token"],
    )
    with DbSession.use(readonly=False) as db:
        board_file = db.exec(SqlBuilder.select.table(ProjectAttachment)).first()
    assert board_file.get_uid() == added["uid"]
    converter = Mock(
        return_value={
            "markdown": "Identical native content",
            "keywords": {},
            "document_json": {"schema_name": "DoclingDocument"},
        }
    )

    class Provider(Embeddings):
        calls = 0

        def embed_documents(self, texts):
            self.calls += 1
            return [[1.0, 2.0, 3.0] for _ in texts]

        def embed_query(self, text):
            raise AssertionError("No query inference during upload")

    provider = Provider()
    for row in (f.card_file, f.wiki_file, board_file):
        # All native processing paths use this exact content/configuration-bound cache.
        with cached_document_extraction(
            binding_uid="native-vision",
            content_hash=board_file.content_sha256,
            vision_config={"binding_uid": "native-vision", "model_name": "vision"},
            filename=row.filename,
            timeout_seconds=1,
        ) as extraction:
            result = extraction.get_or_convert(converter)
            extraction.retain()
        with cached_document_embeddings(
            provider,
            binding_uid="native-embedding",
            content_hash=board_file.content_sha256,
            extraction_identity=sha256(result["markdown"].encode()).hexdigest(),
            fingerprint=sha256(b"same official provider configuration").hexdigest(),
            dimensions=3,
            timeout_seconds=1,
        ) as embedding:
            assert embedding.embed_documents([result["markdown"]]) == [[1.0, 2.0, 3.0]]
            embedding.retain()
    assert converter.call_count == provider.calls == 1


@pytest.mark.parametrize("kind", ["card", "wiki"])
def test_same_bytes_native_replace_never_reallocates_or_requeues(sources, monkeypatch, kind):
    f = sources
    allocator = Mock(wraps=Storage.upload_named)
    monkeypatch.setattr(Storage, "upload_named", allocator)
    result = choose(f, upload(f, content=b"original"), kind, content=b"original")
    assert result["source"]["kind"] == kind and not allocator.called
    f.documents.supersede.assert_not_called()
    f.documents.delete_document_by_attachment_uid.assert_not_called()
    f.documents.request.assert_not_called()
    if kind == "card":
        assert f.service.card_attachment.dispatch_created.call_args.kwargs["include_document_processing"] is False


def test_hidden_sources_do_not_change_duplicate_response(sources):
    f = sources
    with f.engine.begin() as db:
        db.execute(update(Card).values(owner_user_id=f.other.id, created_by_user_id=f.other.id))
        db.execute(delete(ProjectWikiAssignedUser))
    result = upload(f)
    assert not result.get("confirmation_required") and "conflicts" not in result
    assert result["project_uid"] == f.project.get_uid()


def test_archived_card_remains_readable_duplicate(sources):
    from langboard_shared.core.types import SafeDateTime

    with sources.engine.begin() as db:
        db.execute(update(Card).values(archived_at=SafeDateTime.now()))
    assert "card" in {item["source"]["kind"] for item in upload(sources)["conflicts"]}


def test_hidden_card_matching_metadata_is_filtered_before_native_resolution(sources, monkeypatch):
    f = sources
    with f.engine.begin() as db:
        db.execute(update(Card).values(owner_user_id=f.other.id, created_by_user_id=f.other.id))
    resolver = Mock(wraps=f.service.card_attachment.resolve_file_owner)
    monkeypatch.setattr(f.service.card_attachment, "resolve_file_owner", resolver)
    result = upload(f)
    assert {item["source"]["kind"] for item in result["conflicts"]} == {"wiki"}
    resolver.assert_not_called()


@pytest.fixture
def native_pipeline(sources, monkeypatch, tmp_path):
    """Keep upload, services, tasks, cache SQL and vector publication native."""
    import asyncio
    from json import dumps
    from pathlib import Path
    from types import SimpleNamespace
    from langboard_shared.core.broker import Broker
    from langboard_shared.domain.models import (
        DocumentEmbeddingCache,
        DocumentEmbeddingCacheReference,
        DocumentExtractionCache,
        DocumentExtractionCacheReference,
        DocumentVectorCleanup,
        ProjectAttachmentDocument,
    )
    from langboard_shared.domain.models.InternalBot import InternalBotType
    from langboard_shared.domain.services import DomainService, ProjectDocumentQuery
    from langboard_shared.domain.services.factory.InternalBotService import InternalBotService
    from langboard_shared.tasks.docling import DocumentEmbedding, ProjectDocumentTask
    from langchain_core.embeddings import Embeddings

    f = sources
    for model in (DocumentEmbeddingCache, DocumentEmbeddingCacheReference, DocumentExtractionCache,
                  DocumentExtractionCacheReference, DocumentVectorCleanup, ProjectAttachmentDocument):
        model.__table__.create(f.engine)
    # This scenario creates its references through the native cloud upload boundary.
    with f.engine.begin() as db:
        db.execute(delete(CardAttachment))
        db.execute(delete(ProjectWikiAttachment))
    monkeypatch.setattr(type(Env), "DATA_DIR", property(lambda _: tmp_path))
    config = {
        "agent_llm": "OpenAI Compatible", "base_url": "https://fixture.invalid", "model_name": "fixture",
        "retrieval": {"enabled": True, "dimensions": 3, "store": "sqlite", "timeout_seconds": 1.0},
    }
    vision = SimpleNamespace(
        value=dumps({"model_name": "fixture", "base_url": "https://fixture.invalid"}),
        get_uid=lambda: "native-vision", bot_type=InternalBotType.DocumentVision,
    )
    embedding = SimpleNamespace(
        value=dumps(config), get_uid=lambda: "native-embedding", bot_type=InternalBotType.DocumentEmbedding,
    )
    monkeypatch.setattr(InternalBotService, "is_document_processing_enabled", lambda _: True)
    monkeypatch.setattr(InternalBotService, "get_document_vision_binding", lambda _: vision)
    monkeypatch.setattr(InternalBotService, "get_document_embedding_binding", lambda _: embedding)
    monkeypatch.setattr(InternalBotService, "get_current_by_id_like", lambda _, uid: {
        "native-vision": vision, "native-embedding": embedding,
    }.get(uid))
    f.sent = Mock()
    monkeypatch.setattr(Broker.celery, "send_task", f.sent)
    native = DomainService()
    # Replace the old test's mocked document service with its real implementation.
    f.documents = native.project_document
    services = {
        CardService: f.service.card, CardAttachmentService: native.card_attachment,
        ProjectWikiService: f.service.project_wiki, ProjectDocumentService: f.documents,
        WikiDocumentService: native.wiki_document, DoclingMetadataService: native.docling_metadata,
    }
    monkeypatch.setattr(f.service.project_attachment, "_get_service", lambda cls: services[cls])
    f.convert_calls = []
    f.fail_conversion = False

    def convert(path, **kwargs):
        content = Path(path).read_bytes()
        f.convert_calls.append(content)
        if f.fail_conversion:
            raise RuntimeError("Deterministic external conversion failure")
        kwargs["on_progress"]()
        if getattr(f, "structured", False):
            from docling_core.types.doc import DocItemLabel, DoclingDocument

            document = DoclingDocument(name="fixture")
            document.add_text(label=DocItemLabel.PARAGRAPH, text="alpha " + content.decode())
            kwargs["on_document"](document.model_dump(mode="json"))
        return "alpha " + content.decode()

    monkeypatch.setattr(ProjectDocumentTask, "_convert_to_markdown", convert)

    class Provider(Embeddings):
        calls = 0
        queries = 0

        def embed_documents(self, texts):
            # LangGraph uses the synchronous batch API for its query text.
            assert texts == ["alpha"], "Publication must use the native async path"
            self.queries += 1
            return [[1.0, 2.0, 3.0] for _ in texts]

        async def aembed_documents(self, texts):
            self.calls += 1
            assert DbSession._atomic_session.get() is None
            await asyncio.sleep(0)
            return [[1.0, 2.0, 3.0] for _ in texts]

        def embed_query(self, text):
            self.queries += 1
            return [1.0, 2.0, 3.0]

    f.provider = Provider()
    monkeypatch.setattr(ProjectDocumentTask, "create_document_embeddings", lambda *_: f.provider)
    monkeypatch.setattr(DocumentEmbedding, "create_document_embeddings", lambda *_: f.provider)
    f.native = native
    f.query = ProjectDocumentQuery

    def process(uid, *, duplicate=False):
        generation = f.documents.load_source(uid)[2].generation
        ProjectDocumentTask._index_project_attachment(native, uid, generation)

        async def embed():
            work = [ProjectDocumentTask._embed_project_attachment(native, uid, generation)]
            if duplicate:
                work.append(ProjectDocumentTask._embed_project_attachment(native, uid, generation))
            await asyncio.gather(*work)

        asyncio.run(embed())
        return f.documents.load_source(uid)[2]

    f.process = process
    try:
        yield f
    finally:
        native.close()


def test_native_duplicate_add_replace_failure_retry_and_search_isolation(native_pipeline):
    """Actual native upload-to-search composition; only model output and queue transport are fixtures."""
    from langboard_shared.domain.models import (
        AttachmentStorageObject,
        AttachmentStorageReference,
        DocumentEmbeddingCache,
        DocumentEmbeddingCacheReference,
        DocumentExtractionCache,
        DocumentExtractionCacheReference,
        DocumentVectorCleanup,
    )
    from langboard_shared.tasks.docling.DocumentCleanupTask import process_cleanup

    f = native_pipeline

    def rows(model):
        with DbSession.use(readonly=False) as db:
            return db.exec(SqlBuilder.select.table(model)).all()

    def read(uid):
        return f.query.read_project_document(f.native, f.actor, f.project.get_uid(), uid)

    def search(uid):
        result = f.query.search_project_document(f.native, f.actor, f.project.get_uid(), uid, "alpha")
        assert result.matches
        assert all(match.source.attachment_uid == uid for match in result.matches)
        return result

    def add(data):
        preview = upload(f, content=data)
        if not preview.get("confirmation_required"):
            return preview
        return upload(f, content=data, request_uid=preview["request_uid"], duplicate_action="add",
                      confirmation_token=preview["confirmation_token"])

    def replace(uid, data):
        preview = upload(f, content=data)
        assert preview["confirmation_required"]
        return upload(f, content=data, request_uid=preview["request_uid"], duplicate_action="replace",
                      replacement_uid=uid, replacement_source="project",
                      confirmation_token=preview["confirmation_token"])

    first = add(b"original")["uid"]
    original = f.process(first)
    second = add(b"original")["uid"]
    duplicate = f.process(second, duplicate=True)
    assert first != second
    assert f.convert_calls == [b"original"] and f.provider.calls == 1
    assert original.embedding["status"] == duplicate.embedding["status"] == "indexed"
    original_pointer = original.embedding["pointer"]
    duplicate_pointer = duplicate.embedding["pointer"]
    assert set(original_pointer["chunk_ids"]).isdisjoint(duplicate_pointer["chunk_ids"])
    for uid in (first, second):
        assert read(uid).text == "alpha original"
        search(uid)
    assert len(rows(DocumentExtractionCache)) == len(rows(DocumentEmbeddingCache)) == 1
    assert len(rows(DocumentExtractionCacheReference)) == len(rows(DocumentEmbeddingCacheReference)) == 2
    assert {(ref.source_uid, ref.generation) for ref in rows(DocumentExtractionCacheReference)} == {
        (first, original.generation), (second, duplicate.generation),
    }
    before = {row.get_uid(): row for row in rows(ProjectAttachment)}
    physical = {row.id: row for row in rows(AttachmentStorageObject)}
    locators = {row.filename: row for row in rows(AttachmentStorageReference)}
    assert locators[before[first].file.filename].object_id == locators[before[second].file.filename].object_id
    assert physical[locators[before[first].file.filename].object_id].content_sha256 == sha256(b"original").hexdigest()
    assert Storage.get_file(before[first].file) == Storage.get_file(before[second].file) == b"original"

    # A replacement that fails extraction keeps both original published projections.
    assert replace(first, b"replacement")["uid"] == first
    f.fail_conversion = True
    failed = f.process(first)
    assert failed.status == "failed"
    assert failed.embedding["pointer"] == original_pointer
    assert failed.vector_source["generation"] == original.generation
    for uid in (first, second):
        assert read(uid).text == "alpha original"
        assert search(uid).generation in {original.generation, duplicate.generation}
    assert f.provider.calls == 1

    # Explicit retry obtains a new generation. Concurrent duplicate delivery shares inference.
    f.fail_conversion = False
    retry = f.documents.request(f.actor, f.project, first, force=True)
    assert retry["generation"] != original.generation
    replaced = f.process(first, duplicate=True)
    assert replaced.embedding["status"] == "indexed"
    assert f.provider.calls == 2
    assert read(first).text == "alpha replacement" and read(second).text == "alpha original"
    assert search(first).generation == replaced.generation
    assert search(second).generation == duplicate.generation
    assert all("replacement" in match.content for match in search(first).matches)
    assert all("replacement" not in match.content and "original" in match.content
               for match in search(second).matches)
    assert replaced.embedding["pointer"]["content_hash"] == sha256(b"replacement").hexdigest()
    assert duplicate_pointer["content_hash"] == sha256(b"original").hexdigest()
    assert f.documents.load_source(second)[2].embedding["pointer"] == duplicate_pointer
    assert Storage.get_file(before[first].file) == Storage.get_file(before[second].file) == b"original"
    assert f.convert_calls == [b"original", b"replacement", b"replacement"]

    # Cleanup through the real durable outbox removes only the replaced generation.
    assert rows(DocumentVectorCleanup)
    for event in rows(DocumentVectorCleanup):
        assert process_cleanup(event.id)
    assert rows(DocumentVectorCleanup) == []
    assert len(rows(DocumentExtractionCache)) == len(rows(DocumentEmbeddingCache)) == 2
    assert len(rows(DocumentExtractionCacheReference)) == 2
    refs = rows(DocumentEmbeddingCacheReference)
    assert len([row for row in refs if row.expires_at is None]) == 2
    # A losing concurrent worker retains a bounded pending inference reference.
    # Exercise the real recovery boundary; published references must survive.
    from datetime import timedelta
    from langboard_shared.core.types import SafeDateTime
    from langboard_shared.tasks.docling.DocumentEmbeddingCache import recover_expired_cache_references

    with DbSession.atomic() as db:
        db.exec(update(DocumentEmbeddingCacheReference).where(
            DocumentEmbeddingCacheReference.expires_at.is_not(None),
        ).values(expires_at=SafeDateTime.now() - timedelta(seconds=1)))
    recover_expired_cache_references()
    assert len(rows(DocumentEmbeddingCacheReference)) == 2
    search(first)
    search(second)

    calls = (len(f.convert_calls), f.provider.calls)
    assert replace(first, b"replacement")["uid"] == first
    f.process(first, duplicate=True)
    assert (len(f.convert_calls), f.provider.calls) == calls
    # Revoking this actor's board role rejects both references before any query inference.
    with f.engine.begin() as db:
        db.execute(update(ProjectRole).values(actions=[]))
    query_calls = f.provider.queries
    for uid in (first, second):
        with pytest.raises(ValueError, match="unavailable"):
            search(uid)
    assert f.provider.queries == query_calls


@pytest.mark.parametrize("api_has_processing", [True, False], ids=["same-runtime", "api-without-processing"])
@pytest.mark.parametrize("structured", [False, True], ids=["text", "structured"])
def test_native_pending_duplicate_survives_original_replacement_and_cleanup(
    native_pipeline, monkeypatch, api_has_processing, structured,
):
    """Acceptance must own reusable results before the duplicate worker starts."""
    from langboard_shared.domain.models import DocumentVectorCleanup
    from langboard_shared.tasks.docling import DocumentExtractionCache as extraction_cache
    from langboard_shared.tasks.docling.DocumentCleanupTask import process_cleanup

    f = native_pipeline
    f.structured = structured
    first = upload(f, content=b"original")["uid"]
    original = f.process(first)
    preview = upload(f, content=b"original")
    actual_version = extraction_cache.metadata.version

    def api_version(distribution):
        if distribution in {"docling", "docling-slim", "pypdfium2", "lxml"}:
            raise extraction_cache.metadata.PackageNotFoundError(distribution)
        return actual_version(distribution)

    with monkeypatch.context() as api_runtime:
        if not api_has_processing:
            import builtins
            actual_import = builtins.__import__

            def without_processing(name, *args, **kwargs):
                if name.startswith(("docling", "pypdfium2", "lxml")):
                    raise ImportError("API does not contain document processing packages")
                return actual_import(name, *args, **kwargs)

            api_runtime.setattr(extraction_cache.metadata, "version", api_version)
            api_runtime.setattr(builtins, "__import__", without_processing)
        second = upload(
            f, content=b"original", request_uid=preview["request_uid"], duplicate_action="add",
            confirmation_token=preview["confirmation_token"],
        )["uid"]
    assert first != second and f.documents.load_source(second)[2].status == "pending"
    assert f.convert_calls == [b"original"] and f.provider.calls == 1

    preview = upload(f, content=b"replacement")
    assert upload(
        f, content=b"replacement", request_uid=preview["request_uid"], duplicate_action="replace",
        replacement_uid=first, replacement_source="project", confirmation_token=preview["confirmation_token"],
    )["uid"] == first
    replaced = f.process(first)
    assert replaced.embedding["status"] == "indexed"
    with DbSession.use(readonly=False) as db:
        cleanup = db.exec(SqlBuilder.select.table(DocumentVectorCleanup)).all()
    assert cleanup
    for event in cleanup:
        assert process_cleanup(event.id)
    with DbSession.use(readonly=False) as db:
        assert db.exec(SqlBuilder.select.table(DocumentVectorCleanup)).all() == []

    duplicate = f.process(second)
    assert duplicate.embedding["status"] == "indexed"
    assert (f.convert_calls, f.provider.calls) == ([b"original", b"replacement"], 2)
    pointers = [replaced.embedding["pointer"], duplicate.embedding["pointer"]]
    assert set(pointers[0]["chunk_ids"]).isdisjoint(pointers[1]["chunk_ids"])
    assert pointers[1]["content_hash"] == original.embedding["pointer"]["content_hash"]
    for uid, text, document in ((first, "alpha replacement", replaced), (second, "alpha original", duplicate)):
        assert f.query.read_project_document(f.native, f.actor, f.project.get_uid(), uid).text == text
        result = f.query.search_project_document(f.native, f.actor, f.project.get_uid(), uid, "alpha")
        assert result.generation == document.generation and result.matches
        assert all(match.source.attachment_uid == uid and text in match.content for match in result.matches)
    with f.engine.begin() as db:
        db.execute(update(ProjectRole).values(actions=[]))
    query_calls = f.provider.queries
    for uid in (first, second):
        with pytest.raises(ValueError, match="unavailable"):
            f.query.read_project_document(f.native, f.actor, f.project.get_uid(), uid)
        with pytest.raises(ValueError, match="unavailable"):
            f.query.search_project_document(f.native, f.actor, f.project.get_uid(), uid, "alpha")
    assert f.provider.queries == query_calls


@pytest.mark.parametrize("kind", ["card", "wiki"])
@pytest.mark.parametrize("finish", ["cancel", "expire"])
def test_native_pending_cache_ownership_across_adapters_and_cleanup(native_pipeline, kind, finish):
    from datetime import timedelta
    from langboard_shared.core.types import SafeDateTime
    from langboard_shared.domain.models import (
        CardDocumentArtifact,
        CardMetadata,
        DocumentEmbeddingCache,
        DocumentEmbeddingCacheReference,
        DocumentExtractionCache,
        DocumentExtractionCacheReference,
        DocumentKeywordIndex,
        DocumentVectorCleanup,
        KeywordSearchConfiguration,
        WikiAttachmentDocument,
    )
    from langboard_shared.tasks.docling.DocumentCleanupTask import process_cleanup, recover_cleanup

    f = native_pipeline
    first = upload(f, content=b"original")["uid"]
    original = f.process(first)
    for model in (CardDocumentArtifact, CardMetadata, DocumentKeywordIndex, KeywordSearchConfiguration,
                  WikiAttachmentDocument):
        model.__table__.create(f.engine, checkfirst=True)
    storage = StorageName.CardAttachment if kind == "card" else StorageName.Wiki
    file = Storage.upload_named(BytesIO(b"original"), storage, f"pending-{uuid4().hex}.pdf", "same.pdf")
    with DbSession.atomic() as db:
        attachment = (
            CardAttachment(user_id=f.actor.id, card_id=f.card.id, filename="same.pdf", file=file,
                           content_sha256=sha256(b"original").hexdigest())
            if kind == "card" else
            ProjectWikiAttachment(user_id=f.actor.id, project_wiki_id=f.wiki.id, filename="same.pdf", file=file,
                                  content_sha256=sha256(b"original").hexdigest())
        )
        db.insert(attachment)
    uid = attachment.get_uid()
    if kind == "card":
        metadata = f.native.docling_metadata
        assert metadata.queue_document(
            CardMetadata, f.card, uid, attachment.filename,
            vision_config=original.vision_config, embedding_config=original.embedding_config,
        )
        queued = metadata.get_document_by_attachment_uid(CardMetadata, f.card, uid)["generation"]
        assert metadata.claim_document(CardMetadata, f.card, uid, attachment.filename, generation=queued)
        generation = metadata.get_document_by_attachment_uid(CardMetadata, f.card, uid)["generation"]
        assert generation != queued
    else:
        generation = f.native.wiki_document.request(f.actor, f.project, attachment)["generation"]

    def rows(model):
        with DbSession.use(readonly=False) as db:
            return db.exec(SqlBuilder.select.table(model)).all()

    pending = [ref for ref in rows(DocumentExtractionCacheReference) if ref.source_uid == uid]
    assert len(pending) == 1 and pending[0].generation == pending[0].use_token == generation
    assert pending[0].expires_at is not None
    assert len([ref for ref in rows(DocumentEmbeddingCacheReference) if ref.use_token == generation]) == 1
    with DbSession.atomic() as db:
        f.documents.invalidate(db, f.documents.load_source(first)[1])
    for event in rows(DocumentVectorCleanup):
        assert process_cleanup(event.id)
    assert len(rows(DocumentExtractionCache)) == len(rows(DocumentEmbeddingCache)) == 1
    assert all(ref.expires_at is not None for ref in rows(DocumentExtractionCacheReference))
    assert all(ref.expires_at is not None for ref in rows(DocumentEmbeddingCacheReference))
    if finish == "cancel":
        if kind == "card":
            metadata.delete_document_by_attachment_uid(CardMetadata, f.card, uid)
        else:
            with DbSession.atomic() as db:
                f.native.wiki_document.invalidate(db, attachment)
    else:
        with DbSession.atomic() as db:
            for model in (DocumentExtractionCacheReference, DocumentEmbeddingCacheReference):
                db.exec(update(model).values(expires_at=SafeDateTime.now() - timedelta(seconds=1)))
        assert recover_cleanup() == 0
    for model in (DocumentExtractionCacheReference, DocumentEmbeddingCacheReference,
                  DocumentExtractionCache, DocumentEmbeddingCache):
        assert rows(model) == []
    assert f.convert_calls == [b"original"] and f.provider.calls == 1
    assert Storage.get_file(file) == b"original"


@pytest.mark.parametrize("chunk_count", [0, 2049])
def test_pending_cache_reservation_never_rechunks_during_acceptance(
    native_pipeline, monkeypatch, chunk_count,
):
    from types import SimpleNamespace
    from langboard_shared.domain.models import DocumentEmbeddingCacheReference
    from langboard_shared.tasks.docling import DocumentSplitter

    f = native_pipeline
    first = upload(f, content=b"original")["uid"]
    f.process(first)
    monkeypatch.setattr(DocumentSplitter, "split_document", lambda *_: [SimpleNamespace(page_content="alpha")] * chunk_count)
    preview = upload(f, content=b"original")
    second = upload(
        f, content=b"original", request_uid=preview["request_uid"], duplicate_action="add",
        confirmation_token=preview["confirmation_token"],
    )["uid"]
    document = f.documents.load_source(second)[2]
    assert document.status == "pending"
    with DbSession.use(readonly=False) as db:
        assert db.exec(SqlBuilder.select.table(DocumentEmbeddingCacheReference).where(
            DocumentEmbeddingCacheReference.use_token == document.generation,
        )).all() != []
    assert f.convert_calls == [b"original"] and f.provider.calls == 1


def test_native_pending_reservation_opposite_project_requeues_use_ordered_locks(native_pipeline, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, local
    from time import sleep
    from langboard_shared.domain.models import DocumentExtractionCache, DocumentExtractionCacheReference, Project
    from langboard_shared.tasks.docling import DocumentExtractionCache as extraction_cache
    from sqlalchemy import text

    f = native_pipeline
    first = upload(f, content=b"original")["uid"]
    f.process(first)
    preview = upload(f, content=b"replacement")
    second = upload(
        f, content=b"replacement", request_uid=preview["request_uid"], duplicate_action="add",
        confirmation_token=preview["confirmation_token"],
    )["uid"]
    f.process(second)
    sources = []
    for actor, content in ((f.actor, b"original"), (f.other, b"replacement")):
        file = Storage.upload_named(BytesIO(content), StorageName.ProjectAttachment, f"pending-{uuid4().hex}.pdf", "same.pdf")
        with DbSession.atomic() as db:
            board = Project(owner_id=actor.id, title="Independent pending source")
            db.insert(board)
            attachment = ProjectAttachment(project_id=board.id, user_id=actor.id, filename="same.pdf", file=file,
                                           content_sha256=sha256(content).hexdigest())
            db.insert(attachment)
        generation = f.documents.request(actor, board, attachment)["generation"]
        sources.append((actor, board, attachment, generation))
    # Simulate source revision drift before explicit requeue. Each request must
    # replace the old pending ownership and reserve the opposite shared identity.
    for (actor, board, attachment, _), content in zip(sources, (b"replacement", b"original"), strict=True):
        file = Storage.upload_named(BytesIO(content), StorageName.ProjectAttachment, f"changed-{uuid4().hex}.pdf", "same.pdf")
        with DbSession.atomic() as db:
            db.exec(update(ProjectAttachment).where(ProjectAttachment.id == attachment.id).values(
                file=file, content_sha256=sha256(content).hexdigest(),
            ))
    barrier = Barrier(2)
    observed = local()
    original_lock = extraction_cache._lock_entry
    postgres = f.engine.dialect.name == "postgresql"

    def lock(db, identifier):
        result = original_lock(db, identifier)
        observed.locks.append(identifier)
        if len(observed.locks) == 1 and postgres:
            sleep(0.05)
        return result

    monkeypatch.setattr(extraction_cache, "_lock_entry", lock)

    def requeue(source):
        actor, board, attachment, previous = source
        observed.locks = []
        with DbSession.atomic() as db:
            if postgres:
                db.exec(text("SET LOCAL lock_timeout = '3s'"))
                db.exec(text("SET LOCAL statement_timeout = '5s'"))
                barrier.wait(timeout=5)
            receipt = f.documents.request(actor, board, attachment, force=True)
        assert receipt["generation"] != previous
        return observed.locks

    if postgres:
        with ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(requeue, source) for source in sources]
            orders = [future.result(10) for future in futures]
    else:
        orders = [requeue(source) for source in sources]
    with DbSession.use(readonly=False) as db:
        cache_ids = sorted(row.id for row in db.exec(SqlBuilder.select.table(DocumentExtractionCache)).all())
        references = db.exec(SqlBuilder.select.table(DocumentExtractionCacheReference)).all()
    assert all(order[:2] == cache_ids for order in orders)
    assert len(references) == 4
    assert not {source[3] for source in sources} & {reference.use_token for reference in references}
    assert f.convert_calls == [b"original", b"replacement"] and f.provider.calls == 2


@pytest.mark.parametrize("change", ["parser", "conversion", "provider", "model", "chunk"])
def test_native_pending_reservation_preserves_worker_settings_identity(native_pipeline, monkeypatch, change):
    from copy import deepcopy
    from json import dumps, loads
    from langboard_shared.domain.models import DocumentVectorCleanup
    from langboard_shared.tasks.docling import DocumentExtractionCache as extraction_cache
    from langboard_shared.tasks.docling.DocumentCleanupTask import process_cleanup

    f = native_pipeline
    first = upload(f, content=b"original")["uid"]
    f.process(first)
    if change in {"provider", "model", "chunk"}:
        binding = f.native.internal_bot.get_document_embedding_binding()
        config = deepcopy(loads(binding.value))
        if change == "provider":
            config["base_url"] = "https://changed.fixture.invalid"
        elif change == "model":
            config["model_name"] = "changed-model"
        else:
            config["retrieval"]["splitter"] = {"chunk_size": 2000}
        binding.value = dumps(config)
    preview = upload(f, content=b"original")
    second = upload(
        f, content=b"original", request_uid=preview["request_uid"], duplicate_action="add",
        confirmation_token=preview["confirmation_token"],
    )["uid"]
    with DbSession.atomic() as db:
        f.documents.invalidate(db, f.documents.load_source(first)[1])
    with DbSession.use(readonly=False) as db:
        events = db.exec(SqlBuilder.select.table(DocumentVectorCleanup)).all()
    for event in events:
        assert process_cleanup(event.id)
    if change == "parser":
        monkeypatch.setattr(extraction_cache.metadata, "version", lambda _: "changed-worker-parser")
    if change == "conversion":
        monkeypatch.setattr(extraction_cache, "CONVERSION_VERSION", "changed-worker-conversion")
    document = f.process(second)
    assert document.embedding["status"] == "indexed" and f.provider.calls == 2
    assert f.convert_calls == ([b"original", b"original"] if change in {"parser", "conversion"} else [b"original"])
    assert f.query.read_project_document(f.native, f.actor, f.project.get_uid(), second).text == "alpha original"


def test_native_replacement_uncertain_inference_never_resends_on_retry(native_pipeline, monkeypatch):
    """A lost model response retains the published vector source and fences retries."""
    from langboard_shared.domain.models import DocumentEmbeddingCache
    from langboard_shared.tasks.docling import DocumentEmbeddingCache as cache_module

    f = native_pipeline
    first = upload(f, content=b"original")["uid"]
    original = f.process(first)
    preview = upload(f, content=b"changed")
    upload(f, content=b"changed", request_uid=preview["request_uid"], duplicate_action="replace",
           replacement_uid=first, replacement_source="project", confirmation_token=preview["confirmation_token"])

    async def uncertain(texts):
        f.provider.calls += 1
        raise TimeoutError("Deterministic provider response loss")

    monkeypatch.setattr(f.provider, "aembed_documents", uncertain)
    failed = f.process(first)
    assert failed.embedding["status"] == "failed"
    assert failed.embedding["pointer"] == original.embedding["pointer"]
    assert failed.vector_source == original.vector_source
    assert f.provider.calls == 2
    found = f.query.search_project_document(f.native, f.actor, f.project.get_uid(), first, "alpha")
    assert found.generation == original.generation
    assert found.matches and all("original" in match.content for match in found.matches)
    with DbSession.use(readonly=False) as db:
        uncertain_rows = db.exec(SqlBuilder.select.table(DocumentEmbeddingCache).where(
            DocumentEmbeddingCache.vector.is_(None),
        )).all()
    assert len(uncertain_rows) == 1
    assert uncertain_rows[0].lease_token and uncertain_rows[0].lease_expires_at is None

    # Narrowly shorten the waiter budget; the durable dispatched fence remains unchanged.
    monkeypatch.setattr(cache_module, "embedding_inference_budget_seconds", lambda _: 0)
    retry = f.documents.request(f.actor, f.project, first, force=True)
    retried = f.process(first)
    assert retried.generation == retry["generation"] and retried.generation != failed.generation
    assert retried.embedding["status"] == "failed" and f.provider.calls == 2
    assert retried.embedding["pointer"] == original.embedding["pointer"]
    assert f.convert_calls == [b"original", b"changed"]
    found = f.query.search_project_document(f.native, f.actor, f.project.get_uid(), first, "alpha")
    assert found.generation == original.generation
    assert found.matches and all("original" in match.content for match in found.matches)


@pytest.mark.parametrize("processing", ["disabled", "pending", "queue_failure", "conversion_failure"])
def test_cloud_card_replacement_retains_independent_native_publications(sources, monkeypatch, tmp_path, processing):
    from types import SimpleNamespace
    from langboard_shared.core.broker import Broker
    from langboard_shared.domain.models import CardDocumentArtifact, CardMetadata, DocumentExtractionCacheReference
    from langboard_shared.domain.services import DomainService
    from langboard_shared.domain.services.factory.CardAttachmentService import DOCLING_INDEX_CARD_ATTACHMENT_TASK
    from test_attachment_duplicate_upload import rows
    from src.api.tests.card_workspace.test_card_document_publication import configure

    f = sources
    native = DomainService()
    b = configure(SimpleNamespace(
        source=f.card, source_board=f.project, uploads=f.service.card_attachment,
        metadata=native.docling_metadata, service=f.service.card, actor=f.actor, engine=f.engine,
    ), monkeypatch, tmp_path)
    previous = f.service.project_attachment._get_service
    monkeypatch.setattr(f.service.project_attachment, "_get_service", lambda cls: (
        b.metadata if cls is DoclingMetadataService else b.uploads if cls is CardAttachmentService else previous(cls)
    ))
    uid = f.card_file.get_uid()
    try:
        assert b.metadata.queue_document(
            CardMetadata, b.source, uid, f.card_file.filename,
            vision_config=b.vision, embedding_config=b.config, requested_by_uid=f.actor.get_uid(),
        )
        original = b.process(uid)
        assert original["embedding"]["status"] == "indexed" and b.search(uid)["matches"]
        b.automatic = processing != "disabled"
        if processing == "queue_failure":
            def send(name, **kwargs):
                if name == DOCLING_INDEX_CARD_ATTACHMENT_TASK:
                    raise RuntimeError("Deterministic broker failure")
            monkeypatch.setattr(Broker.celery, "send_task", send)
        artifact = [row.document_json for row in rows(CardDocumentArtifact)]
        choose(f, upload(f), "card")
        pending = b.document(uid)
        assert pending["generation"] != original["generation"]
        if processing == "conversion_failure":
            b.fail_conversion = True
            assert b.process(uid)["status"] == "failed"
        assert b.read(uid)["content"] == "transcription original"
        assert b.read(uid)["generation"] == b.search(uid)["generation"] == original["generation"]
        assert b.read(uid)["content_hash"] == sha256(b"original").hexdigest()
        assert b.document(uid)["embedding"]["pointer"] == original["embedding"]["pointer"]
        assert [row.document_json for row in rows(CardDocumentArtifact)] == artifact
        assert len([ref for ref in rows(DocumentExtractionCacheReference) if ref.expires_at is None]) == 1
        assert not b.metadata.mark_document_indexed(
            CardMetadata, b.source, uid, "pdf", sha256(b"original").hexdigest(),
            {"markdown": "late Cloud worker"}, generation=original["generation"],
        )
        b.fail_conversion = False
        monkeypatch.setattr(Broker.celery, "send_task", Mock())
        if processing in {"disabled", "conversion_failure"}:
            assert b.uploads.request_document_processing(f.project, f.card, uid, user=f.actor, reprocess=True) == "pending"
        latest = b.process(uid)
        assert latest["embedding"]["status"] == "indexed"
        assert b.read(uid)["content"] == "transcription replacement"
        assert b.search(uid)["generation"] == latest["generation"] != original["generation"]
    finally:
        native.close()
