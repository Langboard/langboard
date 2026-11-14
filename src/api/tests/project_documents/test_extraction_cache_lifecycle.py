"""Native source publication/deletion with real private cache SQL and fixture conversion."""

from datetime import timedelta
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from uuid import uuid4
import pytest
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.storage import FileModel
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import (
    CardAttachment,
    CardDocumentArtifact,
    CardMetadata,
    DocumentEmbeddingCache,
    DocumentEmbeddingCacheReference,
    DocumentKeywordIndex,
    KeywordSearchConfiguration,
    ProjectAssignedUser,
)
from langboard_shared.domain.models.DocumentExtractionCache import (
    DocumentExtractionCache as Cache,
)
from langboard_shared.domain.models.DocumentExtractionCache import (
    DocumentExtractionCacheReference as Reference,
)
from langboard_shared.tasks.docling.DocumentCleanupTask import recover_cleanup
from langboard_shared.tasks.docling.DocumentExtractionCache import (
    PublishedExtractionUnavailable,
    cached_document_extraction,
    published_extraction_identity,
)
from sqlalchemy import delete, update


pytestmark = pytest.mark.parametrize("mirror_board", ["sqlite", "postgresql-test"], indirect=True)


_spec = spec_from_file_location("extraction_lifecycle_documents", Path(__file__).with_name("test_project_document_state.py"))
_module = module_from_spec(_spec)
_spec.loader.exec_module(_module)
documents = _module.documents
upload = _module.upload
source = _module.source


@pytest.fixture
def lifecycle(documents):
    b = documents
    for model in (Cache, Reference, CardAttachment, CardDocumentArtifact, CardMetadata,
                  KeywordSearchConfiguration, DocumentKeywordIndex, DocumentEmbeddingCache,
                  DocumentEmbeddingCacheReference):
        model.__table__.create(b.engine, checkfirst=True)
    return b


def extraction(attachment):
    return cached_document_extraction(
        binding_uid="vision-fixture", content_hash=attachment.content_sha256,
        vision_config={"binding_uid": "vision-fixture", "model_name": "fixture"},
        filename="report.pdf", timeout_seconds=5,
    )


def converted():
    # External OCR/model inference is explicitly a fixture; cache and services use native SQL.
    return {"markdown": "Shared private extraction", "keywords": {"en": ["shared"]},
            "document_json": {"schema_name": "DoclingDocument", "name": "private-original.pdf"}}


def rows(model):
    with DbSession.use(readonly=False) as db:
        return db.exec(SqlBuilder.select.table(model)).all()


def publish(b, attachment, cache):
    generation = b.documents.load_source(attachment.get_uid())[2].generation
    assert b.documents.claim(attachment.get_uid(), generation)
    assert b.documents.publish_extraction(
        attachment.get_uid(), generation, extraction_cache_token=cache.token, **cache.get_or_convert(converted)
    )
    cache.retain()
    return generation


def test_embedding_identity_requires_exact_committed_extraction(lifecycle, monkeypatch):
    b = lifecycle
    upload(b)
    attachment = source(b)
    uid = attachment.get_uid()
    generation = b.documents.load_source(uid)[2].generation
    with extraction(attachment) as cache:
        cache.get_or_convert(converted)
        with pytest.raises(PublishedExtractionUnavailable, match="explicitly reprocess"):
            published_extraction_identity("project_attachment", uid, generation)
        publish(b, attachment, cache)
        identity = cache.key
    # Runtime changes after publication must never rewrite the original identity.
    monkeypatch.setattr(
        "langboard_shared.tasks.docling.DocumentExtractionCache.metadata.version", lambda *_: "future-runtime"
    )
    assert published_extraction_identity("project_attachment", uid, generation) == identity
    with pytest.raises(PublishedExtractionUnavailable):
        published_extraction_identity("wiki_attachment", uid, generation)
    with pytest.raises(PublishedExtractionUnavailable):
        published_extraction_identity("project_attachment", uid, uuid4().hex)
    newer = b.documents.request(b.actor, b.source_board, attachment, force=True)["generation"]
    with pytest.raises(PublishedExtractionUnavailable):
        published_extraction_identity("project_attachment", uid, newer)
    assert published_extraction_identity("project_attachment", uid, generation) == identity


