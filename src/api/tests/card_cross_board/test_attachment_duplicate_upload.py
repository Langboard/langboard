"""Persistent native duplicate decisions, immutable objects and historical URLs."""

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
from langboard.routes.file import FileApi
from langboard_shared.core.db import DbSession, EditorContentModel, SqlBuilder
from langboard_shared.core.routing import ApiException
from langboard_shared.core.storage import Storage
from langboard_shared.core.storage.LocalStorage import LocalStorage
from langboard_shared.domain.models import (
    AttachmentStorageObject,
    AttachmentStorageReference,
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
    ProjectActivity,
    ProjectRole,
)
from langboard_shared.domain.models.ProjectRole import ProjectRoleAction
from langboard_shared.domain.services.CardCrossBoardOperations import _grant
from langboard_shared.domain.services.factory.CardAttachmentService import CardAttachmentService
from langboard_shared.domain.services.factory.CardService import CardService
from langboard_shared.domain.services.factory.DoclingMetadataService import DoclingMetadataService
from langboard_shared.Env import Env
from langboard_shared.infrastructure.repositories.factory.CardAttachmentRepository import CardAttachmentRepository
from langboard_shared.infrastructure.repositories.factory.MetadataRepository import MetadataRepository
from sqlalchemy import text, update
from test_card_mirrors import mirror_board as mirror_board


@pytest.fixture
def uploads(mirror_board, monkeypatch, tmp_path):  # noqa: F811 - imported native fixture
    b = mirror_board
    for model in (
        CardAttachment,
        DocumentExtractionCache,
        DocumentExtractionCacheReference,
        CardMetadata,
        CardDocumentArtifact,
        DocumentKeywordIndex,
        DocumentVectorCleanup,
        DocumentEmbeddingCache,
        DocumentEmbeddingCacheReference,
    ):
        model.__table__.create(b.engine)
    monkeypatch.setattr(type(Env), "LOCAL_STORAGE_DIR", property(lambda _: tmp_path / "objects"))
    monkeypatch.setattr(Storage, "_storages", {"local": LocalStorage()})
    repo = SimpleNamespace(
        card_attachment=CardAttachmentRepository(None, None), metadata=MetadataRepository(None, None)
    )
    metadata = DoclingMetadataService(lambda _: None, lambda _: None, repo)
    service = CardAttachmentService(
        lambda cls: b.service if cls is CardService else metadata if cls is DoclingMetadataService else None,
        lambda _: None,
        repo,
    )
    service._mark_card_changed_for_unread = Mock()
    service.dispatch_created = Mock()
    import langboard_shared.domain.services.CardAttachmentUploadOperations as operations

    monkeypatch.setattr(operations, "record_project_activity", Mock())
    monkeypatch.setattr(FileApi.MiddlewareHelper, "validate_auth", lambda _: b.actor)
    b.uploads = service
    b.metadata = metadata
    b.http_service = SimpleNamespace(
        card_attachment=service,
        card=b.service,
        project=SimpleNamespace(
            get_user_role_actions_by_project=lambda actor, project: (["read"] if grant(b, actor, project) else [])
        ),
    )
    return b


def grant(b, actor, project):
    with DbSession.use(readonly=False) as db:
        return _grant(db, actor, project, ProjectRoleAction.Read)


def rows(model, *, deleted=False):
    with DbSession.use(readonly=False) as db:
        return db.exec(SqlBuilder.select.table(model, with_deleted=deleted)).all()


def upload(b, name="report.pdf", content=b"first", **decision):
    return b.uploads.upload(b.actor, b.source_board, b.source, UploadFile(BytesIO(content), filename=name), **decision)


def choose(b, preview, *, action="replace", content=b"second", name="report.pdf", **kwargs):
    return upload(
        b,
        name,
        content,
        request_uid=preview["request_uid"],
        duplicate_action=action,
        confirmation_token=preview["confirmation_token"],
        replacement_uid=preview["conflicts"][0]["uid"] if action == "replace" else None,
        **kwargs,
    )


def download(b, file):
    parts = file.path.split("/")
    response = FileApi.get_file(SimpleNamespace(scope={}), parts[-3], parts[-2], parts[-1], b.http_service)
    spool = response.background.func.__self__
    try:
        return spool.read(), response
    finally:
        spool.close()


