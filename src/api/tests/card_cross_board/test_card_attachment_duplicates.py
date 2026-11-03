# ruff: noqa: F811 - imported native fixtures are resolved by pytest.
"""Changed-byte replacement must retain independently published card outputs."""

from copy import deepcopy
from json import dumps
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from langboard.mcp_tools import CardMcp
from langboard_shared.core.broker import Broker
from langboard_shared.domain.models import (
    CardAttachment,
    CardDocumentArtifact,
    CardMetadata,
    DocumentExtractionCacheReference,
    DocumentVectorCleanup,
    Project,
)
from langboard_shared.domain.services import KeywordSearchSettings
from langboard_shared.domain.services.factory.CardAttachmentService import (
    DOCLING_INDEX_CARD_ATTACHMENT_TASK,
    CardAttachmentService,
)
from langboard_shared.domain.services.factory.InternalBotService import InternalBotService
from langboard_shared.helpers import InfraHelper
from langboard_shared.publishers import CardAttachmentPublisher
from langboard_shared.tasks.docling import DoclingMetadataTask
from langboard_shared.tasks.docling.DocumentExtractionCache import cached_document_extraction
from test_attachment_duplicate_upload import choose, rows, upload
from test_attachment_duplicate_upload import uploads as uploads
from test_card_mirrors import mirror_board as mirror_board


pytestmark = pytest.mark.parametrize("mirror_board", ["sqlite", "postgresql-test"], indirect=True)


@pytest.fixture
def published_card(uploads, monkeypatch):
    """Use native storage, source authority, extraction publication, and metadata."""
    b = uploads
    monkeypatch.setattr(KeywordSearchSettings, "enabled", lambda _: False)
    monkeypatch.setattr(Broker.celery, "send_task", Mock())
    monkeypatch.setattr(CardAttachmentPublisher, "uploaded", Mock())
    monkeypatch.setattr(b.metadata, "publish_update", Mock())
    upload(b)
    b.attachment = rows(CardAttachment)[0]
    uid = b.attachment.get_uid()
    vision = {"binding_uid": "fixture-vision", "model_name": "fixture"}
    assert b.metadata.queue_document(
        CardMetadata, b.source, uid, b.attachment.filename, vision_config=vision,
    )
    queued = b.metadata.get_document_by_attachment_uid(CardMetadata, b.source, uid)["generation"]
    assert b.metadata.claim_document(CardMetadata, b.source, uid, b.attachment.filename, generation=queued)
    b.generation = b.metadata.get_document_by_attachment_uid(CardMetadata, b.source, uid)["generation"]
    with cached_document_extraction(
        binding_uid=vision["binding_uid"], content_hash=b.attachment.content_sha256,
        vision_config=vision, filename=b.attachment.filename, timeout_seconds=1,
    ) as extraction:
        converted = extraction.get_or_convert(lambda: {
            "markdown": "Last successful transcription", "keywords": {},
            "document_json": {"schema_name": "DoclingDocument", "pages": {"1": {}}},
        })
        assert b.metadata.mark_document_indexed(
            CardMetadata, b.source, uid, "pdf", b.attachment.content_sha256,
            {"markdown": converted["markdown"], "docling_document": converted["document_json"]},
            generation=b.generation, extraction_cache_token=extraction.token,
        )
        extraction.retain()
    # A committed server-owned pointer is enough to observe premature retirement;
    # external vector inference and transport are outside this regression.
    b.embedding = {
        "status": "indexed", "source_generation": b.generation,
        "pointer": {
            "namespace": "published-card", "chunk_ids": ["published-chunk"],
            "generation": "published-vector", "board_uid": b.source_board.get_uid(),
            "card_uid": b.source.get_uid(), "attachment_uid": uid,
            "content_hash": b.attachment.content_sha256, "storage": {"type": "sqlite"},
        },
    }
    assert b.metadata.publish_document_embedding(
        b.source, uid, b.generation, b.attachment.content_sha256, b.embedding, expected_embedding={},
    )
    b.reader = SimpleNamespace(
        card=b.service, card_attachment=b.uploads, docling_metadata=b.metadata,
        project=b.http_service.project,
    )
    assert CardMcp.read_card_document(
        b.source_board.get_uid(), b.source.get_uid(), uid, b.actor, b.reader,
    )["content"] == "Last successful transcription"
    b.before = output_state(b)
    assert b.before["text"] == "Last successful transcription"
    assert b.before["artifact"] and len(b.before["extraction_references"]) == 1
    assert b.before["cleanup"] == []
    b.vision = vision
    return b


def output_state(b):
    document = b.metadata.get_document_by_attachment_uid(CardMetadata, b.source, b.attachment.get_uid()) or {}
    attachment = next(row for row in rows(CardAttachment) if row.id == b.attachment.id)
    return {
        "text": attachment.document_text,
        "artifact": [row.document_json for row in rows(CardDocumentArtifact)],
        "content": deepcopy(document.get("content")),
        "content_hash": document.get("content_hash"),
        "embedding": deepcopy(document.get("embedding")),
        "extraction_references": [
            {"source_uid": row.source_uid, "generation": row.generation}
            for row in rows(DocumentExtractionCacheReference) if row.expires_at is None
        ],
        "cleanup": [row.pointer for row in rows(DocumentVectorCleanup)],
    }


