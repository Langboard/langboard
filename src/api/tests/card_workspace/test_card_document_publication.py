# ruff: noqa: F811 - pytest resolves imported native fixtures by parameter name.
"""Native card publication, retained reads and private ownership on SQLite/PostgreSQL."""

import asyncio
from copy import deepcopy
from hashlib import sha256
from json import dumps, loads
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from langboard.mcp_tools import CardMcp
from langboard_shared.core.broker import Broker
from langboard_shared.core.db import DbSession
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import (
    Card,
    CardAttachment,
    CardDocumentArtifact,
    CardMetadata,
    DocumentEmbeddingCache,
    DocumentEmbeddingCacheReference,
    DocumentExtractionCache,
    DocumentExtractionCacheReference,
    DocumentKeywordIndex,
    DocumentVectorCleanup,
    KeywordSearchConfiguration,
    Project,
    ProjectAssignedUser,
)
from langboard_shared.domain.models.InternalBot import InternalBotType
from langboard_shared.domain.models.ProjectRole import ProjectRoleAction
from langboard_shared.domain.services import KeywordSearchSettings
from langboard_shared.domain.services.CardCrossBoardOperations import _grant
from langboard_shared.domain.services.factory.CardAttachmentService import CardAttachmentService
from langboard_shared.domain.services.factory.DoclingMetadataService import DoclingMetadataService
from langboard_shared.domain.services.factory.InternalBotService import InternalBotService
from langboard_shared.Env import Env
from langboard_shared.helpers import InfraHelper
from langboard_shared.publishers import CardAttachmentPublisher
from langboard_shared.tasks.activities import CardAttachmentActivityTask
from langboard_shared.tasks.bots import CardAttachmentBotTask
from langboard_shared.tasks.docling import DoclingMetadataTask, DocumentEmbedding, DocumentEmbeddingTask
from langboard_shared.tasks.docling.DocumentCleanupTask import process_cleanup
from langboard_shared.tasks.docling.DocumentExtractionCache import (
    cached_document_extraction,
    published_extraction_identity,
)
from langchain_core.embeddings import Embeddings
from sqlalchemy import delete, update
from test_attachment_duplicate_upload import choose, rows, upload
from test_attachment_duplicate_upload import uploads as uploads
from test_card_mirrors import mirror_board as mirror_board


TABLES = (
    CardMetadata, CardDocumentArtifact, DocumentExtractionCache, DocumentExtractionCacheReference,
    DocumentEmbeddingCache, DocumentEmbeddingCacheReference, DocumentVectorCleanup,
    DocumentKeywordIndex, KeywordSearchConfiguration,
)