def test_add_replace_cancel_retry_and_exact_embedded_urls(uploads):
    b = uploads
    first = upload(b)
    original = rows(CardAttachment)[0]
    old_file = original.file.model_copy()
    with DbSession.use(readonly=False) as db:
        current = db.exec(SqlBuilder.select.table(Card).where(Card.id == b.source.id)).first()
        current.description = EditorContentModel(content=f"![original]({old_file.path})")
        db.update(current)
    preview = upload(b, content=b"second")
    assert preview["confirmation_required"] and preview["conflicts"][0]["same_name"]
    assert not preview["conflicts"][0]["same_content"]
    assert len(rows(CardAttachment)) == 1
    cancelled = upload(b, content=b"second", duplicate_action="cancel", request_uid=preview["request_uid"])
    assert cancelled["cancelled"] and len(rows(CardAttachment)) == 1
    result = choose(b, preview)
    assert result["uid"] == first["uid"]
    current = rows(CardAttachment)[0]
    assert current.user_id == original.user_id and current.file != old_file
    assert Storage.get_file(old_file) == b"first"
    assert Storage.delete(old_file) and Storage.get_file(old_file) == b"first"
    assert download(b, old_file)[0] == b"second"
    assert rows(Card)[0].description.content == f"![original]({old_file.path})"
    assert choose(b, preview)["replayed"]
    assert len(rows(CardAttachment, deleted=True)) == 2
    assert len(rows(ProjectActivity)) == 2
    same = upload(b, "different.pdf", b"second")
    assert same["conflicts"][0]["same_content"] and not same["conflicts"][0]["same_name"]
    added = choose(b, same, action="add", content=b"second", name="different.pdf")
    assert added["uid"] != first["uid"]
    active = rows(CardAttachment)
    assert len(active) == 2 and active[0].file.filename != active[1].file.filename
    assert all(row.content_sha256 == sha256(b"second").hexdigest() for row in active)
    again = upload(b, content=b"third")
    choose(b, again, content=b"third")
    assert download(b, old_file)[0] == b"third"
    aliases = [row for row in rows(CardAttachment, deleted=True) if row.replaced_by_id is not None]
    assert all(row.replaced_by_id == original.id and row.deleted_at is not None for row in aliases)


def test_legacy_unknown_fingerprints_and_hidden_names_are_honest(uploads):
    b = uploads
    upload(b, ".private", b"secret")
    assert upload(b, ".other", b"secret")["conflicts"][0]["same_content"]
    original = rows(CardAttachment)[0]
    with b.engine.begin() as db:
        db.execute(update(CardAttachment).where(CardAttachment.id == original.id).values(content_sha256=None))
    preview = upload(b, ".private", b"secret")
    assert preview["conflicts"][0]["same_name"] and not preview["conflicts"][0]["same_content"]
    assert "confirmation_required" not in upload(b, ".different", b"secret")
    with b.engine.begin() as db:
        db.execute(update(Card).where(Card.id == b.source.id).values(visibility="PRIVATE", owner_user_id=b.outsider.id))
    assert upload(b, ".private", b"changed") is None


def test_stale_confirmation_and_concurrent_retries_are_fenced(uploads):
    b = uploads
    upload(b)
    preview = upload(b, content=b"second")
    with b.engine.begin() as db:
        db.execute(update(CardAttachment).values(filename="renamed.pdf"))
    stale = choose(b, preview)
    assert stale["confirmation_required"] and not stale["conflicts"]
    assert Storage.get_file(rows(CardAttachment)[0].file) == b"first"
    with b.engine.begin() as db:
        db.execute(update(CardAttachment).values(filename="report.pdf"))
    preview = upload(b, content=b"second")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: choose(b, preview), range(2)))
    assert sum(bool(result.get("replayed")) for result in results) == 1
    assert len(rows(CardAttachment, deleted=True)) == 2 and len(rows(ProjectActivity)) == 2
    with pytest.raises(ValueError, match="already belongs"):
        choose(b, preview, content=b"wrong bytes")