def test_shared_card_project_ownership_and_final_selected_deletion(lifecycle):
    b = lifecycle
    upload(b)
    attachment = source(b)
    with extraction(attachment) as first:
        publish(b, attachment, first)
    with DbSession.atomic() as db:
        card_attachment = CardAttachment(
            user_id=b.actor.id, card_id=b.source.id, filename="card.pdf", content_sha256=attachment.content_sha256,
            file=FileModel(storage_type="test", storage_name="test", original_filename="card.pdf",
                           filename="card.pdf", path="/fixture"),
        )
        db.insert(card_attachment)
    card = b.domain.docling_metadata
    assert card.queue_document(CardMetadata, b.source, card_attachment.get_uid(), "card.pdf")
    generation = card.get_document_by_attachment_uid(CardMetadata, b.source, card_attachment.get_uid())["generation"]
    with extraction(attachment) as second:
        result = second.get_or_convert(lambda: pytest.fail("Shared result must avoid a second conversion"))
        assert card.mark_document_indexed(
            CardMetadata, b.source, card_attachment.get_uid(), "pdf", content_hash=attachment.content_sha256,
            content={"markdown": result["markdown"], "docling_document": result["document_json"]},
            generation=generation, extraction_cache_token=second.token,
        )
        second.retain()
    assert len(rows(Cache)) == 1 and len(rows(Reference)) == 2
    assert all(ref.expires_at is None for ref in rows(Reference))
    assert "extraction_cache_token" not in str(card.load_documents(CardMetadata, b.source))
    card.delete_document_by_attachment_uid(CardMetadata, b.source, card_attachment.get_uid())
    assert len(rows(Cache)) == 1 and len(rows(Reference)) == 1
    b.documents.record_scope_embedding_cleanup(b.source_board)
    assert rows(Cache) == [] and rows(Reference) == []


def test_publication_rollback_stale_generation_and_requeue_keep_previous_reference(lifecycle):
    b = lifecycle
    upload(b)
    attachment = source(b)
    with extraction(attachment) as cache:
        generation = b.documents.load_source(attachment.get_uid())[2].generation
        assert b.documents.claim(attachment.get_uid(), generation)
        result = cache.get_or_convert(converted)
        with pytest.raises(RuntimeError, match="rollback"):
            with DbSession.atomic():
                assert b.documents.publish_extraction(
                    attachment.get_uid(), generation, extraction_cache_token=cache.token, **result
                )
                raise RuntimeError("rollback")
        assert rows(Reference)[0].expires_at is not None
        assert b.documents.load_source(attachment.get_uid())[2].markdown == ""
        assert not b.documents.publish_extraction(
            attachment.get_uid(), generation, extraction_cache_token=uuid4().hex, **result
        )
        assert b.documents.publish_extraction(
            attachment.get_uid(), generation, extraction_cache_token=cache.token, **result
        )
        cache.retain()
    previous = rows(Reference)[0].use_token
    newer = b.documents.request(b.actor, b.source_board, attachment, force=True)["generation"]
    assert newer != generation and rows(Reference)[0].use_token == previous
    with extraction(attachment) as next_cache:
        result = next_cache.get_or_convert(converted)
        assert not b.documents.publish_extraction(
            attachment.get_uid(), generation, extraction_cache_token=next_cache.token, **result
        )
        assert rows(Reference)[0].use_token == previous
        assert b.documents.claim(attachment.get_uid(), newer)
        assert b.documents.publish_extraction(
            attachment.get_uid(), newer, extraction_cache_token=next_cache.token, **result
        )
        next_cache.retain()
    assert len(rows(Reference)) == 1 and rows(Reference)[0].generation == newer
    b.documents.record_scope_embedding_cleanup(b.source_board)
    assert not rows(Cache)