def configure(b, monkeypatch, tmp_path):
    """Only remote model output and broker delivery are fixtures; storage and SQL are native."""
    for model in TABLES:
        model.__table__.create(b.engine, checkfirst=True)
    monkeypatch.setattr(type(Env), "DATA_DIR", property(lambda _: tmp_path / "data"))
    monkeypatch.setattr(type(Env), "CARD_INTERNAL_ACCESS_MODE", property(lambda _: "project_members"))
    monkeypatch.setattr(KeywordSearchSettings, "enabled", lambda _: False)
    monkeypatch.setattr(CardAttachmentPublisher, "uploaded", Mock())
    monkeypatch.setattr(Broker.celery, "send_task", Mock())
    monkeypatch.setattr(b.metadata, "publish_update", Mock())
    vision = SimpleNamespace(
        value=dumps({"model_name": "fixture-vision", "base_url": "https://fixture.invalid"}),
        get_uid=lambda: "fixture-vision", bot_type=InternalBotType.DocumentVision,
    )
    embedding = SimpleNamespace(
        value=dumps({
            "agent_llm": "OpenAI Compatible", "base_url": "https://fixture.invalid",
            "model_name": "fixture-embedding",
            "retrieval": {"enabled": True, "store": "sqlite", "dimensions": 3, "timeout_seconds": 1.0},
        }),
        get_uid=lambda: "fixture-embedding", bot_type=InternalBotType.DocumentEmbedding,
    )
    b.automatic = True
    internal = SimpleNamespace(
        get_document_vision_binding=lambda: vision,
        get_document_embedding_binding=lambda: embedding,
        is_document_processing_enabled=lambda: b.automatic,
        get_current_by_id_like=lambda uid: {"fixture-vision": vision, "fixture-embedding": embedding}.get(uid),
    )
    previous = b.uploads._get_service
    monkeypatch.setattr(b.uploads, "_get_service", lambda cls: (
        internal if cls is InternalBotService else b.metadata if cls is DoclingMetadataService else previous(cls)
    ))
    monkeypatch.setattr(b.uploads, "dispatch_created", lambda *args, **kwargs: CardAttachmentService.dispatch_created(
        b.uploads, *args, **{**kwargs, "include_bot": False, "include_activity": False},
    ))
    b.worker = SimpleNamespace(
        card=b.service, card_attachment=b.uploads, docling_metadata=b.metadata, internal_bot=internal,
        project=SimpleNamespace(get_by_id_like=lambda uid: InfraHelper.get_by_id_like(Project, uid)),
    )
    def actions(actor, project):
        with DbSession.use(readonly=False) as db:
            return ["read"] if _grant(db, actor, project, ProjectRoleAction.Read) else []
    b.reader = SimpleNamespace(**{**vars(b.worker), "project": SimpleNamespace(get_user_role_actions_by_project=actions)})
    b.converted = []
    b.fail_conversion = False

    def convert(path, **kwargs):
        from docling_core.types.doc import BoundingBox, DocItemLabel, DoclingDocument, ProvenanceItem, Size
        content = Path(path).read_bytes()
        b.converted.append(content)
        if b.fail_conversion:
            raise RuntimeError("Deterministic conversion failure")
        kwargs["on_progress"](1, 1)
        text = "transcription " + content.decode()
        document = DoclingDocument(name="fixture")
        document.add_page(page_no=1, size=Size(width=600, height=800))
        document.add_text(label=DocItemLabel.PARAGRAPH, text=text, prov=ProvenanceItem(
            page_no=1, bbox=BoundingBox(l=0, t=0, r=600, b=800), charspan=(0, len(text)),
        ))
        kwargs["on_document"](document.export_to_dict())
        kwargs["on_keywords"]({"en": ["published"]})
        return text
    monkeypatch.setattr(DoclingMetadataTask, "_convert_to_markdown", convert)
    monkeypatch.setattr(DoclingMetadataTask, "find_spec", lambda _: object())

    class Provider(Embeddings):
        calls = 0
        fail = False
        def embed_documents(self, texts):
            assert texts == ["transcription"], "Publication must use native async embedding"
            return [[1.0, 2.0, 3.0] for _ in texts]
        async def aembed_documents(self, texts):
            assert not DbSession.has_active_transaction()
            self.calls += 1
            if self.fail:
                raise RuntimeError("Deterministic embedding failure")
            await asyncio.sleep(0)
            return [[1.0, 2.0, 3.0] for _ in texts]
        def embed_query(self, text):
            return [1.0, 2.0, 3.0]
    b.provider = Provider()
    monkeypatch.setattr(DocumentEmbeddingTask, "create_document_embeddings", lambda *_: b.provider)
    monkeypatch.setattr(DocumentEmbedding, "create_document_embeddings", lambda *_: b.provider)
    monkeypatch.setenv("MODEL_PROVIDER_ALLOWED_BASE_URLS", "https://fixture.invalid")
    b.vision = b.uploads._snapshot_vision_config(vision)
    b.config = b.uploads._snapshot_embedding_config()
    b.document = lambda uid: b.metadata.get_document_by_attachment_uid(CardMetadata, b.source, uid)
    def read(uid, **kwargs):
        return CardMcp.read_card_document(b.source_board.get_uid(), b.source.get_uid(), uid, b.actor, b.reader, **kwargs)
    b.read = read
    b.search = lambda uid: CardMcp.search_card_document(
        b.source_board.get_uid(), b.source.get_uid(), uid, "transcription", b.actor, b.reader,
    )
    def process(uid, *, vectors=True):
        attachment = b.uploads.get_by_id_like(uid, consistent=True)
        DoclingMetadataTask._index_card_attachment(b.worker, attachment, generation=b.document(uid)["generation"])
        current = b.document(uid)
        if vectors and current["status"] == "indexed":
            asyncio.run(DocumentEmbeddingTask.embed_transcription(b.worker, uid, current["generation"]))
        return b.document(uid)
    b.process = process
    return b