def test_rollback_retains_old_object_and_removes_staged_upload(uploads, monkeypatch):
    b = uploads
    upload(b)
    old = rows(CardAttachment)[0]
    preview = upload(b, content=b"second")

    def fail(*_, **__):
        raise RuntimeError("Simulated persistence failure")

    monkeypatch.setattr(b.metadata, "supersede_document", fail)
    with pytest.raises(RuntimeError, match="Simulated"):
        choose(b, preview)
    assert len(rows(CardAttachment, deleted=True)) == 1
    assert Storage.get_file(old.file) == b"first"
    assert len(rows(AttachmentStorageObject)) == len(rows(AttachmentStorageReference)) == 1
    assert len(list((Env.LOCAL_STORAGE_DIR / "attachment_objects").iterdir())) == 2
    assert len(rows(ProjectActivity)) == 1


def test_alias_uses_current_acl_and_stops_after_target_deletion(uploads):
    b = uploads
    upload(b)
    old = rows(CardAttachment)[0].file
    choose(b, upload(b, content=b"second"))
    assert download(b, old)[0] == b"second"
    with b.engine.begin() as db:
        db.execute(update(ProjectRole).values(actions=[]))
    with pytest.raises(ApiException.NotFound_404):
        download(b, old)
    with b.engine.begin() as db:
        db.execute(update(ProjectRole).values(actions=["read"]))
    with DbSession.use(readonly=False) as db:
        db.delete(rows(CardAttachment)[0])
    assert b.uploads.resolve_file_owner(old.storage_type, old.storage_name, old.filename) is None
    with pytest.raises(ApiException.NotFound_404):
        download(b, old)


@pytest.mark.parametrize("legacy", [False, True])
def test_same_bytes_preserve_native_generation_pointer_and_retained_cache(uploads, legacy):
    b = uploads
    upload(b)
    original = rows(CardAttachment)[0]
    document = {
        "attachment_uid": original.get_uid(),
        "generation": "existing-generation",
        "status": "indexed",
        "content_hash": original.content_sha256,
        "content": {"markdown": "persistent transcription"},
        "embedding": {"status": "indexed", "pointer": {"namespace": "original", "chunk_ids": ["chunk"]}},
    }
    b.metadata.save_documents(CardMetadata, b.source, [document])
    with DbSession.use(readonly=False) as db:
        entry = DocumentEmbeddingCache(cache_key="a" * 64, vector=[1.0, 2.0, 3.0])
        db.insert(entry)
        db.insert(DocumentEmbeddingCacheReference(cache_id=entry.id, use_token="b" * 32, expires_at=None))
    if legacy:
        with b.engine.begin() as db:
            db.execute(update(CardAttachment).values(content_sha256=None))
    preview = upload(b, content=b"first")
    assert preview["conflicts"][0]["same_content"] is not legacy
    choose(b, preview, content=b"first")
    assert b.metadata.get_document_by_attachment_uid(CardMetadata, b.source, original.get_uid()) == document
    assert rows(DocumentVectorCleanup) == [] and len(rows(DocumentEmbeddingCacheReference)) == 1
    assert rows(CardAttachment)[0].file == original.file
    assert b.uploads.dispatch_created.call_args.kwargs["include_document_processing"] is False


def test_upload_hash_reads_are_bounded_and_source_acl_is_rechecked(uploads):
    b = uploads

    class Bounded(BytesIO):
        def read(self, size=-1):
            assert 0 < size <= 65536
            return super().read(size)

    result = b.uploads.upload(
        b.actor, b.source_board, b.source, UploadFile(Bounded(b"x" * 150000), filename="bounded.bin")
    )
    assert result and rows(CardAttachment)[0].content_sha256 == sha256(b"x" * 150000).hexdigest()
    preview = upload(b, "bounded.bin", b"new")
    with b.engine.begin() as db:
        db.execute(update(ProjectRole).values(actions=[]))
    assert choose(b, preview, name="bounded.bin", content=b"new") is None


@pytest.mark.parametrize("mirror_board", ["postgresql-test"], indirect=True)
def test_postgresql_duplicate_replacement_and_retry(uploads):
    b = uploads
    upload(b)
    old = rows(CardAttachment)[0].file
    preview = upload(b, content=b"second")
    choose(b, preview)
    assert choose(b, preview)["replayed"] and download(b, old)[0] == b"second"


