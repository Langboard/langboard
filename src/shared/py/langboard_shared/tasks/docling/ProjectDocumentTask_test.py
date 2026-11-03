"""Native worker fences; external conversion/inference fixtures, real cache/vector stores."""

import asyncio
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
from json import dumps
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from langchain_core.embeddings import Embeddings
from sqlalchemy import create_engine
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.domain.models import (
    AttachmentStorageObject,
    AttachmentStorageReference,
    DocumentEmbeddingCache,
    DocumentEmbeddingCacheReference,
    DocumentExtractionCache,
    DocumentExtractionCacheReference,
)
from langboard_shared.domain.models.InternalBot import InternalBotType
from langboard_shared.tasks.docling import ProjectDocumentTask as task
from langboard_shared.tasks.docling.DocumentEmbedding import snapshot_embedding_config


class FixtureProvider(Embeddings):
    def __init__(self):
        self.calls = 0
        self.during = lambda: None

    def embed_documents(self, texts):
        from langboard_shared.core.db import DbSession
        assert DbSession._atomic_session.get() is None
        self.calls += 1
        self.during()
        return [[1.0, 2.0, 3.0] for _ in texts]

    async def aembed_documents(self, texts):
        return self.embed_documents(texts)

    def embed_query(self, text):
        return [1.0, 2.0, 3.0]


@pytest.fixture
def worker(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'native-cache.db'}")
    for model in (AttachmentStorageObject, AttachmentStorageReference, DocumentEmbeddingCache, DocumentEmbeddingCacheReference,
                  DocumentExtractionCache, DocumentExtractionCacheReference):
        model.__table__.create(engine)
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    config = dumps(dict(
        agent_llm="OpenAI Compatible", model_name="embedding-fixture", base_url="https://fixture.invalid",
        api_key="secret-fixture", retrieval=dict(enabled=True, store="sqlite", dimensions=3, timeout_seconds=1),
    ))
    snapshot = snapshot_embedding_config(config, "binding")
    document = SimpleNamespace(
        generation="g" * 32, source_revision="r" * 64, status="pending", vision_config={"binding_uid": "vision"},
        embedding_config=snapshot, markdown="Native project text", document_json=None, embedding={},
    )
    attachment = SimpleNamespace(
        filename="report.pdf", file="fixture-source", content_sha256=sha256(b"source").hexdigest(),
    )
    project = SimpleNamespace(get_uid=lambda: "board")
    valid = [True]
    documents = Mock()
    documents.load_source.side_effect = lambda *_: (project, attachment, document) if valid[0] else None

    def claim(*_):
        if document.status != "pending":
            return None
        document.status = "processing"
        return project, attachment, document

    documents.claim.side_effect = claim

    def publish(*_, markdown, keywords, document_json, extraction_cache_token=None):
        if not valid[0]:
            return False
        document.status = "indexed"
        document.markdown, document.document_json = markdown, document_json
        return True

    documents.publish_extraction.side_effect = publish
    published = []

    def publish_embedding(*_, expected_embedding, embedding):
        if not valid[0] or document.embedding != expected_embedding:
            return False
        document.embedding = deepcopy(embedding)
        published.append(document.embedding)
        return True

    documents.publish_embedding.side_effect = publish_embedding
    binding = SimpleNamespace(bot_type=InternalBotType.DocumentEmbedding, value=config)
    service = SimpleNamespace(
        project_document=documents,
        internal_bot=SimpleNamespace(get_current_by_id_like=Mock(return_value=binding)),
    )
    monkeypatch.setattr(task, "Env", SimpleNamespace(
        DATA_DIR=tmp_path, MAX_FILE_SIZE_MB=1, DOCLING_CONVERSION_TIMEOUT_SECONDS=30, get_from_env=lambda *_: "https://fixture.invalid",
    ))
    monkeypatch.setattr(task, "_current_vision_config", Mock(return_value={"keyword_languages": ["en"]}))
    provider = FixtureProvider()
    monkeypatch.setattr(task, "create_document_embeddings", Mock(return_value=provider))

    def download(_, destination):
        destination.write(b"source")
        return True

    monkeypatch.setattr(task.Storage, "download_file", Mock(side_effect=download))

    def convert(path, **kwargs):
        from langboard_shared.core.db import DbSession
        assert not DbSession.has_active_transaction()
        with open(path, "rb") as source:
            assert source.read() == b"source"
        kwargs["on_progress"](0, 1)
        kwargs["on_keywords"]({"en": ["  Native  ", "native", "Project"]})
        return "Native project text"

    monkeypatch.setattr(task, "_convert_to_markdown", Mock(side_effect=convert))
    monkeypatch.setattr(task.Broker.celery, "send_task", Mock())
    yield SimpleNamespace(
        service=service, document=document, attachment=attachment, provider=provider,
        valid=valid, published=published, binding=binding,
    )
    engine.dispose()


