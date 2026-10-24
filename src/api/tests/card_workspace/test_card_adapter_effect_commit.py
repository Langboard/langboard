"""Image and upload adapters prepare native durable effects in their write unit."""

from hashlib import sha256
from importlib import import_module
from io import BytesIO
from json import dumps
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4
import pytest
from fastapi import UploadFile
from langboard_shared.core.db import DbSession, EditorContentModel
from langboard_shared.core.storage import FileModel, Storage
from langboard_shared.core.storage.LocalStorage import LocalStorage
from langboard_shared.domain.models import (
    AttachmentStorageObject,
    AttachmentStorageReference,
    Card,
    CardAttachment,
    CardChangeReceipt,
    CardComment,
    CardDocumentArtifact,
    CardMetadata,
    DocumentEmbeddingCache,
    DocumentEmbeddingCacheReference,
    DocumentExtractionCache,
    DocumentExtractionCacheReference,
    DocumentKeywordIndex,
    DocumentVectorCleanup,
    EmployeeMembershipPolicy,
    NotificationEmailDelivery,
    ProjectActivity,
    ProjectRole,
    UserActivity,
    UserNotification,
)
from langboard_shared.domain.services.factory.CardAttachmentService import CardAttachmentService
from langboard_shared.domain.services.factory.CardCommentService import CardCommentService
from langboard_shared.domain.services.factory.DoclingMetadataService import DoclingMetadataService
from langboard_shared.domain.services.factory.InternalBotService import InternalBotService
from langboard_shared.Env import Env
from PIL import Image
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session
from test_card_change_counts import counts as _counts
from test_card_change_counts import native_delivery as _native_delivery
from test_card_change_counts import notification_recipient as _notification_recipient


counts, native_delivery, notification_recipient = _counts, _native_delivery, _notification_recipient


def row_count(model):
    with DbSession.atomic() as db:
        return db.exec(select(func.count()).select_from(model)).first()[0]


@pytest.fixture
def adapters(counts, monkeypatch, tmp_path):
    f = counts
    tables = set()

    def include(table):
        if table in tables:
            return
        tables.add(table)
        for fk in table.foreign_keys:
            include(fk.column.table)

    for model in (
        AttachmentStorageObject,
        AttachmentStorageReference,
        ProjectActivity,
        UserActivity,
        CardDocumentArtifact,
        DocumentExtractionCache,
        DocumentExtractionCacheReference,
        DocumentEmbeddingCache,
        DocumentEmbeddingCacheReference,
        DocumentKeywordIndex,
        DocumentVectorCleanup,
        EmployeeMembershipPolicy,
    ):
        include(model.__table__)
    Card.metadata.create_all(f.engine, tables=list(tables))
    with DbSession.atomic() as db:
        db.insert(ProjectRole(project_id=f.project.id, user_id=f.user.id, actions=["read"]))
    monkeypatch.setattr(type(Env), "LOCAL_STORAGE_DIR", property(lambda _: tmp_path / "objects"))
    monkeypatch.setattr(Storage, "_storages", {"local": LocalStorage()})
    f.attachments = f.get(CardAttachmentService)
    f.comments = f.get(CardCommentService)
    f.metadata = f.get(DoclingMetadataService)
    f.provider = f.get(InternalBotService)
    monkeypatch.setattr(
        f.provider,
        "get_document_vision_binding",
        lambda: SimpleNamespace(value='{"model_name":"test"}', get_uid=lambda: "fixture-binding"),
    )
    monkeypatch.setattr(f.provider, "is_document_processing_enabled", lambda: True)
    monkeypatch.setattr(f.provider, "get_document_embedding_binding", lambda: None)
    monkeypatch.setattr(
        import_module("langboard_shared.domain.services.KeywordSearchSettings"), "enabled", lambda _: False
    )
    return f