@pytest.mark.parametrize("mirror_board", [None, "postgresql-test"], indirect=True)
def test_additive_migration_preserves_legacy_values_and_refuses_alias_loss(uploads):
    b = uploads
    migration_path = Path(__file__).parents[2] / "langboard/migrations/versions/20261022130000-2d03e9417c65.py"
    spec = spec_from_file_location("attachment_duplicate_migration", migration_path)
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    with b.engine.begin() as db:
        db.execute(text("DROP TABLE card_document_artifact"))
        db.execute(text("DROP TABLE card_attachment"))
        db.execute(
            text(
                "CREATE TABLE card_attachment (id BIGINT PRIMARY KEY, card_id BIGINT, file JSON, deleted_at TIMESTAMP, filename TEXT)"
            )
        )
        db.execute(text("INSERT INTO card_attachment (id,card_id,filename) VALUES (1,2,'legacy.pdf')"))
        with Operations.context(MigrationContext.configure(db)):
            migration.upgrade()
        legacy = db.execute(text("SELECT filename,content_sha256,replaced_by_id FROM card_attachment")).one()
        assert tuple(legacy) == ("legacy.pdf", None, None)
        db.execute(text("UPDATE card_attachment SET replaced_by_id=3"))
        with Operations.context(MigrationContext.configure(db)), pytest.raises(RuntimeError, match="historical"):
            migration.downgrade()
        db.execute(text("UPDATE card_attachment SET replaced_by_id=NULL"))
        with Operations.context(MigrationContext.configure(db)):
            migration.downgrade()
        assert db.execute(text("SELECT filename FROM card_attachment")).scalar_one() == "legacy.pdf"


def test_replacement_requires_attachment_owner_or_native_update_grant(uploads):
    b = uploads
    upload(b)
    with b.engine.begin() as db:
        db.execute(update(CardAttachment).values(user_id=b.outsider.id))
    preview = upload(b, content=b"second")
    assert not preview["conflicts"][0]["can_replace"]
    with pytest.raises(PermissionError):
        choose(b, preview)
    assert Storage.get_file(rows(CardAttachment)[0].file) == b"first"
    # Native action spelling comes from the model, never a caller-supplied role name.
    with b.engine.begin() as db:
        db.execute(update(ProjectRole).values(actions=["read", ProjectRoleAction.CardUpdate.value]))
    result = choose(b, preview)
    assert result["uid"] == rows(CardAttachment)[0].get_uid()
    assert result["user"]["uid"] == b.outsider.get_uid()
    assert rows(CardAttachment)[0].user_id == b.outsider.id


def test_alias_rejects_cross_card_cycles_transitive_and_ambiguous_targets(uploads):
    b = uploads
    upload(b)
    old = rows(CardAttachment)[0].file
    choose(b, upload(b, content=b"second"))
    active = rows(CardAttachment)[0]
    alias = next(row for row in rows(CardAttachment, deleted=True) if row.replaced_by_id is not None)

    def resolve():
        return b.uploads.resolve_file_owner(old.storage_type, old.storage_name, old.filename)

    with b.engine.begin() as db:
        db.execute(update(CardAttachment).where(CardAttachment.id == alias.id).values(card_id=int(b.source.id) + 1))
    assert resolve() is None
    with b.engine.begin() as db:
        db.execute(
            update(CardAttachment)
            .where(CardAttachment.id == alias.id)
            .values(card_id=b.source.id, replaced_by_id=alias.id)
        )
    assert resolve() is None
    with b.engine.begin() as db:
        db.execute(update(CardAttachment).where(CardAttachment.id == alias.id).values(replaced_by_id=active.id))
        db.execute(update(CardAttachment).where(CardAttachment.id == active.id).values(replaced_by_id=alias.id))
    assert resolve() is None
    with b.engine.begin() as db:
        db.execute(update(CardAttachment).where(CardAttachment.id == active.id).values(replaced_by_id=None))
    with DbSession.use(readonly=False) as db:
        db.insert(
            CardAttachment(card_id=active.card_id, user_id=active.user_id, filename=active.filename, file=active.file)
        )
    assert resolve() is None