def index(w):
    task._index_project_attachment(w.service, "attachment", w.document.generation)


def embed(w):
    asyncio.run(task._embed_project_attachment(w.service, "attachment", w.document.generation))


def test_bounded_download_rejects_before_write():
    destination = BytesIO()
    writer = task._BoundedDownload(destination, 6)
    writer.write(b"source")
    with pytest.raises(ValueError):
        writer.write(b"!")
    assert destination.getvalue() == b"source"
    assert writer.digest.hexdigest() == sha256(b"source").hexdigest()


def test_extract_and_real_shared_cache_vector_publication(worker):
    w = worker
    index(w)
    assert w.document.status == "indexed"
    assert w.service.project_document.publish_extraction.call_args.kwargs["keywords"] == {"en": ["Native", "Project"]}
    task.Broker.celery.send_task.assert_called_once()
    embed(w)
    pointer = w.published[0]["pointer"]
    assert pointer["source_type"] == "project_attachment"
    assert pointer["board_uid"] == "board"
    assert not {"card_uid", "wiki_uid"} & pointer.keys()
    assert w.provider.calls == 1
    first_ids = pointer["chunk_ids"]
    # A second native source uses independent vectors and the same paid cache.
    w.document.embedding = {}
    embed(w)
    assert w.provider.calls == 1
    assert first_ids != w.published[1]["pointer"]["chunk_ids"]


@pytest.mark.parametrize("phase", ["download", "conversion", "hash", "size", "binding"])
def test_invalid_extraction_never_publishes(worker, monkeypatch, phase):
    w = worker
    if phase == "download":
        def download(*_):
            w.valid[0] = False
            return True
        task.Storage.download_file.side_effect = download
    elif phase == "conversion":
        def convert(*_, **__):
            w.valid[0] = False
            return "secret stale document"
        task._convert_to_markdown.side_effect = convert
    elif phase == "hash":
        w.attachment.content_sha256 = "0" * 64
    elif phase == "size":
        task.Storage.download_file.side_effect = lambda _, destination: destination.write(b"x" * (1024 * 1024 + 1))
    else:
        task._current_vision_config.side_effect = ValueError("secret-credential")
    index(w)
    w.service.project_document.publish_extraction.assert_not_called()
    w.service.project_document.mark_failed.assert_called_once_with("attachment", w.document.generation, phase="extraction")
    if phase != "conversion":
        task._convert_to_markdown.assert_not_called()
    task.Broker.celery.send_task.assert_not_called()


@pytest.mark.parametrize("phase", ["source", "binding", "inference", "publication", "delete-fails"])
def test_embedding_drift_and_failed_publication_cleanup(worker, monkeypatch, phase):
    w = worker
    w.document.status = "indexed"
    remove = Mock(wraps=task.delete_vector_generation)
    monkeypatch.setattr(task, "delete_vector_generation", remove)
    if phase == "source":
        w.valid[0] = False
    elif phase == "binding":
        w.binding.value = w.binding.value.replace("fixture.invalid", "changed.invalid")
    elif phase == "inference":
        w.provider.during = lambda: w.valid.__setitem__(0, False)
    else:
        w.service.project_document.publish_embedding.side_effect = None
        w.service.project_document.publish_embedding.return_value = False
        if phase == "delete-fails":
            remove.side_effect = lambda _, pointer: (_ for _ in ()).throw(task.VectorGenerationCleanupError(pointer))
            cleanup = Mock()
            monkeypatch.setattr(task, "_record_cleanup", cleanup)
    embed(w)
    assert not any(item.get("status") == "indexed" for item in w.published)
    if phase in {"source", "binding"}:
        task.create_document_embeddings.assert_not_called()
        assert w.provider.calls == 0
    elif phase in {"publication", "delete-fails"}:
        remove.assert_called_once()
        if phase == "delete-fails":
            cleanup.assert_called_once()


def test_same_generation_replay_has_no_model_work(worker):
    w = worker
    w.document.status = "indexed"
    embed(w)
    embed(w)
    assert w.provider.calls == 1
    assert len(w.published) == 1


def test_duplicate_extraction_claim_never_converts(worker):
    worker.document.status = "processing"
    index(worker)
    task._convert_to_markdown.assert_not_called()