def test_revoked_authority_cannot_activate_or_publish(lifecycle):
    b = lifecycle
    upload(b)
    attachment = source(b)
    generation = b.documents.load_source(attachment.get_uid())[2].generation
    assert b.documents.claim(attachment.get_uid(), generation)
    with extraction(attachment) as cache:
        result = cache.get_or_convert(converted)
        with DbSession.atomic() as db:
            db.exec(delete(ProjectAssignedUser).where(ProjectAssignedUser.user_id == b.actor.id))
        assert not b.documents.publish_extraction(
            attachment.get_uid(), generation, extraction_cache_token=cache.token, **result
        )
        assert rows(Reference)[0].expires_at is not None
    assert not rows(Cache) and not rows(Reference)


def test_native_invalidation_rollback_and_pending_cleanup(lifecycle):
    b = lifecycle
    upload(b)
    attachment = source(b)
    with extraction(attachment) as cache:
        publish(b, attachment, cache)
    with pytest.raises(RuntimeError, match="rollback"):
        with DbSession.atomic() as db:
            b.documents.invalidate(db, attachment)
            assert not rows(Reference)
            raise RuntimeError("rollback")
    assert len(rows(Reference)) == 1 and b.documents.load_source(attachment.get_uid())
    with DbSession.atomic() as db:
        b.documents.invalidate(db, attachment)
    assert not rows(Cache)
    with extraction(attachment) as cache:
        cache.get_or_convert(converted)
        cache.retain()  # Simulate process loss before publication.
    with DbSession.atomic() as db:
        db.exec(update(Reference).values(expires_at=SafeDateTime.now() - timedelta(seconds=1)))
    assert recover_cleanup(limit=1) == 0
    assert not rows(Reference) and not rows(Cache)


def test_card_stale_generation_missing_source_and_scope_cleanup(lifecycle):
    b = lifecycle
    upload(b)
    attachment = source(b)
    with DbSession.atomic() as db:
        selected = CardAttachment(
            user_id=b.actor.id, card_id=b.source.id, filename="card.pdf",
            file=FileModel(storage_type="test", storage_name="test", original_filename="card.pdf",
                           filename="card.pdf", path="/fixture"),
        )
        db.insert(selected)
    card = b.domain.docling_metadata
    assert card.queue_document(CardMetadata, b.source, selected.get_uid(), "card.pdf")
    generation = card.get_document_by_attachment_uid(CardMetadata, b.source, selected.get_uid())["generation"]
    with extraction(attachment) as cache:
        result = cache.get_or_convert(converted)
        args = (CardMetadata, b.source, selected.get_uid(), "pdf")
        assert not card.mark_document_indexed(
            *args, content={"markdown": "stale"}, generation=uuid4().hex, extraction_cache_token=cache.token
        )
        assert not card.mark_document_indexed(
            *args, content={"markdown": "unfenced"}, extraction_cache_token=cache.token
        )
        with DbSession.atomic() as db:
            db.exec(update(CardAttachment).where(CardAttachment.id == selected.id).values(deleted_at=SafeDateTime.now()))
        assert not card.mark_document_indexed(
            *args, content={"markdown": "deleted"}, generation=generation, extraction_cache_token=cache.token
        )
        assert rows(Reference)[0].expires_at is not None
        assert not rows(CardDocumentArtifact)
        with DbSession.atomic() as db:
            db.exec(update(CardAttachment).where(CardAttachment.id == selected.id).values(deleted_at=None))
        assert card.mark_document_indexed(
            *args, content={"markdown": result["markdown"]}, generation=generation, extraction_cache_token=cache.token
        )
        cache.retain()
    # Legacy metadata updates preserve the active extraction reference.
    assert card.mark_document_indexed(*args, content={"markdown": result["markdown"]}, generation=generation)
    assert len(rows(Reference)) == 1
    # Card moves fence generations but preserve conversion ownership.
    identity = published_extraction_identity("card_attachment", selected.get_uid(), generation)
    card.invalidate_card_location(b.source_board, b.source)
    assert len(rows(Reference)) == 1
    relocated = card.get_document_by_attachment_uid(CardMetadata, b.source, selected.get_uid())["generation"]
    assert relocated != generation
    assert published_extraction_identity("card_attachment", selected.get_uid(), relocated) == identity
    with pytest.raises(PublishedExtractionUnavailable):
        published_extraction_identity("card_attachment", selected.get_uid(), generation)
    card.record_scope_embedding_cleanup(b.source_board, b.source)
    assert not rows(Reference) and not rows(Cache)


