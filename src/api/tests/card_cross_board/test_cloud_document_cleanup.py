# ruff: noqa: F811, F401 - pytest resolves imported native fixtures by parameter name.
"""Cloud replacement fences native publications atomically; task dispatch is a boundary."""

from copy import deepcopy
from hashlib import sha256
from unittest.mock import Mock
import pytest
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.storage import Storage
from langboard_shared.domain.models import (
    CardAttachment,
    CardDocumentArtifact,
    CardMetadata,
    DocumentExtractionCache,
    DocumentExtractionCacheReference,
    DocumentKeywordIndex,
    DocumentVectorCleanup,
    ProjectAttachmentUploadReceipt,
    ProjectWikiAttachment,
    WikiAttachmentDocument,
)
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.factory.CardAttachmentService import CardAttachmentService
from langboard_shared.domain.services.factory.CardService import CardService
from langboard_shared.domain.services.factory.DoclingMetadataService import DoclingMetadataService
from langboard_shared.domain.services.factory.ProjectWikiService import ProjectWikiService
from langboard_shared.domain.services.factory.WikiDocumentService import WikiDocumentService
from langboard_shared.tasks.docling import DocumentCleanupTask, DocumentKeywordTask
from src.api.tests.card_cross_board.test_cloud_source_duplicates import choose, upload
from src.api.tests.card_cross_board.test_cloud_source_duplicates import sources as base_sources
from src.api.tests.card_visibility.conftest import current_card as current_card
from src.api.tests.card_visibility.test_board_cloud import cloud as base_cloud


pytestmark = pytest.mark.parametrize("current_card", ["sqlite://", "postgresql-test"], indirect=True)


def rows(model, *, with_deleted=False):
    with DbSession.use(readonly=False) as db:
        return db.exec(SqlBuilder.select.table(model, with_deleted=with_deleted)).all()


@pytest.fixture
def documents(base_sources, monkeypatch):
    f = base_sources
    for model in (
        CardMetadata,
        CardDocumentArtifact,
        DocumentExtractionCache,
        DocumentExtractionCacheReference,
        DocumentKeywordIndex,
        DocumentVectorCleanup,
        WikiAttachmentDocument,
    ):
        model.__table__.create(f.engine)
    f.native = DomainService()
    services = {
        CardService: f.service.card,
        CardAttachmentService: f.service.card_attachment,
        ProjectWikiService: f.service.project_wiki,
        DoclingMetadataService: f.native.docling_metadata,
        WikiDocumentService: f.native.wiki_document,
    }
    for service in (f.service.project_attachment, f.service.card_attachment):
        monkeypatch.setattr(service, "_get_service", lambda cls: services[cls])
    # Cleanup and fencing remain native. Only post-commit work leaves this process.
    f.wiki_dispatch = Mock()
    f.vector_dispatch, f.keyword_dispatch = Mock(), Mock()
    monkeypatch.setattr(f.native.wiki_document, "request", f.wiki_dispatch)
    monkeypatch.setattr(DocumentCleanupTask, "enqueue_cleanup", f.vector_dispatch)
    monkeypatch.setattr(DocumentKeywordTask, "enqueue", f.keyword_dispatch)
    f.pointer = {"namespace": "selected-old-vectors", "attachment_uid": f.card_file.get_uid()}
    f.card_documents = [
        {
            "attachment_uid": f.card_file.get_uid(),
            "generation": "old-card-generation",
            "status": "indexed",
            "content_hash": sha256(b"original").hexdigest(),
            "content": {"markdown": "old selected text"},
            "embedding": {"pointer": f.pointer},
        },
        {
            "attachment_uid": f.image.get_uid(),
            "generation": "unrelated-generation",
            "status": "indexed",
            "content": {"markdown": "unrelated text"},
        },
    ]
    f.native.docling_metadata.save_documents(CardMetadata, f.card, f.card_documents)
    with DbSession.atomic() as db:
        for attachment, text in ((f.card_file, "old selected text"), (f.image, "unrelated text")):
            current = db.exec(SqlBuilder.select.table(CardAttachment).where(CardAttachment.id == attachment.id)).first()
            current.document_text = text
            db.update(current)
            db.insert(CardDocumentArtifact(attachment_id=current.id, document_json='{"old":true}'))
            db.insert(
                DocumentKeywordIndex(
                    attachment_id=current.id,
                    card_id=f.card.id,
                    project_id=f.project.id,
                    destination_fingerprint="fixture",
                    destination={"endpoint": "https://fixture.invalid", "index": "test"},
                    source_generation="old-card-generation",
                    version=3,
                    completed_version=3,
                )
            )
        snapshot = {"file": f.wiki_file.file.model_dump(mode="json"), "generation": "old-wiki-generation"}
        db.insert(
            WikiAttachmentDocument(
                attachment_id=f.wiki_file.id,
                requested_by_id=f.actor.id,
                source_revision=f.native.wiki_document.source_revision(f.wiki_file),
                generation="old-wiki-generation",
                status="processing",
                markdown="published wiki text",
                embedding={"pointer": {"namespace": "old-wiki-vectors"}},
                text_source=snapshot,
                vector_source=snapshot,
            )
        )
    return f