@pytest.mark.parametrize("outcome", ["commit", "rollback", "preparation_failure", "commit_failure"])
def test_image_comment_native_outbox_is_atomic_and_replay_is_silent(
    adapters, native_delivery, notification_recipient, monkeypatch, outcome
):
    f, observed = adapters, native_delivery
    source = Env.LOCAL_STORAGE_DIR / "card_attachment" / "immutable-image"
    source.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (200, 100)).save(source, format="PNG")
    with DbSession.atomic() as db:
        image = CardAttachment(
            card_id=f.card.id,
            user_id=f.user.id,
            filename="image.png",
            file=FileModel(
                filename="immutable-image",
                original_filename="image.png",
                storage_name="card_attachment",
                storage_type="local",
                path="/image.png",
            ),
        )
        db.insert(image)
    anchor = dict(
        type="ImageRegionSelector",
        version=1,
        shape="point",
        image_width=200,
        image_height=100,
        x=10,
        y=20,
        width=0,
        height=0,
        source=dict(type="card_attachment", attachment_uid=image.get_uid(), revision=image.image_revision),
    )
    request_uid = str(uuid4())
    mention = EditorContentModel(content=f"[**@Owner**]({f.other.get_uid()})")
    native_accept = f.repo.notification_email_delivery.accept
    prepared = []

    def accept(*args):
        native_accept(*args)
        prepared.append(DbSession.has_active_transaction())
        if outcome == "preparation_failure":
            raise RuntimeError("native outbox preparation failure")

    monkeypatch.setattr(f.repo.notification_email_delivery, "accept", accept)

    def create():
        return f.comments.create(f.user, f.project, f.card, mention, anchor, request_uid=request_uid)

    def fail_commit(session):
        raise RuntimeError("commit failure")

    if outcome == "commit_failure":
        event.listen(Session, "before_commit", fail_commit)
    try:

        def run():
            with DbSession.atomic():
                first = create()
                if outcome == "preparation_failure":
                    return
                assert first["replayed"] is False
                assert [
                    row_count(model)
                    for model in (CardComment, CardChangeReceipt, UserNotification, NotificationEmailDelivery)
                ] == [1] * 4
                assert create() == {**first, "replayed": True}
                assert observed == [], "Image comment transport escaped the open transaction"
                if outcome == "rollback":
                    raise RuntimeError("outer rollback")

        if outcome == "commit":
            run()
        else:
            with pytest.raises(RuntimeError, match="rollback|failure"):
                run()
    finally:
        if outcome == "commit_failure":
            event.remove(Session, "before_commit", fail_commit)
    expected = int(outcome == "commit")
    assert prepared == [True]
    assert [
        row_count(model) for model in (CardComment, CardChangeReceipt, UserNotification, NotificationEmailDelivery)
    ] == [expected] * 4
    if not expected:
        assert observed == []
        monkeypatch.setattr(f.repo.notification_email_delivery, "accept", native_accept)
        assert create()["replayed"] is False
    assert [
        row_count(model) for model in (CardComment, CardChangeReceipt, UserNotification, NotificationEmailDelivery)
    ] == [1] * 4
    emitted = list(observed)
    assert create()["replayed"] is True
    assert observed == emitted
    assert all(not active for _, active, _ in observed)
    assert sum("publish_pending_work_events" in name for name, _, _ in observed) == 1
    assert sum("card_comment_added" in name for name, _, _ in observed) == 2


@pytest.mark.parametrize("outcome", ["commit", "preparation_failure", "commit_failure"])
def test_duplicate_upload_native_metadata_is_atomic_and_replay_is_silent(
    adapters, native_delivery, monkeypatch, outcome
):
    f, observed = adapters, native_delivery
    request_uid = str(uuid4())
    native_queue = f.metadata.queue_document
    prepared = []
    deleted = Mock(wraps=Storage.delete)
    monkeypatch.setattr(Storage, "delete", deleted)

    def queue(*args, **kwargs):
        queued = native_queue(*args, **kwargs)
        prepared.append(DbSession.has_active_transaction())
        if outcome == "preparation_failure":
            raise RuntimeError("native metadata preparation failure")
        assert row_count(CardMetadata) == row_count(CardAttachment) == row_count(CardChangeReceipt) == 1
        assert row_count(ProjectActivity) == 1
        assert observed == [], "Upload transport escaped metadata preparation"
        return queued

    monkeypatch.setattr(f.metadata, "queue_document", queue)

    def upload():
        return f.attachments.upload(
            f.user,
            f.project,
            f.card,
            UploadFile(BytesIO(b"document bytes"), filename="file.pdf"),
            request_uid=request_uid,
        )

    failed_commits = []

    def fail_commit(session):
        if not failed_commits:
            failed_commits.append(True)
            raise RuntimeError("commit failure")

    if outcome == "commit_failure":
        event.listen(Session, "before_commit", fail_commit)
    try:
        if outcome == "commit":
            assert upload()["uid"]
        else:
            with pytest.raises(RuntimeError, match="failure"):
                upload()
    finally:
        if outcome == "commit_failure":
            event.remove(Session, "before_commit", fail_commit)
    expected = int(outcome == "commit")
    assert prepared == [True]
    assert [
        row_count(model)
        for model in (
            CardMetadata,
            CardAttachment,
            CardChangeReceipt,
            ProjectActivity,
            AttachmentStorageObject,
            AttachmentStorageReference,
        )
    ] == [expected] * 6
    if not expected:
        assert observed == []
        deleted.assert_called_once()
        assert Storage.get_file(deleted.call_args.args[0]) is None
        monkeypatch.setattr(f.metadata, "queue_document", native_queue)
        assert upload()["uid"]
    else:
        deleted.assert_not_called()
    emitted = list(observed)
    assert upload()["replayed"] is True
    assert observed == emitted
    assert all(not active for _, active, _ in observed)
    assert sum("DoclingMetadataTask.index_card_attachment" in name for name, _, _ in observed) == 1
    assert sum("CardAttachmentActivityTask.card_attachment_uploaded" in name for name, _, _ in observed) == 0
    assert row_count(ProjectActivity) == row_count(UserActivity) == 1