@pytest.fixture
def native_worker(worker, monkeypatch, tmp_path):
    """Real native primary state and publication; only external model output is a fixture."""
    from langboard_shared.core.db import DbSession
    from langboard_shared.core.storage import FileModel, StorageName
    from langboard_shared.core.storage.LocalStorage import LocalStorage
    from langboard_shared.core.types import SafeDateTime
    from langboard_shared.domain.models import (
        Project,
        ProjectAssignedUser,
        ProjectAttachment,
        ProjectAttachmentDocument,
        ProjectRole,
        User,
    )
    from langboard_shared.domain.models.DocumentVectorCleanup import DocumentVectorCleanup
    from langboard_shared.domain.services.factory.ProjectDocumentService import ProjectDocumentService
    from langboard_shared.Env import Env

    engine = DbEngine.get_main_engine()
    for model in (User, Project, ProjectAssignedUser, ProjectRole, ProjectAttachment, ProjectAttachmentDocument,
                  DocumentVectorCleanup):
        model.__table__.create(engine)
    with DbSession.use(readonly=False) as db:
        owner = User(firstname="Owner", lastname="Fixture", email="owner@fixture.invalid", password="fixture",
                     activated_at=SafeDateTime.now())
        actor = User(firstname="Reader", lastname="Fixture", email="reader@fixture.invalid", password="fixture",
                     activated_at=SafeDateTime.now())
        db.insert(owner)
        db.insert(actor)
        project = Project(owner_id=owner.id, title="Native project")
        db.insert(project)
        db.insert(ProjectAssignedUser(project_id=project.id, user_id=actor.id))
        db.insert(ProjectRole(project_id=project.id, user_id=actor.id, actions=["read"]))
        file = FileModel(storage_type="local", storage_name=StorageName.ProjectAttachment.value,
                         original_filename="report.pdf", filename="native-report.pdf", path="/fixture")
        attachment = ProjectAttachment(project_id=project.id, user_id=actor.id, filename="report.pdf", file=file,
                                       content_sha256=sha256(b"source").hexdigest())
        db.insert(attachment)
        document = ProjectAttachmentDocument(
            attachment_id=attachment.id, requested_by_id=actor.id,
            source_revision=ProjectDocumentService.source_revision(attachment), generation="a" * 32,
            vision_config=worker.document.vision_config, embedding_config=worker.document.embedding_config,
        )
        db.insert(document)
    monkeypatch.setattr(type(Env), "LOCAL_STORAGE_DIR", property(lambda _: tmp_path / "objects"))
    destination = Env.LOCAL_STORAGE_DIR / file.storage_name / file.filename
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"source")
    monkeypatch.setattr(task.Storage, "download_file",
                        lambda file, target: LocalStorage().download(file.storage_name, file.filename, target))
    from langboard_shared.domain.services import DomainService
    native_services = DomainService()
    worker.service.project_document = native_services.project_document
    worker.attachment, worker.document = attachment, document
    worker.project, worker.actor = project, actor
    try:
        yield worker
    finally:
        native_services.close()


def native_index(w):
    task._index_project_attachment(w.service, w.attachment.get_uid(), w.document.generation)


def native_embed(w):
    asyncio.run(task._embed_project_attachment(w.service, w.attachment.get_uid(), w.document.generation))


def test_native_primary_extraction_and_actual_vector_cache_publication(native_worker):
    from langboard_shared.core.db import DbSession, SqlBuilder
    w = native_worker
    native_index(w)
    native_embed(w)
    readable = w.service.project_document.get_readable(w.actor, w.project, w.attachment)
    assert readable[2].status == "indexed"
    assert readable[2].markdown == "Native project text"
    pointer = readable[2].embedding["pointer"]
    assert pointer["source_type"] == "project_attachment"
    assert pointer["attachment_uid"] == w.attachment.get_uid()
    with DbSession.use(readonly=False) as db:
        references = db.exec(SqlBuilder.select.table(DocumentEmbeddingCacheReference)).all()
        assert references and all(ref.expires_at is None for ref in references)
    native_embed(w)
    assert w.provider.calls == 1
    # Independent native attachment ownership shares paid inference, never pointers.
    from langboard_shared.core.storage import StorageName
    from langboard_shared.domain.models import ProjectAttachment, ProjectAttachmentDocument
    from langboard_shared.domain.services.factory.ProjectDocumentService import ProjectDocumentService
    from langboard_shared.Env import Env
    first_pointer = pointer
    file = w.attachment.file.model_copy(update={"filename": "second-report.pdf"})
    (Env.LOCAL_STORAGE_DIR / StorageName.ProjectAttachment.value / file.filename).write_bytes(b"source")
    with DbSession.use(readonly=False) as db:
        other = ProjectAttachment(project_id=w.project.id, user_id=w.actor.id, filename="second.pdf", file=file,
                                  content_sha256=w.attachment.content_sha256)
        db.insert(other)
        other_document = ProjectAttachmentDocument(
            attachment_id=other.id, requested_by_id=w.actor.id,
            source_revision=ProjectDocumentService.source_revision(other), generation="c" * 32,
            vision_config=w.document.vision_config, embedding_config=w.document.embedding_config,
        )
        db.insert(other_document)
    w.attachment, w.document = other, other_document
    native_index(w)
    native_embed(w)
    second = w.service.project_document.get_readable(w.actor, w.project, other)[2].embedding["pointer"]
    assert second["attachment_uid"] != first_pointer["attachment_uid"]
    assert set(second["chunk_ids"]).isdisjoint(first_pointer["chunk_ids"])
    assert w.provider.calls == 1