def state(f):
    """Read durable public models, independent of service identity-map objects."""
    return {
        "attachments": [
            (row.id, row.file.model_dump(), row.content_sha256, getattr(row, "document_text", None))
            for model in (CardAttachment, ProjectWikiAttachment)
            for row in rows(model, with_deleted=True)
        ],
        "metadata": f.native.docling_metadata.load_documents(CardMetadata, f.card),
        "artifacts": [(row.attachment_id, row.document_json) for row in rows(CardDocumentArtifact)],
        "keywords": [(row.attachment_id, row.operation, row.version) for row in rows(DocumentKeywordIndex)],
        "wiki": [
            {key: deepcopy(getattr(row, key)) for key in type(row).model_fields} for row in rows(WikiAttachmentDocument)
        ],
        "cleanup": [row.pointer for row in rows(DocumentVectorCleanup)],
        "receipts": [row.model_dump() for row in rows(ProjectAttachmentUploadReceipt)],
    }


def test_changed_card_bytes_retain_published_projections_and_fence_old_worker(documents):
    f = documents
    before = state(f)
    old_file = f.card_file.file.model_copy()
    result = choose(f, upload(f), "card")
    assert result["uid"] == f.card_file.get_uid()
    current = next(row for row in rows(CardAttachment) if row.id == f.card_file.id)
    assert Storage.get_file(current.file) == b"replacement" and Storage.get_file(old_file) == b"original"
    assert current.document_text == "old selected text"
    documents_by_uid = {doc["attachment_uid"]: doc for doc in f.native.docling_metadata.load_documents(CardMetadata, f.card)}
    published, unrelated = documents_by_uid[f.card_file.get_uid()], documents_by_uid[f.image.get_uid()]
    assert unrelated == f.card_documents[1]
    assert published["generation"] != "old-card-generation" and published["status"] == "disabled"
    assert published["content"] == f.card_documents[0]["content"]
    assert published["embedding"] == f.card_documents[0]["embedding"]
    assert published["text_source"]["file"] == old_file.model_dump(mode="json")
    assert published["text_source"]["generation"] == "old-card-generation"
    assert published["vector_source"] == published["text_source"]
    assert published["requested_source"]["file"] == current.file.model_dump(mode="json")
    assert f.native.docling_metadata.readable_source_matches(f.card, current, published)
    assert state(f)["artifacts"] == before["artifacts"]
    assert state(f)["keywords"] == before["keywords"]
    assert not rows(DocumentVectorCleanup)
    assert not f.native.docling_metadata.mark_document_indexed(
        CardMetadata,
        f.card,
        f.card_file.get_uid(),
        "pdf",
        "old-hash",
        {"markdown": "stale worker output"},
        generation="old-card-generation",
    )
    assert {doc["attachment_uid"]: doc for doc in state(f)["metadata"]} == documents_by_uid
    assert next(row for row in rows(CardAttachment) if row.id == f.card_file.id).document_text == "old selected text"
    f.vector_dispatch.assert_not_called()
    f.keyword_dispatch.assert_not_called()
    f.wiki_dispatch.assert_not_called()
    assert f.service.card_attachment.dispatch_created.call_args.kwargs["include_document_processing"] is True