@pytest.mark.asyncio
async def test_identical_native_uploads_share_one_extraction_and_embedding(
    adapters, native_delivery, monkeypatch, tmp_path
):
    from langboard_shared.domain.models.InternalBot import InternalBotType
    from langboard_shared.domain.services.factory.ProjectService import ProjectService
    from langboard_shared.tasks.docling import DoclingMetadataTask, DocumentEmbeddingTask
    from langchain_core.embeddings import Embeddings

    f, observed = adapters, native_delivery
    monkeypatch.setattr(type(Env), "DATA_DIR", property(lambda _: tmp_path / "data"))
    vision = f.provider.get_document_vision_binding()
    vision.bot_type = InternalBotType.DocumentVision
    embedding = SimpleNamespace(
        bot_type=InternalBotType.DocumentEmbedding,
        get_uid=lambda: "embedding-fixture",
        value=dumps(
            dict(
                agent_llm="OpenAI Compatible",
                model_name="embedding-test",
                base_url="https://fixture.invalid",
                retrieval=dict(enabled=True, store="sqlite", dimensions=3, timeout_seconds=1),
            )
        ),
    )
    monkeypatch.setattr(f.provider, "get_document_embedding_binding", lambda: embedding)
    monkeypatch.setattr(
        f.provider,
        "get_current_by_id_like",
        lambda uid: vision if uid == vision.get_uid() else embedding if uid == embedding.get_uid() else None,
    )
    monkeypatch.setattr(DoclingMetadataTask, "find_spec", lambda _: object())
    converter = Mock(return_value="Same native transcription")
    monkeypatch.setattr(DoclingMetadataTask, "_convert_to_markdown", converter)

    class Provider(Embeddings):
        calls = 0

        def embed_documents(self, texts):
            assert not DbSession.has_active_transaction()
            self.calls += 1
            return [[1.0, 2.0, 3.0] for _ in texts]

        async def aembed_documents(self, texts):
            return self.embed_documents(texts)

        def embed_query(self, text):
            raise AssertionError("No query inference during upload")

    provider = Provider()
    monkeypatch.setattr(DocumentEmbeddingTask, "create_document_embeddings", lambda *_: provider)
    service = SimpleNamespace(
        card_attachment=f.attachments,
        card=f.cards,
        project=f.get(ProjectService),
        docling_metadata=f.metadata,
        internal_bot=f.provider,
    )

    def upload(name, **decision):
        return f.attachments.upload(
            f.user, f.project, f.card, UploadFile(BytesIO(b"same bytes"), filename=name), **decision
        )

    def document(uid):
        return f.metadata.get_document_by_attachment_uid(CardMetadata, f.card, uid)

    async def process(uid):
        current = document(uid)
        attachment = f.attachments.get_by_id_like(uid)
        DoclingMetadataTask._index_card_attachment(service, attachment, generation=current["generation"])
        current = document(uid)
        assert current["status"] == "indexed"
        await DocumentEmbeddingTask.embed_transcription(service, uid, current["generation"])
        assert document(uid)["embedding"]["status"] == "indexed"

    first = upload("first.pdf")
    await process(first["uid"])
    before = document(first["uid"])
    assert converter.call_count == provider.calls == 1
    observed.clear()
    preview = upload("renamed.pdf")
    decision = dict(
        request_uid=preview["request_uid"],
        confirmation_token=preview["confirmation_token"],
        duplicate_action="replace",
        replacement_uid=first["uid"],
    )
    replaced = upload("renamed.pdf", **decision)
    assert replaced["uid"] == first["uid"]
    assert document(first["uid"]) == before
    assert upload("renamed.pdf", **decision)["replayed"] is True
    assert not any("docling" in name.lower() for name, _, _ in observed)
    assert converter.call_count == provider.calls == 1

    preview = upload("additional.pdf")
    decision = dict(
        request_uid=preview["request_uid"], confirmation_token=preview["confirmation_token"], duplicate_action="add"
    )
    additional = upload("additional.pdf", **decision)
    emitted = list(observed)
    assert upload("additional.pdf", **decision)["replayed"] is True
    assert observed == emitted
    await process(additional["uid"])
    assert converter.call_count == provider.calls == 1
    assert (
        row_count(AttachmentStorageObject)
        == row_count(DocumentExtractionCache)
        == row_count(DocumentEmbeddingCache)
        == 1
    )
    assert row_count(AttachmentStorageReference) == row_count(DocumentExtractionCacheReference) == 2
    assert row_count(DocumentEmbeddingCacheReference) == 2
    assert row_count(ProjectActivity) == 3
    assert document(first["uid"]) == before
    for uid in (first["uid"], additional["uid"]):
        assert document(uid)["content_hash"] == sha256(b"same bytes").hexdigest()
