"""Real native document transactions; external queue/provider configuration is a fixture."""

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from importlib.util import module_from_spec, spec_from_file_location
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import UploadFile
from langboard_shared.core.broker import Broker
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.storage import Storage
from langboard_shared.core.storage.LocalStorage import LocalStorage
from langboard_shared.domain.models import (
    DocumentVectorCleanup,
    ProjectAssignedUser,
    ProjectAttachment,
    ProjectAttachmentDocument,
    ProjectAttachmentUploadReceipt,
    ProjectRole,
    User,
)
from langboard_shared.domain.services.DomainService import DomainService
from langboard_shared.domain.services.factory.InternalBotService import InternalBotService
from langboard_shared.Env import Env
from sqlalchemy import delete, text, update


pytestmark = pytest.mark.parametrize("mirror_board", ["sqlite", "postgresql-test"], indirect=True)


@pytest.fixture
def documents(mirror_board, monkeypatch, tmp_path):
    b = mirror_board
    for model in (ProjectAttachment, ProjectAttachmentDocument, ProjectAttachmentUploadReceipt, DocumentVectorCleanup):
        model.__table__.create(b.engine)
    monkeypatch.setattr(type(Env), "LOCAL_STORAGE_DIR", property(lambda _: tmp_path / "objects"))
    monkeypatch.setattr(Storage, "_storages", {"local": LocalStorage()})
    b.enabled = True
    b.binding = SimpleNamespace(
        value='{"model_name":"fixture","api_key":"private-secret"}', get_uid=lambda: "vision-fixture"
    )
    monkeypatch.setattr(InternalBotService, "is_document_processing_enabled", lambda _: b.enabled)
    monkeypatch.setattr(InternalBotService, "get_document_vision_binding", lambda _: b.binding)
    monkeypatch.setattr(InternalBotService, "get_document_embedding_binding", lambda _: None)
    b.sent = Mock()
    monkeypatch.setattr(Broker.celery, "send_task", b.sent)
    b.domain = DomainService()
    b.documents = b.domain.project_document
    yield b
    b.domain.close()


def upload(b, data=b"source", **kwargs):
    return b.domain.project_attachment.upload(
        b.actor,
        b.source_board,
        UploadFile(BytesIO(data), filename="report.pdf"),
        request_uid=kwargs.pop("request_uid", str(uuid4())),
        **kwargs,
    )


def source(b):
    with DbSession.use(readonly=False) as db:
        return db.exec(SqlBuilder.select.table(ProjectAttachment)).first()


def index(b):
    attachment = source(b)
    current = b.documents.load_source(attachment.get_uid())
    generation = current[2].generation
    assert b.documents.claim(attachment.get_uid(), generation)
    assert b.documents.publish_extraction(
        attachment.get_uid(),
        generation,
        markdown="Private extracted text",
        keywords={"en": [" Alpha ", "alpha", "Beta"]},
        document_json={"text": "private"},
    )
    return attachment, generation


def pointer(b, attachment):
    return {
        "source_type": "project_attachment",
        "board_uid": b.source_board.get_uid(),
        "attachment_uid": attachment.get_uid(),
        "content_hash": attachment.content_sha256,
        "embedding_fingerprint": "fixture",
        "generation": uuid4().hex,
        "namespace": "fixture-owned",
        "chunk_ids": ["fixture-chunk"],
    }


def test_upload_commit_state_privacy_and_idempotence(documents):
    b = documents
    result = upload(b)
    attachment = source(b)
    board, loaded, document = b.documents.get_readable(b.actor, b.source_board, attachment)
    assert document.status == "pending" and document.source_revision == b.documents.source_revision(attachment)
    assert document.vision_config == {"binding_uid": "vision-fixture", "model_name": "fixture"}
    assert document.api_response() == {"generation": document.generation, "status": "pending"}
    assert not {"markdown", "vision_config", "embedding_config", "document_json", "embedding"} & result.keys()
    assert b.sent.call_count == 1
    receipt = b.documents.request(b.actor, board, loaded)
    assert receipt == {"attachment_uid": attachment.get_uid(), "generation": document.generation, "status": "pending"}
    assert b.sent.call_count == 1


def test_automatic_disabled_explicit_allowed_and_missing_binding(documents):
    b = documents
    b.enabled = False
    upload(b)
    attachment = source(b)
    assert b.documents.load_source(attachment.get_uid()) is None and not b.sent.called
    assert b.documents.request(b.actor, b.source_board, attachment)["status"] == "pending"
    b.binding = None
    with pytest.raises(ValueError, match="vision provider"):
        b.documents.request(b.actor, b.source_board, attachment, force=True)