@pytest.mark.parametrize("drift", ["membership", "role", "account", "generation", "bytes", "filename"])
def test_native_conversion_drift_cannot_publish(native_worker, drift):
    from sqlalchemy import delete, update
    from langboard_shared.core.db import DbSession, SqlBuilder
    from langboard_shared.domain.models import (
        ProjectAssignedUser,
        ProjectAttachment,
        ProjectAttachmentDocument,
        ProjectRole,
        User,
    )
    w = native_worker

    def convert(*_, **__):
        with DbSession.atomic() as db:
            if drift == "membership":
                db.exec(delete(ProjectAssignedUser).where(ProjectAssignedUser.user_id == w.actor.id))
            elif drift == "role":
                db.exec(update(ProjectRole).where(ProjectRole.user_id == w.actor.id).values(actions=[]))
            elif drift == "account":
                db.exec(update(User).where(User.id == w.actor.id).values(activated_at=None))
            elif drift == "filename":
                db.exec(update(ProjectAttachment).where(ProjectAttachment.id == w.attachment.id)
                        .values(filename="renamed.exe"))
            elif drift == "generation":
                db.exec(update(ProjectAttachmentDocument).where(ProjectAttachmentDocument.id == w.document.id)
                        .values(generation="b" * 32, status="pending"))
            else:
                db.exec(update(ProjectAttachment).where(ProjectAttachment.id == w.attachment.id)
                        .values(content_sha256=sha256(b"changed").hexdigest()))
        return "unauthorized extraction"

    task._convert_to_markdown.side_effect = convert
    native_index(w)
    with DbSession.use(readonly=False) as db:
        current = db.exec(SqlBuilder.select.table(ProjectAttachmentDocument)).first()
        assert current.markdown == ""
        assert current.embedding == {}
    assert w.provider.calls == 0
    task.Broker.celery.send_task.assert_not_called()


@pytest.mark.parametrize("drift", ["membership", "bytes", "provider"])
def test_native_inference_drift_never_publishes(native_worker, drift):
    from sqlalchemy import delete, update
    from langboard_shared.core.db import DbSession, SqlBuilder
    from langboard_shared.domain.models import ProjectAssignedUser, ProjectAttachment, ProjectAttachmentDocument
    w = native_worker
    native_index(w)

    def mutate():
        if drift == "provider":
            w.binding.value = w.binding.value.replace("fixture.invalid", "changed.invalid")
            return
        with DbSession.atomic() as db:
            if drift == "membership":
                db.exec(delete(ProjectAssignedUser).where(ProjectAssignedUser.user_id == w.actor.id))
            else:
                db.exec(update(ProjectAttachment).where(ProjectAttachment.id == w.attachment.id)
                        .values(content_sha256=sha256(b"changed").hexdigest()))

    w.provider.during = mutate
    native_embed(w)
    assert w.provider.calls == 1
    with DbSession.use(readonly=False) as db:
        document = db.exec(SqlBuilder.select.table(ProjectAttachmentDocument)).first()
        assert not document.embedding.get("pointer")
        assert "secret-fixture" not in str(document.api_response())


@pytest.mark.parametrize("name", ["index_project_attachment", "embed_project_attachment"])
def test_native_broker_task_closes_service(worker, monkeypatch, name):
    from threading import Event
    from langboard_shared.Env import Env
    closed = Event()
    service = Mock()
    service.close.side_effect = closed.set
    monkeypatch.setattr(task, "DomainService", Mock(return_value=service))
    if Env.CACHE_TYPE == "in-memory":
        getattr(task, name)("[]")
    else:
        registered = task.Broker.celery.tasks["langboard_shared.tasks.docling.ProjectDocumentTask." + name]
        registered.run("[]")
    assert closed.wait(3)
    service.close.assert_called_once()
    service.project_document.load_source.assert_not_called()