@pytest.fixture
def pipeline(uploads, monkeypatch, tmp_path):
    b = configure(uploads, monkeypatch, tmp_path)
    uid = upload(b)["uid"]
    b.attachment = b.uploads.get_by_id_like(uid, consistent=True)
    b.first = b.process(uid)
    assert b.first["embedding"]["status"] == "indexed"
    assert b.read(uid)["content"] == "transcription first" and b.search(uid)["matches"]
    return b


@pytest.mark.parametrize("mirror_board", ["sqlite", "postgresql-test"], indirect=True)
def test_text_and_vector_publications_advance_independently_and_retire_selected_outputs(pipeline):
    b = pipeline
    uid = b.attachment.get_uid()
    old_pointer = deepcopy(b.first["embedding"]["pointer"])
    old_generation = b.first["generation"]
    choose(b, upload(b, content=b"second"))
    assert b.document(uid)["generation"] != old_generation
    assert b.read(uid)["generation"] == b.search(uid)["generation"] == old_generation
    assert b.read(uid)["content_hash"] == sha256(b"first").hexdigest()
    assert not b.metadata.claim_document(CardMetadata, b.source, uid, "report.pdf", generation=old_generation)
    assert not b.metadata.mark_document_indexed(
        CardMetadata, b.source, uid, "pdf", sha256(b"first").hexdigest(),
        {"markdown": "late result"}, generation=old_generation,
    )
    text = b.process(uid, vectors=False)
    assert b.read(uid)["generation"] == text["generation"] != old_generation
    assert b.read(uid)["content"] == "transcription second"
    assert b.search(uid)["generation"] == old_generation
    assert rows(DocumentVectorCleanup) == []
    refs = rows(DocumentExtractionCacheReference)
    assert len(refs) == 1 and refs[0].generation == text["generation"] and refs[0].expires_at is None
    assert published_extraction_identity("card_attachment", uid, text["generation"])
    b.provider.fail = True
    asyncio.run(DocumentEmbeddingTask.embed_transcription(b.worker, uid, text["generation"]))
    assert b.document(uid)["embedding"]["status"] == "failed"
    assert b.search(uid)["generation"] == old_generation
    # Retry under a different current embedding model avoids the uncertain failed inference key.
    b.provider.fail = False
    snapshot = {**b.config, "model_name": "fixture-retry"}
    state = b.document(uid)
    state["embedding_config"] = snapshot
    b.metadata.upsert_document(CardMetadata, b.source, state, expected_generation=text["generation"])
    asyncio.run(DocumentEmbeddingTask.embed_transcription(b.worker, uid, text["generation"]))
    latest = b.document(uid)
    assert latest["embedding"]["status"] == "indexed"
    assert latest["vector_source"]["generation"] == latest["text_source"]["generation"] == text["generation"]
    assert b.search(uid)["generation"] == text["generation"]
    cleanup = rows(DocumentVectorCleanup)
    assert len(cleanup) == 1
    assert cleanup[0].pointer == {**old_pointer, "storage": {
        key: value for key, value in old_pointer["storage"].items() if key != "cache_reference_count"
    }}
    assert process_cleanup(int(cleanup[0].id))
    assert rows(DocumentVectorCleanup) == []
    active = [ref for ref in rows(DocumentEmbeddingCacheReference) if ref.expires_at is None]
    assert len(active) == 1 and active[0].use_token != old_pointer["storage"]["cache_use_token"]
    assert b.search(uid)["matches"]