def test_post_download_alias_revocation_never_returns_buffered_bytes(uploads, monkeypatch):
    b = uploads
    upload(b)
    old = rows(CardAttachment)[0].file
    choose(b, upload(b, content=b"second"))
    original_download = Storage.download_file

    def revoke(file, destination):
        ok = original_download(file, destination)
        with b.engine.begin() as db:
            db.execute(update(ProjectRole).values(actions=[]))
        return ok

    monkeypatch.setattr(Storage, "download_file", revoke)
    with pytest.raises(ApiException.NotFound_404):
        download(b, old)


def test_receipt_after_deletion_does_not_resurrect_attachment(uploads):
    b = uploads
    request_uid = str(uuid4())
    upload(b, request_uid=request_uid)
    with DbSession.use(readonly=False) as db:
        db.delete(rows(CardAttachment)[0])
    assert upload(b, request_uid=request_uid) is None
    assert rows(CardAttachment) == [] and len(rows(ProjectActivity)) == 1


def test_transport_failure_keeps_committed_bytes_and_retry_receipt(uploads):
    from langboard_shared.domain.services.CardChangeTransaction import dispatch_after_commit

    b = uploads
    transport = Mock(side_effect=RuntimeError("Simulated broker failure"))
    b.uploads.dispatch_created.side_effect = lambda *args, **kwargs: dispatch_after_commit(transport)
    request_uid = str(uuid4())
    result = upload(b, request_uid=request_uid)
    assert result and Storage.get_file(rows(CardAttachment)[0].file) == b"first"
    assert upload(b, request_uid=request_uid)["replayed"]
    b.uploads.dispatch_created.assert_called_once()
    transport.assert_called_once()


def test_different_bytes_retain_native_pointer_until_publication_or_deletion(uploads, monkeypatch):
    b = uploads
    upload(b)
    original = rows(CardAttachment)[0]
    document = {
        "attachment_uid": original.get_uid(),
        "generation": "old-generation",
        "status": "indexed",
        "content_hash": original.content_sha256,
        "content": {"markdown": "old text"},
        "embedding": {"status": "indexed", "pointer": {"namespace": "old", "chunk_ids": ["chunk"]}},
    }
    b.metadata.save_documents(CardMetadata, b.source, [document])
    from langboard_shared.tasks.docling import DocumentCleanupTask

    monkeypatch.setattr(DocumentCleanupTask, "enqueue_cleanup", Mock(return_value=True))
    choose(b, upload(b, content=b"second"))
    retained = b.metadata.get_document_by_attachment_uid(CardMetadata, b.source, original.get_uid())
    assert retained["generation"] != document["generation"]
    assert retained["content"] == document["content"] and retained["embedding"] == document["embedding"]
    assert retained["text_source"]["generation"] == document["generation"]
    assert rows(DocumentVectorCleanup) == []
    b.metadata.delete_document_by_attachment_uid(CardMetadata, b.source, original.get_uid())
    cleanup = rows(DocumentVectorCleanup)
    assert len(cleanup) == 1 and cleanup[0].pointer["namespace"] == "old"


def test_additional_same_content_uses_native_config_bound_embedding_cache(uploads):
    from langboard_shared.tasks.docling.DocumentEmbeddingCache import cached_document_embeddings
    from langchain_core.embeddings import Embeddings

    b = uploads
    upload(b)
    preview = upload(b, "second-name.pdf")
    choose(b, preview, action="add", content=b"first", name="second-name.pdf")
    active = rows(CardAttachment)
    assert len(active) == 2 and active[0].file != active[1].file

    class Provider(Embeddings):
        calls = 0

        def embed_documents(self, texts):
            self.calls += 1
            return [[1.0, 2.0, 3.0] for _ in texts]

        def embed_query(self, text):
            raise AssertionError("No query inference during upload")

    provider = Provider()
    tokens = []
    for attachment in active:
        with cached_document_embeddings(
            provider,
            binding_uid="native-binding",
            content_hash=attachment.content_sha256,
            extraction_identity=sha256(b"committed-extraction").hexdigest(),
            fingerprint=sha256(b"same provider configuration").hexdigest(),
            dimensions=3,
            timeout_seconds=1,
        ) as cache:
            assert cache.embed_documents(["Same native transcription"]) == [[1.0, 2.0, 3.0]]
            tokens.append(cache.token)
            cache.retain()
    assert provider.calls == 1 and tokens[0] != tokens[1]