def test_concurrent_source_deletion_and_publication_leave_no_private_orphan(lifecycle):
    from concurrent.futures import ThreadPoolExecutor
    from langboard_shared.domain.models import ProjectAttachment

    b = lifecycle
    upload(b)
    attachment = source(b)
    generation = b.documents.load_source(attachment.get_uid())[2].generation
    assert b.documents.claim(attachment.get_uid(), generation)
    with extraction(attachment) as cache:
        result = cache.get_or_convert(converted)

        def publication():
            return b.documents.publish_extraction(
                attachment.get_uid(), generation, extraction_cache_token=cache.token, **result
            )

        def removal():
            with DbSession.atomic() as db:
                # Native SQLite source mutation reserves the writer before reading.
                db.exec(
                    update(ProjectAttachment)
                    .where(ProjectAttachment.id == attachment.id)
                    .values(deleted_at=SafeDateTime.now())
                )
                b.documents.invalidate(db, attachment)

        with ThreadPoolExecutor(max_workers=2) as pool:
            published = pool.submit(publication)
            deleted = pool.submit(removal)
            published.result()
            deleted.result()
    assert not rows(Reference) and not rows(Cache)
    assert b.documents.load_source(attachment.get_uid(), generation) is None


def test_explicit_duplicate_add_reuses_extraction_and_deletes_independently(lifecycle):
    from unittest.mock import Mock
    from langboard_shared.domain.models import ProjectAttachment

    b = lifecycle
    upload(b)
    first = source(b)
    converter = Mock(side_effect=converted)
    with extraction(first) as cache:
        generation = b.documents.load_source(first.get_uid())[2].generation
        assert b.documents.claim(first.get_uid(), generation)
        assert b.documents.publish_extraction(
            first.get_uid(), generation, extraction_cache_token=cache.token, **cache.get_or_convert(converter)
        )
        cache.retain()
    proposal = upload(b)
    result = upload(
        b,
        duplicate_action="add",
        confirmation_token=proposal["confirmation_token"],
        request_uid=proposal["request_uid"],
    )
    with DbSession.use(readonly=False) as db:
        second = db.exec(SqlBuilder.select.table(ProjectAttachment).where(ProjectAttachment.id != first.id)).first()
    assert second.get_uid() == result["uid"] and second.file != first.file
    with extraction(second) as cache:
        generation = b.documents.load_source(second.get_uid())[2].generation
        assert b.documents.claim(second.get_uid(), generation)
        assert b.documents.publish_extraction(
            second.get_uid(), generation, extraction_cache_token=cache.token, **cache.get_or_convert(converter)
        )
        cache.retain()
    assert converter.call_count == 1 and len(rows(Reference)) == 2
    with DbSession.atomic() as db:
        b.documents.invalidate(db, first)
    assert len(rows(Reference)) == 1 and len(rows(Cache)) == 1
    assert b.documents.get_readable(b.actor, b.source_board, second)[2].markdown == "Shared private extraction"
    with DbSession.atomic() as db:
        b.documents.invalidate(db, second)
    assert not rows(Reference) and not rows(Cache)