@pytest.mark.parametrize("mirror_board", ["sqlite", "postgresql-test"], indirect=True)
def test_publication_and_replacement_roll_back_and_terminal_deletion_releases_owners(pipeline):
    b = pipeline
    uid = b.attachment.get_uid()
    before = deepcopy(b.document(uid))
    references = [(r.use_token, r.generation) for r in rows(DocumentExtractionCacheReference)]
    with pytest.raises(RuntimeError, match="rollback"):
        with DbSession.atomic():
            b.metadata.supersede_document(b.source, b.attachment)
            raise RuntimeError("rollback")
    assert b.document(uid) == before
    assert b.metadata.queue_document(
        CardMetadata, b.source, uid, "report.pdf", vision_config=b.vision, embedding_config=b.config, force=True,
        requested_by_uid=b.actor.get_uid(),
    )
    queued = b.document(uid)["generation"]
    assert b.metadata.claim_document(CardMetadata, b.source, uid, "report.pdf", generation=queued)
    generation = b.document(uid)["generation"]
    with cached_document_extraction(
        binding_uid="fixture-vision", content_hash=b.attachment.content_sha256,
        vision_config=b.vision, filename="report.pdf", timeout_seconds=1,
    ) as cache:
        result = cache.get_or_convert(lambda: pytest.fail("Native same-byte extraction must reuse the active cache"))
        with pytest.raises(RuntimeError, match="rollback"):
            with DbSession.atomic():
                assert b.metadata.mark_document_indexed(
                    CardMetadata, b.source, uid, "pdf", b.attachment.content_sha256,
                    {"markdown": result["markdown"], "docling_document": result["document_json"]},
                    generation=generation, extraction_cache_token=cache.token,
                )
                raise RuntimeError("rollback")
    assert [(r.use_token, r.generation) for r in rows(DocumentExtractionCacheReference) if r.expires_at is None] == references
    assert b.read(uid)["generation"] == before["generation"]
    assert b.search(uid)["generation"] == before["generation"]
    b.metadata.delete_document_by_attachment_uid(CardMetadata, b.source, uid)
    assert rows(DocumentExtractionCacheReference) == rows(DocumentExtractionCache) == rows(CardDocumentArtifact) == []
    assert b.uploads.get_by_id_like(uid, consistent=True).document_text == ""
    assert len(rows(DocumentVectorCleanup)) == 1
    assert process_cleanup(int(rows(DocumentVectorCleanup)[0].id))
    assert rows(DocumentEmbeddingCacheReference) == rows(DocumentEmbeddingCache) == []
    with pytest.raises(ValueError, match="unavailable"):
        b.read(uid)


@pytest.mark.parametrize("mirror_board", ["sqlite", "postgresql-test"], indirect=True)
@pytest.mark.parametrize("change", ["membership", "private", "moved", "deleted", "hash", "foreign"])
def test_current_request_authority_and_source_fence_publication(pipeline, change):
    b = pipeline
    uid = b.attachment.get_uid()
    choose(b, upload(b, content=b"second"))
    generation = b.document(uid)["generation"]
    assert b.metadata.claim_document(CardMetadata, b.source, uid, "report.pdf", generation=generation)
    generation = b.document(uid)["generation"]
    with DbSession.atomic() as db:
        if change == "membership":
            db.exec(delete(ProjectAssignedUser).where(ProjectAssignedUser.user_id == b.actor.id))
        elif change == "private":
            db.exec(update(Card).where(Card.id == b.source.id).values(visibility="PRIVATE", owner_user_id=b.outsider.id))
        elif change == "moved":
            db.exec(update(Card).where(Card.id == b.source.id).values(project_id=b.destination.id))
        elif change == "deleted":
            db.exec(update(CardAttachment).where(CardAttachment.id == b.attachment.id).values(deleted_at=SafeDateTime.now()))
        elif change == "hash":
            db.exec(update(CardAttachment).where(CardAttachment.id == b.attachment.id).values(content_sha256="a" * 64))
    target = b.attachment.get_uid() if change != "foreign" else b.source.get_uid()
    assert not b.metadata.mark_document_indexed(
        CardMetadata, b.source, target, "pdf", sha256(b"second").hexdigest(),
        {"markdown": "unauthorized result"}, generation=generation,
    )
    assert not b.metadata.publish_document_embedding(
        b.source, target, generation, sha256(b"second").hexdigest(), b.first["embedding"],
    )
    if change == "deleted":
        assert b.uploads.get_by_id_like(uid, consistent=True) is None
    assert rows(DocumentVectorCleanup) == []
    if change in {"membership", "private", "moved", "deleted", "hash"}:
        with pytest.raises(ValueError, match="unavailable"):
            b.read(uid)


