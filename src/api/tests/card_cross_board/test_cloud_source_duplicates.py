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