def test_authority_drift_and_cross_board_denied(documents):
    b = documents
    upload(b)
    attachment = source(b)
    generation = b.documents.load_source(attachment.get_uid())[2].generation
    assert b.documents.get_readable(b.actor, b.destination, attachment) is None
    with DbSession.atomic() as db:
        db.exec(
            delete(ProjectAssignedUser).where(
                ProjectAssignedUser.project_id == b.source_board.id, ProjectAssignedUser.user_id == b.actor.id
            )
        )
    assert b.documents.get_readable(b.actor, b.source_board, attachment) is None
    assert b.documents.load_source(attachment.get_uid()) is None
    assert b.documents.claim(attachment.get_uid(), generation) is None
    assert not b.documents.publish_extraction(
        attachment.get_uid(), generation, markdown="stale", keywords={}, document_json=None
    )
    assert b.documents.request(b.actor, b.source_board, attachment) is None


def test_inactive_actor_role_revocation_and_ambiguous_source(documents):
    b = documents
    upload(b)
    attachment = source(b)
    with DbSession.atomic() as db:
        db.exec(update(User).where(User.id == b.actor.id).values(activated_at=None))
    assert b.documents.load_source(attachment.get_uid()) is None
    from langboard_shared.core.types import SafeDateTime

    with DbSession.atomic() as db:
        db.exec(update(User).where(User.id == b.actor.id).values(activated_at=SafeDateTime.now()))
        db.exec(
            update(ProjectRole)
            .where(ProjectRole.project_id == b.source_board.id, ProjectRole.user_id == b.actor.id)
            .values(actions=[])
        )
    assert b.documents.get_readable(b.actor, b.source_board, attachment) is None
    with DbSession.atomic() as db:
        db.exec(
            update(ProjectRole)
            .where(ProjectRole.project_id == b.source_board.id, ProjectRole.user_id == b.actor.id)
            .values(actions=["read"])
        )
        db.insert(
            ProjectAttachment(
                project_id=b.destination.id,
                user_id=b.actor.id,
                filename="alias.pdf",
                file=attachment.file,
                content_sha256=attachment.content_sha256,
            )
        )
    assert b.documents.load_source(attachment.get_uid()) is None


def test_claim_race_force_and_extraction_bounds(documents):
    b = documents
    upload(b)
    attachment = source(b)
    generation = b.documents.load_source(attachment.get_uid())[2].generation
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: b.documents.claim(attachment.get_uid(), generation), range(2)))
    assert sum(item is not None for item in results) == 1
    assert not b.documents.publish_extraction(
        attachment.get_uid(), generation, markdown="한" * (3 * 1024 * 1024), keywords={}, document_json=None
    )
    assert not b.documents.publish_extraction(
        attachment.get_uid(), generation, markdown="ok", keywords={}, document_json={"huge": "x" * (8 * 1024 * 1024)}
    )
    newer = b.documents.request(b.actor, b.source_board, attachment, force=True)
    assert newer["generation"] != generation
    assert not b.documents.publish_extraction(
        attachment.get_uid(), generation, markdown="old", keywords={}, document_json=None
    )
    assert b.documents.claim(attachment.get_uid(), newer["generation"])
    assert b.documents.mark_failed(attachment.get_uid(), newer["generation"], phase="extraction")
    failed = b.documents.load_source(attachment.get_uid())[2]
    assert failed.status == "failed" and failed.markdown == "" and failed.document_json is None


def test_display_rename_stable_revision_and_format_check(documents):
    b = documents
    upload(b)
    attachment = source(b)
    revision = b.documents.source_revision(attachment)
    with DbSession.atomic() as db:
        db.exec(
            update(ProjectAttachment)
            .where(ProjectAttachment.id == attachment.id)
            .values(filename="renamed.pdf", folder_id="documents")
        )
    renamed = source(b)
    assert b.documents.source_revision(renamed) == revision and b.documents.load_source(attachment.get_uid())
    with DbSession.atomic() as db:
        db.exec(update(ProjectAttachment).where(ProjectAttachment.id == attachment.id).values(filename="renamed.exe"))
    assert b.documents.load_source(attachment.get_uid()) is None
    with pytest.raises(ValueError, match="format"):
        b.documents.request(b.actor, b.source_board, attachment)