@pytest.mark.parametrize("mirror_board", ["sqlite", "postgresql-test"], indirect=True)
def test_same_bytes_are_noop_and_additional_owners_reuse_private_work_independently(pipeline):
    b = pipeline
    uid = b.attachment.get_uid()
    before = deepcopy(b.document(uid))
    choose(b, upload(b, content=b"first"), content=b"first")
    assert b.document(uid) == before and len(b.converted) == b.provider.calls == 1
    result = choose(b, upload(b, name="additional.pdf", content=b"first"),
                    action="add", name="additional.pdf", content=b"first")
    second = b.process(result["uid"])
    assert result["uid"] != uid and b.uploads.get_by_id_like(result["uid"], consistent=True).file != b.attachment.file
    assert second["embedding"]["status"] == "indexed" and len(b.converted) == b.provider.calls == 1
    assert len(rows(DocumentExtractionCacheReference)) == len(rows(DocumentEmbeddingCacheReference)) == 2
    b.metadata.delete_document_by_attachment_uid(CardMetadata, b.source, uid)
    for event in rows(DocumentVectorCleanup):
        assert process_cleanup(int(event.id))
    assert len(rows(DocumentExtractionCacheReference)) == len(rows(DocumentEmbeddingCacheReference)) == 1
    assert b.read(result["uid"])["content"] == "transcription first" and b.search(result["uid"])["matches"]
    b.metadata.delete_document_by_attachment_uid(CardMetadata, b.source, result["uid"])
    for event in rows(DocumentVectorCleanup):
        assert process_cleanup(int(event.id))
    assert rows(DocumentExtractionCache) == rows(DocumentEmbeddingCache) == []


@pytest.mark.parametrize("mirror_board", ["sqlite", "postgresql-test"], indirect=True)
def test_retained_text_pages_search_and_keywords_use_published_source_labels(pipeline, monkeypatch):
    from langboard_shared.domain.services import DomainService
    from langboard_shared.tasks.docling import DocumentKeywordTask

    b = pipeline
    uid = b.attachment.get_uid()
    b.automatic = False
    original = deepcopy(b.first)
    choose(b, upload(b, content=b"second"))
    monkeypatch.setattr(CardAttachmentPublisher, "name_changed", Mock())
    monkeypatch.setattr(CardAttachmentActivityTask, "card_attachment_name_changed", Mock())
    monkeypatch.setattr(CardAttachmentBotTask, "card_attachment_name_changed", Mock())
    assert b.uploads.change_name(b.actor, b.source_board, b.source, uid, "renamed.pdf")
    assert b.uploads.get_by_id_like(uid, consistent=True).filename == "renamed.pdf"
    assert b.read(uid)["filename"] == b.read(uid, page=1)["filename"] == "report.pdf"
    assert "transcription first" in b.read(uid, page=1)["content"]
    _, _, context = b.service.resolve_readable_card(b.source_board, b.source, b.actor)
    repository = b.service.repo.card
    matches = repository.search_document_matches(b.source_board, [b.source.id], "transcription", context=context)
    assert matches[b.source.id][0]["filename"] == "report.pdf"
    candidates = [{"attachment_uid": uid, "card_uid": b.source.get_uid(),
                   "projection_hash": sha256("transcription first\n\npublished".encode()).hexdigest()}]
    keyword_matches = repository.validate_document_candidates(b.source_board, candidates, context=context)
    assert keyword_matches[b.source.id][0]["filename"] == "report.pdf"
    native = DomainService()
    class PayloadService:
        docling_metadata = native.docling_metadata
        def close(self):
            pass
    monkeypatch.setattr(DocumentKeywordTask, "DomainService", PayloadService)
    with DbSession.atomic() as db:
        job = DocumentKeywordIndex(
            attachment_id=b.attachment.id, card_id=b.source.id, project_id=b.source_board.id,
            source_generation=original["generation"], destination_fingerprint="b" * 64,
            destination={"endpoint": "https://keyword.invalid", "index": "fixture"}, claim_token="fixture",
        )
        db.insert(job)
    payload = DocumentKeywordTask._payload(job)
    assert payload["active"] and "transcription first" in payload["text"]
    assert "transcription second" not in payload["text"]
    projected = loads(DoclingMetadataService.project_api_documents(dumps([b.document(uid)]), None))[0]
    assert not {"text_source", "vector_source", "requested_source", "requested_by_uid"}.intersection(projected)
    native.close()