def test_changed_wiki_bytes_fence_old_generation_and_preserve_published_snapshots(documents):
    f = documents
    before = state(f)
    result = choose(f, upload(f), "wiki")
    assert result["uid"] == f.wiki_file.get_uid()
    document = rows(WikiAttachmentDocument)[0]
    attachment = rows(ProjectWikiAttachment)[0]
    assert Storage.get_file(attachment.file) == b"replacement"
    assert document.generation != "old-wiki-generation" and document.status == "pending"
    assert document.source_revision == f.native.wiki_document.source_revision(attachment)
    assert document.markdown == "published wiki text"
    for key in ("embedding", "text_source", "vector_source"):
        assert getattr(document, key) == before["wiki"][0][key]
    assert not f.native.wiki_document.publish_extraction(
        attachment.get_uid(),
        "old-wiki-generation",
        markdown="stale worker output",
        keywords={},
        document_json={},
    )
    assert rows(WikiAttachmentDocument)[0].markdown == "published wiki text"
    assert state(f)["metadata"] == before["metadata"]
    assert state(f)["artifacts"] == before["artifacts"]
    assert state(f)["keywords"] == before["keywords"]
    assert not rows(DocumentVectorCleanup)
    f.wiki_dispatch.assert_called_once()
    assert f.wiki_dispatch.call_args.kwargs == {"automatic": True, "force": True}


@pytest.mark.parametrize("kind", ["card", "wiki"])
def test_failure_after_native_cleanup_rolls_back_attachment_metadata_and_dispatch(documents, monkeypatch, kind):
    f = documents
    preview, before = upload(f), deepcopy(state(f))
    insert = DbSession.insert

    def reject_receipt(db, row, *args, **kwargs):
        if isinstance(row, ProjectAttachmentUploadReceipt):
            # Receipt is written after native fencing, before the owning commit.
            if kind == "card":
                published = f.native.docling_metadata.get_document_by_attachment_uid(
                    CardMetadata, f.card, f.card_file.get_uid()
                )
                assert published["generation"] != "old-card-generation"
                assert published["text_source"]["generation"] == "old-card-generation"
                assert published["content"] == f.card_documents[0]["content"]
            else:
                assert rows(WikiAttachmentDocument)[0].generation != "old-wiki-generation"
            raise RuntimeError("receipt persistence failed after native cleanup")
        return insert(db, row, *args, **kwargs)

    monkeypatch.setattr(DbSession, "insert", reject_receipt)
    with pytest.raises(RuntimeError, match="receipt persistence failed"):
        choose(f, preview, kind)
    assert state(f) == before
    for row in (f.card_file, f.wiki_file):
        assert Storage.get_file(row.file) == b"original"
    f.vector_dispatch.assert_not_called()
    f.keyword_dispatch.assert_not_called()
    f.wiki_dispatch.assert_not_called()


@pytest.mark.parametrize("kind", ["card", "wiki"])
def test_identical_bytes_keep_native_document_state_and_never_dispatch_processing(documents, kind):
    f = documents
    before = state(f)
    result = choose(f, upload(f, content=b"original"), kind, content=b"original")
    assert result["source"]["kind"] == kind
    after = state(f)
    assert len(after.pop("receipts")) == 1
    before.pop("receipts")
    assert after == before
    f.vector_dispatch.assert_not_called()
    f.keyword_dispatch.assert_not_called()
    f.wiki_dispatch.assert_not_called()
    if kind == "card":
        assert f.service.card_attachment.dispatch_created.call_args.kwargs["include_document_processing"] is False