def test_embedding_cas_pointer_ownership_failure_and_scope_cleanup(documents):
    b = documents
    upload(b)
    attachment, generation = index(b)
    assert b.documents.load_source(attachment.get_uid())[2].keywords == {"en": ["Alpha", "Beta"]}
    valid = {"status": "indexed", "pointer": pointer(b, attachment)}
    for key, wrong in (
        ("source_type", "card_attachment"),
        ("board_uid", b.destination.get_uid()),
        ("attachment_uid", "other"),
        ("content_hash", "stale"),
        ("card_uid", "forbidden"),
    ):
        invalid = {"status": "indexed", "pointer": {**valid["pointer"], key: wrong}}
        assert not b.documents.publish_embedding(
            attachment.get_uid(), generation, expected_embedding={}, embedding=invalid
        )
    assert b.documents.publish_embedding(attachment.get_uid(), generation, expected_embedding={}, embedding=valid)
    assert not b.documents.publish_embedding(attachment.get_uid(), generation, expected_embedding={}, embedding=valid)
    assert not b.documents.mark_failed(attachment.get_uid(), generation, phase="embedding")
    assert b.documents.publish_embedding(
        attachment.get_uid(),
        generation,
        expected_embedding=valid,
        embedding={**valid, "status": "failed", "error": "Document embedding failed; request processing again"},
    )
    assert b.documents.load_source(attachment.get_uid())[2].embedding["pointer"] == valid["pointer"]
    b.documents.record_scope_embedding_cleanup(b.source_board)
    with DbSession.use(readonly=False) as db:
        assert not db.exec(SqlBuilder.select.table(ProjectAttachmentDocument)).all()
        assert len(db.exec(SqlBuilder.select.table(DocumentVectorCleanup)).all()) == 1


def test_changed_upload_invalidates_generation_same_bytes_replays_do_not_dispatch(documents):
    b = documents
    upload(b)
    attachment, generation = index(b)
    same = upload(b)
    upload(
        b,
        duplicate_action="replace",
        confirmation_token=same["confirmation_token"],
        replacement_uid=attachment.get_uid(),
        request_uid=same["request_uid"],
    )
    assert b.documents.load_source(attachment.get_uid())[2].generation == generation and b.sent.call_count == 1
    assert upload(
        b,
        duplicate_action="replace",
        confirmation_token=same["confirmation_token"],
        replacement_uid=attachment.get_uid(),
        request_uid=same["request_uid"],
    )["replayed"]
    changed = upload(b, data=b"changed")
    upload(
        b,
        data=b"changed",
        duplicate_action="replace",
        confirmation_token=changed["confirmation_token"],
        replacement_uid=attachment.get_uid(),
        request_uid=changed["request_uid"],
    )
    current = b.documents.load_source(attachment.get_uid())
    assert current[1].content_sha256 == sha256(b"changed").hexdigest()
    assert current[2].generation != generation and current[2].markdown == "" and b.sent.call_count == 2
    assert not b.documents.publish_extraction(
        attachment.get_uid(), generation, markdown="stale inference", keywords={}, document_json=None
    )


def test_rollback_never_dispatches_and_queue_failure_sanitized(documents):
    b = documents
    b.enabled = False
    upload(b)
    attachment = source(b)
    with pytest.raises(RuntimeError):
        with DbSession.atomic():
            b.documents.request(b.actor, b.source_board, attachment)
            raise RuntimeError("rollback")
    assert b.documents.load_source(attachment.get_uid()) is None and not b.sent.called
    b.sent.side_effect = RuntimeError("secret-provider-value")
    b.documents.request(b.actor, b.source_board, attachment)
    failed = b.documents.load_source(attachment.get_uid())[2]
    assert failed.status == "failed" and "secret" not in failed.error


def test_migration_preserves_populated_state(documents):
    b = documents
    migration_path = Path(__file__).parents[2] / "langboard/migrations/versions/20261022180000-5026bc74af98.py"
    spec = spec_from_file_location("project_document_migration", migration_path)
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    ProjectAttachmentDocument.__table__.drop(b.engine)
    with b.engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
    upload(b)
    with b.engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        with pytest.raises(RuntimeError, match="Cannot discard"):
            migration.downgrade()
        assert connection.execute(text("SELECT count(*) FROM project_attachment_document")).scalar() == 1
    with b.engine.begin() as connection:
        connection.execute(text("DELETE FROM project_attachment_document"))
        migration.op = Operations(MigrationContext.configure(connection))
        migration.downgrade()