@pytest.mark.parametrize("mirror_board", ["sqlite", "postgresql-test"], indirect=True)
@pytest.mark.parametrize("operation", ["read", "page", "search"])
def test_retained_read_rechecks_membership_after_content_or_provider_work(pipeline, monkeypatch, operation):
    b = pipeline
    uid = b.attachment.get_uid()
    b.automatic = False
    choose(b, upload(b, content=b"second"))
    def revoke():
        with DbSession.atomic() as db:
            db.exec(delete(ProjectAssignedUser).where(ProjectAssignedUser.user_id == b.actor.id))
    if operation == "search":
        original = b.provider.embed_documents
        def query(texts):
            value = original(texts)
            revoke()
            return value
        monkeypatch.setattr(b.provider, "embed_documents", query)
        def call():
            return b.search(uid)
    elif operation == "page":
        original = b.metadata.get_structural_document
        def structure(*args, **kwargs):
            value = original(*args, **kwargs)
            revoke()
            return value
        monkeypatch.setattr(b.metadata, "get_structural_document", structure)
        def call():
            return b.read(uid, page=1)
    else:
        original = b.metadata.get_document_by_attachment_uid
        def document(*args, **kwargs):
            value = original(*args, **kwargs)
            revoke()
            return value
        monkeypatch.setattr(b.metadata, "get_document_by_attachment_uid", document)
        def call():
            return b.read(uid)
    with pytest.raises(ValueError, match="unavailable"):
        call()


@pytest.mark.parametrize("mirror_board", ["sqlite", "postgresql-test"], indirect=True)
def test_keyword_request_follows_claimed_generation_and_only_new_text_activates(pipeline, monkeypatch):
    from langboard_shared.tasks.docling import DocumentKeywordTask

    b = pipeline
    uid = b.attachment.get_uid()
    monkeypatch.setattr(KeywordSearchSettings, "enabled", lambda _: True)
    monkeypatch.setattr(DocumentKeywordTask, "index_configuration", lambda: {
        "endpoint": "https://keyword.invalid", "index": "fixture", "connection_id": None,
    })
    assert b.metadata.queue_document(
        CardMetadata, b.source, uid, "report.pdf", vision_config=b.vision,
        embedding_config=b.config, force=True, requested_by_uid=b.actor.get_uid(),
    )
    queued = b.document(uid)["generation"]
    before = rows(DocumentKeywordIndex)[0]
    assert before.source_generation == queued and before.due_at is None
    completed = b.process(uid, vectors=False)
    current = rows(DocumentKeywordIndex)[0]
    assert current.source_generation == completed["generation"] != queued
    assert current.due_at is not None