def test_route_preserves_native_create_and_exposes_explicit_choice_statuses(uploads):
    import json
    from langboard.routes.board.BoardCardAttachmentApi import upload_card_attachment

    b = uploads
    service = SimpleNamespace(card=b.service, card_attachment=b.uploads)

    def call(content=b"first", **decision):
        return upload_card_attachment(
            b.source_board.get_uid(),
            b.source.get_uid(),
            SimpleNamespace(scope={}),
            UploadFile(BytesIO(content), filename="report.pdf"),
            b.actor,
            service,
            duplicate_action=decision.get("duplicate_action"),
            confirmation_token=decision.get("confirmation_token"),
            replacement_uid=decision.get("replacement_uid"),
            request_uid=decision.get("request_uid"),
        )

    assert (
        upload_card_attachment(
            b.source_board.get_uid(),
            b.source.get_uid(),
            SimpleNamespace(scope={}),
            UploadFile(BytesIO(b"first"), filename="report.pdf"),
            b.actor,
            service,
        ).status_code
        == 201
    )
    response = call(b"second")
    assert response.status_code == 409
    preview = json.loads(response.body)
    decision = dict(
        duplicate_action="replace",
        confirmation_token=preview["confirmation_token"],
        request_uid=preview["request_uid"],
        replacement_uid=preview["conflicts"][0]["uid"],
    )
    assert call(b"second", **decision).status_code == 200
    assert json.loads(call(b"second", **decision).body)["replayed"]


@pytest.mark.parametrize("failure", ["missing", "oversized"])
def test_selected_legacy_verification_fails_without_changing_provenance(uploads, monkeypatch, failure):
    b = uploads
    upload(b)
    original = rows(CardAttachment)[0]
    with b.engine.begin() as db:
        db.execute(update(CardAttachment).values(content_sha256=None))
    preview = upload(b, content=b"x")
    if failure == "missing":
        from langboard_shared.domain.services.AttachmentStorageOperations import resolve

        managed, physical = resolve(original.file.storage_type, original.file.storage_name, original.file.filename)
        assert managed and physical is not None
        (Env.LOCAL_STORAGE_DIR / physical[0] / physical[1]).unlink()
    else:
        monkeypatch.setattr(type(Env), "MAX_FILE_SIZE_MB", property(lambda _: 0.000002))
    with pytest.raises(ValueError):
        choose(b, preview, content=b"x")
    assert rows(CardAttachment)[0].file == original.file
    assert len(rows(CardAttachment, deleted=True)) == 1


@pytest.mark.parametrize("mirror_board", [None, "postgresql-test"], indirect=True)
def test_concurrent_distinct_confirmations_cannot_silently_replace_twice(uploads):
    b = uploads
    upload(b)
    previews = [upload(b, content=b"second") for _ in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda preview: choose(b, preview), previews))
    assert sum(bool(result.get("confirmation_required")) for result in results) == 1
    assert len(rows(CardAttachment, deleted=True)) == 2
    assert len(rows(ProjectActivity)) == 2


def test_foreign_hidden_card_files_do_not_become_conflicts(uploads):
    b = uploads
    with DbSession.use(readonly=False) as db:
        hidden = Card(
            project_id=b.source_board.id,
            project_column_id=b.source.project_column_id,
            created_by_user_id=b.outsider.id,
            title="Other hidden card",
            visibility="PRIVATE",
            owner_user_id=b.outsider.id,
        )
        db.insert(hidden)
    result = upload(b)
    attachment = rows(CardAttachment)[0]
    with b.engine.begin() as db:
        db.execute(update(CardAttachment).where(CardAttachment.id == attachment.id).values(card_id=hidden.id))
    independent = upload(b)
    assert independent["uid"] != result["uid"] and "conflicts" not in independent


def test_automation_cannot_use_human_upload_to_bypass_app_ownership(uploads):
    from langboard_shared.domain.models import Bot, CardAppOwnership
    from langboard_shared.domain.services.AppGovernance import AppGovernanceDenied

    b = uploads
    with DbSession.use(readonly=False) as db:
        db.insert(CardAppOwnership(card_id=b.source.id, app_key="owned-app"))
    with pytest.raises(AppGovernanceDenied):
        b.uploads.upload(
            Bot(
                name="Unbound automation",
                platform="default",
                platform_running_type="default",
                bot_uname="unbound",
                app_api_token="test-only",
            ),
            b.source_board,
            b.source,
            UploadFile(BytesIO(b"forbidden"), filename="file.pdf"),
        )
    assert rows(CardAttachment) == []