@pytest.mark.parametrize("status", ["pending", "processing", "indexed"])
@pytest.mark.parametrize("revocation", ["membership", "activation"])
def test_new_reader_recovers_unauthorized_pending_requester(documents, status, revocation):
    b = documents
    upload(b)
    attachment = source(b)
    document = b.documents.load_source(attachment.get_uid())[2]
    old_generation = document.generation
    if status in {"processing", "indexed"}:
        assert b.documents.claim(attachment.get_uid(), old_generation)
    if status == "indexed":
        assert b.documents.publish_extraction(
            attachment.get_uid(), old_generation, markdown="Already indexed", keywords={}, document_json=None
        )
    # A second valid reader keeps an authorized in-flight generation unchanged.
    unchanged = b.documents.request(b.outsider, b.source_board, attachment)
    assert unchanged["generation"] == old_generation and b.sent.call_count == 1
    with DbSession.atomic() as db:
        if revocation == "membership":
            db.exec(
                delete(ProjectAssignedUser).where(
                    ProjectAssignedUser.project_id == b.source_board.id, ProjectAssignedUser.user_id == b.actor.id
                )
            )
        else:
            db.exec(update(User).where(User.id == b.actor.id).values(activated_at=None))
    assert b.documents.load_source(attachment.get_uid(), old_generation) is None
    recovered = b.documents.request(b.outsider, b.source_board, attachment)
    if status == "indexed":
        assert recovered["generation"] == old_generation and b.sent.call_count == 1
        assert b.documents.get_readable(b.outsider, b.source_board, attachment)[2].markdown == "Already indexed"
    else:
        assert recovered["generation"] != old_generation and recovered["status"] == "pending"
        assert b.sent.call_count == 2
        current = b.documents.load_source(attachment.get_uid(), recovered["generation"])
        assert current[2].requested_by_id == b.outsider.id
        assert b.documents.claim(attachment.get_uid(), recovered["generation"])
        assert b.documents.load_source(attachment.get_uid(), old_generation) is None


def test_ambiguous_embedding_dispatch_failure_preserves_concurrent_success(documents):
    b = documents
    upload(b)
    attachment, generation = index(b)
    successful = {"status": "indexed", "pointer": pointer(b, attachment)}

    def complete_then_report_dispatch_failure(*args, **kwargs):
        # The broker accepted the task and a worker finished before its response failed.
        assert b.documents.publish_embedding(
            attachment.get_uid(), generation, expected_embedding={}, embedding=successful
        )
        raise RuntimeError("Ambiguous queue submission failure")

    b.sent.side_effect = complete_then_report_dispatch_failure
    try:
        Broker.celery.send_task("fixture.embedding.dispatch")
    except RuntimeError:
        assert not b.documents.mark_failed(attachment.get_uid(), generation, phase="embedding")
    persisted = b.documents.load_source(attachment.get_uid(), generation)[2]
    assert persisted.embedding == successful
    with DbSession.use(readonly=False) as db:
        assert not db.exec(SqlBuilder.select.table(DocumentVectorCleanup)).all()


def test_unstarted_embedding_queue_failure_is_sanitized(documents):
    b = documents
    upload(b)
    attachment, generation = index(b)
    assert b.documents.mark_failed(attachment.get_uid(), generation, phase="embedding")
    embedding = b.documents.load_source(attachment.get_uid(), generation)[2].embedding
    assert embedding == {"status": "failed", "error": "Document embedding failed; request processing again"}
    assert not b.documents.mark_failed(attachment.get_uid(), generation, phase="embedding")


def test_ambiguous_extraction_dispatch_failure_preserves_claimed_conversion(documents):
    b = documents
    b.enabled = False
    upload(b)
    attachment = source(b)
    claimed = []

    def claim_then_report_dispatch_failure(*args, **kwargs):
        document = b.documents.load_source(attachment.get_uid())[2]
        assert b.documents.claim(attachment.get_uid(), document.generation)
        claimed.append(document.generation)
        raise RuntimeError("Ambiguous queue submission failure")

    b.sent.side_effect = claim_then_report_dispatch_failure
    receipt = b.documents.request(b.actor, b.source_board, attachment)
    assert claimed == [receipt["generation"]]
    current = b.documents.load_source(attachment.get_uid(), receipt["generation"])[2]
    assert current.status == "processing" and current.error is None
    assert b.documents.publish_extraction(
        attachment.get_uid(), receipt["generation"], markdown="Conversion finished", keywords={}, document_json=None
    )
    assert b.documents.load_source(attachment.get_uid())[2].status == "indexed"