@pytest.mark.parametrize("mirror_board", ["sqlite", "postgresql-test"], indirect=True)
@pytest.mark.parametrize("phase", ["before", "during"])
def test_same_byte_filename_proposal_cannot_strand_pending_conversion(pipeline, monkeypatch, phase):
    b = pipeline
    uid = b.attachment.get_uid()
    vision = {**b.vision, "model_name": "explicit-new-vision"}
    assert b.metadata.queue_document(
        CardMetadata, b.source, uid, "report.pdf", vision_config=vision,
        embedding_config=b.config, force=True, requested_by_uid=b.actor.get_uid(),
    )
    def propose_name():
        before = deepcopy(b.document(uid))
        attachment = b.uploads.get_by_id_like(uid, consistent=True)
        choose(b, upload(b, name="proposal.pdf", content=b"first"), name="proposal.pdf", content=b"first")
        current = b.uploads.get_by_id_like(uid, consistent=True)
        assert current.filename == attachment.filename == "report.pdf" and current.file == attachment.file
        assert b.document(uid) == before
    if phase == "before":
        propose_name()
    else:
        original = DoclingMetadataTask._convert_to_markdown
        def convert(*args, **kwargs):
            propose_name()
            return original(*args, **kwargs)
        monkeypatch.setattr(DoclingMetadataTask, "_convert_to_markdown", convert)
    published = b.process(uid)
    assert published["status"] == published["embedding"]["status"] == "indexed"
    assert b.read(uid)["content"] == "transcription first" and b.search(uid)["matches"]


@pytest.mark.parametrize("mirror_board", ["sqlite", "postgresql-test"], indirect=True)
def test_published_filename_transfer_budget(uploads):
    from langboard_shared.core.db import EditorContentModel
    from sqlalchemy import event

    b = uploads
    first = b.uploads.get_by_id_like(upload(b)["uid"], consistent=True)
    filename = "\U0001f680" * 2048
    label = filename[:1024]
    body = "transcription first"
    cards, candidates = [], []
    with DbSession.atomic() as db:
        for _ in range(20):
            card = Card(
                project_id=b.source_board.id, project_column_id=b.source.project_column_id,
                title="Publication transfer budget", description=EditorContentModel(content=""),
                visibility="SHARED", created_by_user_id=b.actor.id,
            )
            db.insert(card)
            cards.append(card)
            documents = []
            for _ in range(2):
                attachment = CardAttachment(
                    card_id=card.id, user_id=b.actor.id, filename="requested.pdf", file=first.file,
                    content_sha256=first.content_sha256, document_text=body,
                )
                db.insert(attachment)
                documents.append({
                    "attachment_uid": attachment.get_uid(), "text_source": {"filename": filename},
                    "content": {"markdown": "PRIVATE_UNTRANSFERRED_DOCUMENT" * 1024},
                    "embedding_config": {"private": "fixture"},
                })
                candidates.append({
                    "attachment_uid": attachment.get_uid(), "card_uid": card.get_uid(),
                    "projection_hash": sha256(body.encode()).hexdigest(),
                })
            db.insert(CardMetadata(card_id=card.id, key="__system.docling_documents", value=dumps(documents)))
    _, _, context = b.service.resolve_readable_card(b.source_board, b.source, b.actor)
    repository = b.service.repo.card
    observed = []

    def observe(connection, cursor, statement, parameters, execution_context, executemany):
        if cursor.description:
            observed.append({"sql": statement, "columns": [item[0] for item in cursor.description]})

    def capture(call, expected_queries, expected_hits):
        observed.clear()
        result = call()
        assert len(observed) == expected_queries
        values = [item for matches in result.values() for item in matches]
        assert len(values) == expected_hits
        assert all(item["filename"] == label for item in values)
        if expected_hits:
            assert len(observed[0]["columns"]) == 4
            assert len(observed[1]["columns"]) == 3
            assert "published_sources" in observed[1]["sql"]
            assert "card_metadata.value," not in observed[1]["sql"]
        return {
            "sql_count": len(observed), "hits": len(values),
            "projection_columns": [query["columns"] for query in observed],
            "returned_filename_characters": len(label) if values else 0,
            "returned_filename_utf8_bytes": len(label.encode()) if values else 0,
            "label_sql": observed[-1]["sql"] if expected_hits else None,
        }

    event.listen(b.engine, "after_cursor_execute", observe)
    try:
        capture(lambda: repository.search_document_matches(
            b.source_board, [card.id for card in cards], "transcription", context=context,
        ), 2, 40)
        capture(lambda: repository.validate_document_candidates(
            b.source_board, candidates, context=context,
        ), 2, 20)
        capture(lambda: repository.search_document_matches(
            b.source_board, [card.id for card in cards], "no hit", context=context,
        ), 1, 0)
        observed.clear()
        with DbSession.use(readonly=False) as db:
            assert repository._published_filenames(db, []) == {}
        assert observed == []
    finally:
        event.remove(b.engine, "after_cursor_execute", observe)