@pytest.mark.parametrize("dialect", ["mysql", "mariadb"])
def test_migration_keeps_portable_native_engine_support(monkeypatch, dialect):
    migration_path = Path(__file__).parents[2] / "langboard/migrations/versions/20261022130000-2d03e9417c65.py"
    spec = spec_from_file_location("portable_duplicate_migration", migration_path)
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    operations = Mock()
    operations.get_bind.return_value.dialect.name = dialect
    operations.get_bind.return_value.execute.return_value.first.return_value = None
    monkeypatch.setattr(migration, "op", operations)
    migration.upgrade()
    assert operations.add_column.call_count == 2 and operations.create_index.call_count == 2
    operations.execute.assert_not_called()
    migration.downgrade()
    assert operations.drop_column.call_count == 2 and operations.drop_index.call_count == 2


def test_vanished_conflict_requires_refreshed_explicit_add_then_replays_once(uploads):
    b = uploads
    upload(b)
    preview = upload(b, content=b"second")
    with DbSession.use(readonly=False) as db:
        db.delete(rows(CardAttachment)[0])
    refreshed = choose(b, preview)
    assert refreshed["confirmation_required"] and refreshed["conflicts"] == []
    assert rows(CardAttachment) == []
    decision = dict(
        duplicate_action="add", request_uid=refreshed["request_uid"], confirmation_token=refreshed["confirmation_token"]
    )
    created = upload(b, content=b"second", **decision)
    assert "confirmation_required" not in created and created["uid"]
    assert upload(b, content=b"second", **decision)["replayed"]
    assert len(rows(CardAttachment)) == 1 and len(rows(ProjectActivity)) == 2


@pytest.mark.parametrize("mirror_board", ["postgresql-test"], indirect=True)
def test_postgresql_upload_and_native_ownership_revocation_share_lock_order(uploads, monkeypatch):
    from importlib import import_module
    from threading import Event, current_thread
    from langboard_shared.domain.models import CardAppOwnership, CardAppOwnershipAudit, User
    from sqlalchemy import event

    b = uploads
    governance = import_module("langboard_shared.domain.services.CardAppGovernance")
    CardAppOwnershipAudit.__table__.create(b.engine)
    with DbSession.use(readonly=False) as db:
        ownership = CardAppOwnership(card_id=b.source.id, app_key="owned-app")
        db.insert(ownership)
        db.exec(update(ProjectRole).values(actions=["read", "update"]))
    monkeypatch.setattr(governance.CardPublisher, "put_dispather", Mock())
    actor_locked, upload_waiting = Event(), Event()
    native_current = governance._current

    def pause_after_actor(db, model, identifier, **kwargs):
        row = native_current(db, model, identifier, **kwargs)
        if model is User:
            actor_locked.set()
            assert upload_waiting.wait(4), "Upload did not reach the native actor lock"
        return row

    monkeypatch.setattr(governance, "_current", pause_after_actor)

    def observe_actor_lock(connection, cursor, statement, parameters, context, executemany):
        if current_thread().name.startswith("upload") and 'FROM "user"' in statement and "FOR UPDATE" in statement:
            upload_waiting.set()

    event.listen(b.engine, "before_cursor_execute", observe_actor_lock)
    try:
        with (
            ThreadPoolExecutor(max_workers=1, thread_name_prefix="ownership") as owners,
            ThreadPoolExecutor(max_workers=1, thread_name_prefix="upload") as writers,
        ):
            revoke = owners.submit(
                governance.set_card_app_ownership, b.actor, b.source_board.id, b.source.id, None, ownership.revision
            )
            assert actor_locked.wait(4), "Native governance did not acquire its actor lock"
            writing = writers.submit(upload, b)
            assert revoke.result(timeout=8)["app_key"] is None
            assert writing.result(timeout=8)["uid"]
    finally:
        event.remove(b.engine, "before_cursor_execute", observe_actor_lock)
    assert len(rows(CardAttachment)) == 1
