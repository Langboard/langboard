"""Native card/project publication shares extraction; only external conversion is a fixture."""

from json import dumps
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from sqlalchemy import delete, update
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.domain.models import (
    Card,
    CardAttachment,
    CardDocumentArtifact,
    CardMetadata,
    DocumentExtractionCache,
    DocumentExtractionCacheReference,
    ProjectAssignedUser,
    ProjectAttachment,
    ProjectAttachmentDocument,
    ProjectColumn,
)
from langboard_shared.domain.services import DomainService
from langboard_shared.tasks.docling import DoclingMetadataTask as card_task
from langboard_shared.tasks.docling import ProjectDocumentTask as project_task
from langboard_shared.tasks.docling.ProjectDocumentTask_test import native_worker as _native_worker
from langboard_shared.tasks.docling.ProjectDocumentTask_test import worker as worker


native_worker = _native_worker


@pytest.fixture
def pipeline(native_worker, monkeypatch):
    w = native_worker
    engine = DbEngine.get_main_engine()
    for model in (ProjectColumn, Card, CardAttachment, CardMetadata, CardDocumentArtifact):
        model.__table__.create(engine)
    with DbSession.atomic() as db:
        column = ProjectColumn(project_id=w.project.id, name="Native")
        db.insert(column)
        card = Card(project_id=w.project.id, project_column_id=column.id, title="Private card")
        db.insert(card)
        attachment = CardAttachment(
            card_id=card.id, user_id=w.actor.id, filename="card-original.pdf", file=w.attachment.file,
            content_sha256=w.attachment.content_sha256,
        )
        db.insert(attachment)
    native = DomainService()
    metadata = native.docling_metadata
    metadata.upsert_document(CardMetadata, card, {
        "attachment_uid": attachment.get_uid(), "generation": "d" * 32, "status": "pending",
        "vision_config": w.document.vision_config,
    })
    monkeypatch.setattr(metadata, "publish_update", Mock())
    monkeypatch.setattr(card_task, "find_spec", lambda _: object())
    monkeypatch.setattr(card_task, "_current_vision_config", Mock(return_value={"keyword_languages": ["en"]}))
    monkeypatch.setattr(card_task, "_convert_to_markdown", project_task._convert_to_markdown)
    w.card, w.card_attachment = card, attachment
    w.card_service = SimpleNamespace(
        card_attachment=native.card_attachment, card=native.card, project=native.project,
        docling_metadata=metadata,
    )
    yield w
    native.close()


def index_card(w):
    card_task._index_card_attachment(w.card_service, w.card_attachment, generation="d" * 32)


def index_project(w):
    project_task._index_project_attachment(w.service, w.attachment.get_uid(), w.document.generation)


def test_native_card_and_project_share_one_extraction_and_keep_source_metadata(pipeline, monkeypatch):
    w = pipeline
    download = Mock(wraps=project_task.Storage.download_file)
    monkeypatch.setattr(project_task.Storage, "download_file", download)
    index_card(w)
    index_project(w)
    project_task._convert_to_markdown.assert_called_once()
    assert download.call_count == 2
    card = w.card_service.docling_metadata.get_document_by_attachment_uid(
        CardMetadata, w.card, w.card_attachment.get_uid()
    )
    project = w.service.project_document.get_readable(w.actor, w.project, w.attachment)[2]
    assert card["status"] == project.status == "indexed"
    assert card["content"]["markdown"] == project.markdown == "Native project text"
    assert card["content"]["filename"] == "card-original.pdf"
    assert card["content"]["search_keywords"] == project.keywords == {"en": ["Native", "Project"]}
    assert "cache_token" not in dumps(card)
    assert "cache_token" not in dumps(project.api_response())
    with DbSession.use(readonly=False) as db:
        entries = db.exec(SqlBuilder.select.table(DocumentExtractionCache)).all()
        refs = db.exec(SqlBuilder.select.table(DocumentExtractionCacheReference)).all()
        assert len(entries) == 1
        assert {ref.source_type for ref in refs} == {"card_attachment", "project_attachment"}
        assert {ref.source_uid for ref in refs} == {w.card_attachment.get_uid(), w.attachment.get_uid()}
        assert all(ref.expires_at is None for ref in refs)


@pytest.mark.parametrize("drift", ["authority", "hash", "generation", "suffix"])
def test_native_cache_hit_requires_current_authorized_project_source(pipeline, monkeypatch, drift):
    w = pipeline
    index_card(w)
    if drift == "hash":
        # Current source is valid, but returned storage bytes fail its authoritative hash.
        monkeypatch.setattr(project_task.Storage, "download_file",
                            lambda _, destination: bool(destination.write(b"corrupted")))
    elif drift == "authority":
        with DbSession.atomic() as db:
            db.exec(delete(ProjectAssignedUser).where(ProjectAssignedUser.user_id == w.actor.id))
    else:
        # A ready lookup still validates the current source before delivery/publication.
        from langboard_shared.tasks.docling import DocumentExtractionCache as extraction
        original = extraction.CachedDocumentExtraction.get_or_convert

        def mutate_then_deliver(cache, convert):
            with DbSession.atomic() as db:
                if drift == "generation":
                    db.exec(update(ProjectAttachmentDocument)
                            .where(ProjectAttachmentDocument.id == w.document.id)
                            .values(generation="e" * 32))
                else:
                    db.exec(update(ProjectAttachment).where(ProjectAttachment.id == w.attachment.id)
                            .values(filename="replacement.exe"))
            return original(cache, convert)

        monkeypatch.setattr(extraction.CachedDocumentExtraction, "get_or_convert", mutate_then_deliver)
    index_project(w)
    project_task._convert_to_markdown.assert_called_once()
    with DbSession.use(readonly=False) as db:
        current = db.exec(SqlBuilder.select.table(ProjectAttachmentDocument)).first()
        assert current.markdown == ""
        refs = db.exec(SqlBuilder.select.table(DocumentExtractionCacheReference)).all()
        assert len(refs) == 1 and refs[0].source_type == "card_attachment"