@pytest.mark.parametrize("processing", ["disabled", "pending", "queue_failure", "conversion_failure"])
def test_changed_bytes_retain_published_outputs_until_success(published_card, monkeypatch, tmp_path, processing):
    b = published_card
    provider = SimpleNamespace(
        get_document_vision_binding=lambda: SimpleNamespace(
            value=dumps({"model_name": "fixture"}), get_uid=lambda: "fixture-vision",
        ),
        is_document_processing_enabled=lambda: processing != "disabled",
        get_document_embedding_binding=lambda: None,
    )
    prior_services = b.uploads._get_service
    monkeypatch.setattr(
        b.uploads, "_get_service", lambda cls: provider if cls is InternalBotService else prior_services(cls),
    )
    # Restore the real upload dispatch lifecycle; broker delivery stays private.
    monkeypatch.setattr(
        b.uploads, "dispatch_created",
        lambda *args, **kwargs: CardAttachmentService.dispatch_created(
            b.uploads, *args, include_bot=False, **kwargs,
        ),
    )
    calls = []

    def send(name, *args, **kwargs):
        calls.append(name)
        if processing == "queue_failure" and name == DOCLING_INDEX_CARD_ATTACHMENT_TASK:
            raise RuntimeError("Deterministic queue failure")

    monkeypatch.setattr(Broker.celery, "send_task", send)
    result = choose(b, upload(b, content=b"second"))
    assert result["uid"] == b.attachment.get_uid()
    current = next(row for row in rows(CardAttachment) if row.id == b.attachment.id)
    assert current.user_id == b.attachment.user_id and current.card_id == b.attachment.card_id
    assert current.file != b.attachment.file
    assert b.uploads.resolve_file_owner(
        b.attachment.file.storage_type, b.attachment.file.storage_name, b.attachment.file.filename,
    ).id == current.id
    document = b.metadata.get_document_by_attachment_uid(CardMetadata, b.source, current.get_uid())
    if processing == "disabled":
        assert DOCLING_INDEX_CARD_ATTACHMENT_TASK not in calls
    else:
        assert DOCLING_INDEX_CARD_ATTACHMENT_TASK in calls and document["status"] == "pending"
    if processing == "conversion_failure":
        monkeypatch.setattr(DoclingMetadataTask, "find_spec", lambda _: object())
        monkeypatch.setattr(DoclingMetadataTask, "_current_vision_config", lambda *_: b.vision)
        converter = Mock(side_effect=RuntimeError("Deterministic conversion failure"))
        monkeypatch.setattr(DoclingMetadataTask, "_convert_to_markdown", converter)
        worker = SimpleNamespace(
            card_attachment=b.uploads, card=b.service, docling_metadata=b.metadata,
            project=SimpleNamespace(get_by_id_like=lambda value: InfraHelper.get_by_id_like(Project, value)),
        )
        DoclingMetadataTask._index_card_attachment(worker, current, generation=document["generation"])
        converter.assert_called_once()
        document = b.metadata.get_document_by_attachment_uid(CardMetadata, b.source, current.get_uid())
        assert document["status"] == "failed"
    after = output_state(b)
    try:
        read = CardMcp.read_card_document(
            b.source_board.get_uid(), b.source.get_uid(), current.get_uid(), b.actor, b.reader,
        )
    except ValueError as error:
        read = {"error": str(error)}
    (tmp_path / "replacement-state.json").write_text(dumps({
        "processing": processing, "before": b.before, "after": after,
        "requested": document, "read": read, "broker_calls": calls,
    }, indent=2, default=str))
    assert after == b.before, "Replacement retired published outputs before successful publication"
    assert read["content"] == "Last successful transcription"
    # The completed generation cannot mutate the newly replaced source.
    assert not b.metadata.mark_document_indexed(
        CardMetadata, b.source, current.get_uid(), "pdf", b.attachment.content_sha256,
        {"markdown": "Late old worker"}, generation=b.generation,
    )


@pytest.mark.parametrize("reader", ["transcription", "vectors"])
def test_pending_card_generation_retains_readable_published_source(published_card, reader):
    """Retaining stored outputs alone is insufficient under the current readers."""
    b = published_card
    uid = b.attachment.get_uid()
    _, _, context = b.service.resolve_readable_card(b.source_board, b.source, b.actor)
    assert b.service.repo.card.vector_document_sources(b.source_board, [uid], context=context)
    assert b.metadata.queue_document(
        CardMetadata, b.source, uid, b.attachment.filename, vision_config=b.vision, force=True,
    )
    # This case does not replace bytes or delete anything. The stored successful
    # output survives, while a new native request generation becomes pending.
    assert output_state(b) == b.before
    if reader == "transcription":
        result = CardMcp.read_card_document(
            b.source_board.get_uid(), b.source.get_uid(), uid, b.actor, b.reader,
        )
        assert result["content"] == "Last successful transcription"
        assert result["generation"] == b.generation
    else:
        assert b.service.repo.card.vector_document_sources(b.source_board, [uid], context=context), (
            "Pending requested generation hid the retained published vector source"
        )