@pytest.mark.parametrize("mirror_board", ["postgresql-test"], indirect=True)
@pytest.mark.parametrize("operation", ["claim", "text", "vectors"])
def test_worker_and_upload_share_native_governance_lock_order(pipeline, operation):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, current_thread
    from sqlalchemy import event

    b = pipeline
    uid = b.attachment.get_uid()
    if operation != "vectors":
        assert b.metadata.queue_document(
            CardMetadata, b.source, uid, "report.pdf", force=True, requested_by_uid=b.actor.get_uid(),
            vision_config={**b.vision, "model_name": "lock-order"}, embedding_config=b.config,
        )
    if operation == "text":
        assert b.metadata.claim_document(CardMetadata, b.source, uid, "report.pdf", generation=b.document(uid)["generation"])
    state = b.document(uid)
    actor_locked, worker_requests_actor, release_upload = Event(), Event(), Event()
    worker_locks, outcomes = [], {}

    def before(connection, cursor, statement, parameters, context, executemany):
        if not current_thread().name.startswith("native-worker"):
            return
        if "FOR UPDATE" in statement:
            worker_locks.append(next((table for table in ('"user"', 'project', 'card', 'card_attachment')
                                      if f"FROM {table} \n" in statement), "other"))
        if 'FROM "user"' in statement:
            worker_requests_actor.set()

    def after(connection, cursor, statement, parameters, context, executemany):
        if (current_thread().name.startswith("native-upload") and 'FROM "user"' in statement
                and "FOR UPDATE" in statement and not actor_locked.is_set()):
            actor_locked.set()
            assert release_upload.wait(8), "Test release gate timed out"

    def worker():
        if operation == "claim":
            return b.metadata.claim_document(CardMetadata, b.source, uid, "report.pdf", generation=state["generation"])
        if operation == "text":
            return b.metadata.mark_document_indexed(
                CardMetadata, b.source, uid, "pdf", b.attachment.content_sha256,
                {"markdown": "transcription serialized"}, generation=state["generation"],
            )
        return b.metadata.publish_document_embedding(
            b.source, uid, state["generation"], state["content_hash"],
            {**state["embedding"], "status": "failed"}, expected_embedding=state["embedding"],
        )

    event.listen(b.engine, "before_cursor_execute", before)
    event.listen(b.engine, "after_cursor_execute", after)
    try:
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="native-upload") as uploader:
            with ThreadPoolExecutor(max_workers=1, thread_name_prefix="native-worker") as workers:
                upload_future = uploader.submit(upload, b, "parallel.pdf", b"parallel")
                assert actor_locked.wait(8), "Native upload did not lock the actor"
                worker_future = workers.submit(worker)
                try:
                    assert worker_requests_actor.wait(8), "Native worker did not check the requester"
                finally:
                    release_upload.set()
                for name, future in (("upload", upload_future), ("worker", worker_future)):
                    try:
                        outcomes[name] = bool(future.result(timeout=12))
                    except Exception as error:
                        outcomes[name] = getattr(getattr(error, "orig", error), "sqlstate", type(error).__name__)
    finally:
        release_upload.set()
        event.remove(b.engine, "before_cursor_execute", before)
        event.remove(b.engine, "after_cursor_execute", after)
    assert outcomes == {"upload": True, "worker": True}
    assert worker_locks[:3] == ['"user"', "project", "card"]