def test_native_concurrent_winner_survives_failed_attempt(native_worker):
    from sqlalchemy import update
    from langboard_shared.core.db import DbSession, SqlBuilder
    from langboard_shared.domain.models import ProjectAttachmentDocument
    w = native_worker
    native_index(w)
    winner = {"status": "indexed", "winner": "another current worker"}

    def publish_other_then_fail():
        with DbSession.atomic() as db:
            db.exec(update(ProjectAttachmentDocument).where(ProjectAttachmentDocument.id == w.document.id)
                    .values(embedding=winner))
        raise RuntimeError("private provider failure")

    w.provider.during = publish_other_then_fail
    native_embed(w)
    with DbSession.use(readonly=False) as db:
        document = db.exec(SqlBuilder.select.table(ProjectAttachmentDocument)).first()
        assert document.embedding == winner


def test_native_rejected_stage_failed_delete_uses_durable_cleanup(native_worker, monkeypatch):
    from langboard_shared.core.db import DbSession, SqlBuilder
    from langboard_shared.domain.models.DocumentVectorCleanup import DocumentVectorCleanup
    from langboard_shared.tasks.docling.DocumentCleanupTask import process_cleanup
    w = native_worker
    native_index(w)
    monkeypatch.setattr(w.service.project_document, "publish_embedding", Mock(return_value=False))

    def unavailable(_, pointer):
        raise task.VectorGenerationCleanupError(pointer)

    monkeypatch.setattr(task, "delete_vector_generation", unavailable)
    native_embed(w)
    with DbSession.use(readonly=False) as db:
        events = db.exec(SqlBuilder.select.table(DocumentVectorCleanup)).all()
        assert len(events) == 1
        event = events[0]
        assert event.pointer["source_type"] == "project_attachment"
        assert event.pointer["attachment_uid"] == w.attachment.get_uid()
        assert "secret-fixture" not in str(event.pointer)
    assert process_cleanup(int(event.id))
    with DbSession.use(readonly=False) as db:
        assert db.exec(SqlBuilder.select.table(DocumentVectorCleanup)).all() == []
        assert db.exec(SqlBuilder.select.table(DocumentEmbeddingCacheReference)).all() == []


@pytest.mark.parametrize("failure", ["download", "conversion", "dispatch"])
def test_replacement_worker_failure_retains_published_outputs(native_worker, monkeypatch, failure):
    from langboard_shared.core.db import DbSession, SqlBuilder
    from langboard_shared.domain.models import DocumentVectorCleanup
    from langboard_shared.Env import Env

    w = native_worker
    native_index(w)
    native_embed(w)
    old = w.service.project_document.load_source(w.attachment.get_uid())[2]
    pointer = old.embedding["pointer"]
    old_generation = old.generation
    replacement = w.attachment.file.model_copy(update={"filename": "replacement.pdf"})
    (Env.LOCAL_STORAGE_DIR / replacement.storage_name / replacement.filename).write_bytes(b"replacement")
    with DbSession.atomic() as db:
        w.attachment.file = replacement
        w.attachment.content_sha256 = sha256(b"replacement").hexdigest()
        db.update(w.attachment)
        w.service.project_document.supersede(db, w.attachment)
    w.document = w.service.project_document.load_source(w.attachment.get_uid())[2]
    task._convert_to_markdown.side_effect = None
    task._convert_to_markdown.return_value = "Replacement extracted text"
    if failure == "download":
        monkeypatch.setattr(task.Storage, "download_file", lambda *_: False)
    elif failure == "conversion":
        task._convert_to_markdown.side_effect = RuntimeError("fixture conversion failure")
    else:
        task.Broker.celery.send_task.side_effect = RuntimeError("fixture queue failure")
    native_index(w)
    result = w.service.project_document.get_readable(w.actor, w.project, w.attachment)[2]
    assert result.embedding["pointer"] == pointer
    assert result.vector_source["generation"] == old_generation
    if failure == "dispatch":
        assert result.markdown == "Replacement extracted text" and result.status == "indexed"
        assert result.text_source["generation"] == w.document.generation
        assert result.embedding["status"] == "failed"
    else:
        assert result.markdown == "Native project text" and result.status == "failed"
        assert result.text_source["generation"] == old_generation
    with DbSession.use(readonly=False) as db:
        assert not db.exec(SqlBuilder.select.table(DocumentVectorCleanup)).all()
