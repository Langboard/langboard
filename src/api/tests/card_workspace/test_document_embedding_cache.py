"""Deduplicate inference without sharing attachment identity, permissions or cleanup."""

import asyncio
from datetime import timedelta
from hashlib import sha256
from unittest.mock import Mock
import pytest
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import DocumentEmbeddingCache, DocumentEmbeddingCacheReference
from langboard_shared.tasks.docling.DocumentEmbeddingCache import (
    CachedDocumentEmbeddings,
    EmbeddingInferenceUncertain,
    activate_cache_references,
    cached_document_embeddings,
    recover_expired_cache_references,
    release_cache_references,
)
from langchain_core.embeddings import Embeddings
from sqlalchemy import update
from test_document_cleanup_outbox import cleanup_store, document_source  # noqa: F401
from test_document_mcp_read import binding_store  # noqa: F401


class Provider(Embeddings):
    def __init__(self):
        self.calls = []
        self.query = Mock(return_value=[1.0, 2.0, 3.0])

    def embed_documents(self, texts):
        self.calls.append(texts)
        # A remote call must never run inside a database transaction.
        assert DbSession._atomic_session.get() is None
        return [[float(len(text)), 2.0, 3.0] for text in texts]

    def embed_query(self, text):
        return self.query(text)


def config(**overrides):
    return {
        "binding_uid": "private-binding",
        "content_hash": sha256(b"file").hexdigest(),
        "extraction_identity": sha256(b"committed-extraction").hexdigest(),
        "fingerprint": sha256(b"model").hexdigest(),
        "dimensions": 3,
        "timeout_seconds": 1,
        **overrides,
    }


def rows(model=DocumentEmbeddingCache):
    with DbSession.use(readonly=False) as db:
        return db.exec(SqlBuilder.select.table(model)).all()


def retain(cache):
    with DbSession.atomic() as db:
        activate_cache_references(db, cache.token)
    cache.retain()


def test_identical_uploads_call_once_and_release_only_last_reference(binding_store):  # noqa: F811
    provider = Provider()
    with cached_document_embeddings(provider, **config()) as first:
        expected = first.embed_documents(["alpha", "alpha", "beta"])
        retain(first)
    with cached_document_embeddings(provider, **config()) as second:
        assert second.embed_documents(["beta", "alpha"]) == [expected[2], expected[0]]
        retain(second)
    assert len(provider.calls) == 1 and len(provider.calls[0]) == 2
    assert len(rows()) == 2 and len(rows(DocumentEmbeddingCacheReference)) == 4
    assert all(row.expires_at is None for row in rows(DocumentEmbeddingCacheReference))
    assert "alpha" not in str([row.model_dump() for row in rows()])
    release_cache_references(first.token)
    assert len(rows()) == 2
    release_cache_references(second.token)
    assert rows() == [] and rows(DocumentEmbeddingCacheReference) == []
    release_cache_references(second.token)


@pytest.mark.parametrize("change", ["binding_uid", "content_hash", "extraction_identity", "fingerprint"])
def test_file_or_provider_changes_never_reuse_old_vectors(binding_store, change):  # noqa: F811
    provider = Provider()
    original = config()
    changed = {**original, change: "other-binding" if change == "binding_uid" else sha256(b"changed").hexdigest()}
    for namespace in (original, changed):
        with cached_document_embeddings(provider, **namespace) as cache:
            cache.embed_documents(["same chunk"])
            retain(cache)
    assert len(provider.calls) == 2


def test_rejected_publication_releases_unpublished_results(binding_store):  # noqa: F811
    with cached_document_embeddings(Provider(), **config()) as cache:
        cache.embed_documents(["discarded"])
    assert rows() == [] and rows(DocumentEmbeddingCacheReference) == []


@pytest.mark.parametrize("identity", [None, "", "current-parser", "0" * 63])
def test_embedding_requires_authoritative_extraction_identity_before_inference(identity):
    provider = Provider()
    with pytest.raises(ValueError, match="Authoritative content, extraction and model hashes"):
        CachedDocumentEmbeddings(provider, **config(extraction_identity=identity))
    assert provider.calls == []


def test_expired_undispatched_claim_allows_retry_and_fences_old_worker(binding_store):  # noqa: F811
    provider = Provider()
    stale = CachedDocumentEmbeddings(provider, **config())
    identifier, _, owned = stale._claim(stale._key("alpha"))
    assert owned
    with DbSession.atomic() as db:
        db.exec(
            update(DocumentEmbeddingCache)
            .where(DocumentEmbeddingCache.id == identifier)
            .values(lease_expires_at=SafeDateTime.now() - timedelta(seconds=1))
        )
    with cached_document_embeddings(provider, **config()) as retry:
        assert retry.embed_documents(["alpha"]) == [[5.0, 2.0, 3.0]]
        retain(retry)
    stale._release_claims([identifier])
    release_cache_references(stale.token)
    assert len(provider.calls) == 1
    assert rows()[0].vector == [5.0, 2.0, 3.0] and rows()[0].lease_token is None


def test_failed_inference_stays_fenced_after_reference_expiry(binding_store, monkeypatch):  # noqa: F811
    provider = Provider()
    provider.embed_documents = Mock(side_effect=TimeoutError("private-provider-secret"))
    with pytest.raises(EmbeddingInferenceUncertain) as failed:
        with cached_document_embeddings(provider, **config()) as cache:
            cache.embed_documents(["alpha"])
    assert isinstance(failed.value.__cause__, TimeoutError)
    assert rows()[0].lease_token == cache.token and rows()[0].lease_expires_at is None
    # Reference cleanup simulates a later worker after the original process is gone.
    with DbSession.atomic() as db:
        db.exec(update(DocumentEmbeddingCacheReference).values(
            expires_at=SafeDateTime.now() - timedelta(hours=9)
        ))
    assert recover_expired_cache_references() == 1
    assert rows(DocumentEmbeddingCacheReference) == []
    assert len(rows()) == 1 and rows()[0].lease_token == cache.token
    with cached_document_embeddings(provider, **config()) as retry:
        with monkeypatch.context() as clock:
            clock.setattr("time.monotonic", Mock(side_effect=[0, retry.inference_budget_seconds + 1]))
            with pytest.raises(EmbeddingInferenceUncertain, match="automatic resend disabled"):
                retry.embed_documents(["alpha"])
    assert provider.embed_documents.call_count == 1
    release_cache_references(retry.token)
    assert len(rows()) == 1 and rows()[0].vector is None
    assert "private-provider-secret" not in str([row.model_dump() for row in rows()])


def test_abandoned_references_expire_but_published_ones_survive(binding_store):  # noqa: F811
    provider = Provider()
    active = CachedDocumentEmbeddings(provider, **config())
    active.embed_documents(["active"])
    retain(active)
    orphan = CachedDocumentEmbeddings(provider, **config())
    orphan.embed_documents(["orphan"])
    with DbSession.atomic() as db:
        db.exec(
            update(DocumentEmbeddingCacheReference)
            .where(DocumentEmbeddingCacheReference.use_token == orphan.token)
            .values(expires_at=SafeDateTime.now() - timedelta(seconds=1))
        )
    assert recover_expired_cache_references(limit=1) == 1
    assert len(rows()) == 1 and rows(DocumentEmbeddingCacheReference)[0].use_token == active.token


def test_queries_bypass_cache_and_corrupt_vectors_are_rejected(binding_store):  # noqa: F811
    provider = Provider()
    with cached_document_embeddings(provider, **config()) as cache:
        assert cache.embed_query("private query") == [1.0, 2.0, 3.0]
        assert rows() == []
        cache.embed_documents(["alpha"])
        retain(cache)
    # A dimension mismatch is valid JSON on both databases and must still be
    # rejected by the cache reader. PostgreSQL itself rejects Infinity in JSON.
    with DbSession.atomic() as db:
        db.exec(update(DocumentEmbeddingCache).values(vector=[1.0, 2.0]))
    with cached_document_embeddings(provider, **config()) as corrupt:
        with pytest.raises(ValueError, match="dimension or numeric"):
            corrupt.embed_documents(["alpha"])
    assert len(provider.calls) == 1
    from langboard_shared.core.db.DbEngine import DbEngine
    from sqlalchemy.exc import DataError

    if DbEngine.get_main_engine().dialect.name == "postgresql":
        with pytest.raises(DataError):
            with DbSession.atomic() as db:
                db.exec(update(DocumentEmbeddingCache).values(vector=[float("inf"), 2.0, 3.0]))
    else:
        with DbSession.atomic() as db:
            db.exec(update(DocumentEmbeddingCache).values(vector=[float("inf"), 2.0, 3.0]))
        with cached_document_embeddings(provider, **config()) as corrupt:
            with pytest.raises(ValueError, match="dimension or numeric"):
                corrupt.embed_documents(["alpha"])


def test_activation_rollback_keeps_references_pending(binding_store):  # noqa: F811
    cache = CachedDocumentEmbeddings(Provider(), **config())
    cache.embed_documents(["alpha"])
    with pytest.raises(RuntimeError):
        with DbSession.atomic() as db:
            activate_cache_references(db, cache.token)
            raise RuntimeError("publication rolled back")
    assert rows(DocumentEmbeddingCacheReference)[0].expires_at is not None
    release_cache_references(cache.token)
    assert rows() == []


def test_concurrent_uploads_share_one_model_call(binding_store, monkeypatch, tmp_path):  # noqa: F811
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from langboard_shared.core.db.DbEngine import DbEngine
    from sqlalchemy import create_engine

    # Separate connections are essential: in-memory SQLite cannot prove worker races.
    engine = binding_store
    if engine.dialect.name == "sqlite":
        engine = create_engine(f"sqlite:///{tmp_path / 'concurrent-cache.sqlite'}")
        DocumentEmbeddingCache.__table__.create(engine)
        DocumentEmbeddingCacheReference.__table__.create(engine)
        monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    provider = Provider()
    started, release, second_claimed = Event(), Event(), Event()
    original = provider.embed_documents

    def infer(texts):
        started.set()
        assert release.wait(5), "inference release timed out"
        return original(texts)

    provider.embed_documents = infer
    first = CachedDocumentEmbeddings(provider, **config())
    second = CachedDocumentEmbeddings(provider, **config())
    claim = second._claim

    def observe(key):
        result = claim(key)
        second_claimed.set()
        return result

    second._claim = observe

    def run(cache):
        result = cache.embed_documents(["alpha", "alpha"])
        retain(cache)
        return result

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            one = executor.submit(run, first)
            try:
                assert started.wait(3)
                two = executor.submit(run, second)
                assert second_claimed.wait(3)
            finally:
                release.set()
            assert one.result(timeout=5) == two.result(timeout=5)
        assert len(provider.calls) == 1 and len(rows(DocumentEmbeddingCacheReference)) == 2
        release_cache_references(first.token)
        release_cache_references(second.token)
        assert rows() == []
    finally:
        if engine is not binding_store:
            engine.dispose()


def test_native_task_publishes_separate_attachment_indices_and_cleans_references(
    document_source,  # noqa: F811
    cleanup_store,  # noqa: F811
    monkeypatch,
    tmp_path,
):
    from copy import deepcopy
    from types import SimpleNamespace
    from uuid import uuid4
    from langboard_shared.core.db.DbEngine import DbEngine
    from langboard_shared.domain.models import Card, CardAttachment, CardMetadata
    from langboard_shared.domain.models.DocumentExtractionCache import (
        DocumentExtractionCache,
        DocumentExtractionCacheReference,
    )
    from langboard_shared.domain.models.InternalBot import InternalBotType
    from langboard_shared.domain.services import DomainService
    from langboard_shared.tasks.docling import DocumentEmbeddingTask as task
    from langboard_shared.tasks.docling.DocumentExtractionCache import (
        activate_extraction_reference,
        cached_document_extraction,
    )
    from langboard_shared.tasks.docling.DocumentRetrievalSettings import DocumentRetrievalSettings
    from langboard_shared.tasks.docling.DocumentSqliteStore import open_sqlite_vector_store
    from langboard_shared.tasks.docling.DocumentVectorStore import astage_vector_generation as stage_vector_generation
    from langboard_shared.tasks.docling.DocumentVectorStore import open_document_vector_store

    metadata, card, attachment, document = document_source
    native = DomainService()
    for model in (DocumentExtractionCache, DocumentExtractionCacheReference):
        model.__table__.create(DbEngine.get_main_engine(), checkfirst=True)
    generation = uuid4().hex

    class AsyncOnlyProvider(Provider):
        def embed_documents(self, texts):
            raise AssertionError("The worker must use the official async provider path")

        async def aembed_documents(self, texts):
            await asyncio.sleep(0)
            return Provider.embed_documents(self, texts)

    provider = AsyncOnlyProvider()
    snapshot = {"binding_uid": "fixture-binding"}
    with DbSession.atomic() as db:
        other_card = Card(
            project_id=card.project_id, project_column_id=card.project_column_id, title="Other attachment owner"
        )
        db.insert(other_card)
        other = CardAttachment(
            user_id=attachment.user_id, card_id=other_card.id, filename=attachment.filename, file=attachment.file
        )
        db.insert(other)
    for source_card, source_attachment in ((card, attachment), (other_card, other)):
        doc = {
            **deepcopy(document),
            "generation": generation,
            "attachment_uid": source_attachment.get_uid(),
            "content_hash": config()["content_hash"],
            "embedding_config": snapshot,
            "embedding": {"status": "pending"},
        }
        metadata.save_documents(CardMetadata, source_card, [doc])
        with cached_document_extraction(
            binding_uid="vision-fixture", content_hash=config()["content_hash"],
            vision_config={"binding_uid": "vision-fixture"}, filename="report.pdf", timeout_seconds=1,
        ) as extraction:
            extraction.get_or_convert(lambda: {"markdown": "Shared extraction"})
            with DbSession.atomic() as db:
                assert activate_extraction_reference(
                    db, extraction.token, source_type="card_attachment",
                    source_uid=source_attachment.get_uid(), generation=generation,
                )
            extraction.retain()
    settings = DocumentRetrievalSettings(store="sqlite", dimensions=3, timeout_seconds=1.0)
    monkeypatch.setattr(
        task, "Env", SimpleNamespace(DATA_DIR=tmp_path, get_from_env=lambda *_: "https://fixture.invalid")
    )
    monkeypatch.setattr(task, "create_document_embeddings", lambda *_: provider)
    monkeypatch.setattr(task, "resolve_embedding_snapshot", lambda *_: "private")
    monkeypatch.setattr(
        task,
        "validate_embedding_config",
        lambda *_: ({"base_url": "https://fixture.invalid", "model_name": "fixture"}, settings),
    )
    monkeypatch.setattr(task, "open_document_vector_store", open_document_vector_store)
    monkeypatch.setattr(task, "stage_vector_generation", stage_vector_generation)
    monkeypatch.setattr(metadata, "publish_update", Mock())
    service = SimpleNamespace(
        card=native.card,
        card_attachment=native.card_attachment,
        project=native.project,
        docling_metadata=metadata,
        internal_bot=SimpleNamespace(
            get_current_by_id_like=lambda *_: SimpleNamespace(
                bot_type=InternalBotType.DocumentEmbedding, value="private"
            )
        ),
    )
    try:
        pointers = []
        for source_card, source_attachment in ((card, attachment), (other_card, other)):
            asyncio.run(task.embed_transcription(service, source_attachment.get_uid(), generation))
            saved = metadata.get_document_by_attachment_uid(CardMetadata, source_card, source_attachment.get_uid())
            assert saved["embedding"]["status"] == "indexed", saved["embedding"]
            pointers.append(saved["embedding"]["pointer"])
        assert len(provider.calls) == 1
        assert pointers[0]["chunk_ids"] != pointers[1]["chunk_ids"]
        assert pointers[0]["card_uid"] != pointers[1]["card_uid"]
        assert all(ref.expires_at is None for ref in rows(DocumentEmbeddingCacheReference))
        # Legacy queued work cannot infer lineage from current settings or destroy published vectors.
        from sqlalchemy import delete

        with DbSession.atomic() as db:
            db.exec(delete(DocumentExtractionCacheReference).where(
                DocumentExtractionCacheReference.source_uid == attachment.get_uid(),
            ))
        asyncio.run(task.embed_transcription(service, attachment.get_uid(), generation))
        completed = metadata.get_document_by_attachment_uid(CardMetadata, card, attachment.get_uid())
        assert completed["embedding"]["status"] == "indexed"
        assert completed["embedding"]["pointer"] == pointers[0] and len(provider.calls) == 1
        # A new generation needs explicit extraction lineage; redelivery above does not.
        next_generation = uuid4().hex
        metadata.save_documents(CardMetadata, card, [{**completed, "generation": next_generation}])
        asyncio.run(task.embed_transcription(service, attachment.get_uid(), next_generation))
        failed = metadata.get_document_by_attachment_uid(CardMetadata, card, attachment.get_uid())["embedding"]
        assert failed["status"] == "failed" and "explicitly reprocess" in failed["error"]
        assert failed["pointer"] == pointers[0] and len(provider.calls) == 1
        path = tmp_path / "document-retrieval" / (pointers[0]["embedding_fingerprint"] + ".sqlite")
        task.remove_recorded_embedding(pointers[0])
        with open_sqlite_vector_store(path, provider, dimensions=3) as store:
            assert store.get_by_ids(pointers[0]["chunk_ids"]) == []
            assert store.get_by_ids(pointers[1]["chunk_ids"])
        assert len(rows()) == 1
        task.remove_recorded_embedding(pointers[1])
        assert rows() == [] and rows(DocumentEmbeddingCacheReference) == []
    finally:
        native.close()


def test_expiry_recovery_cannot_remove_a_concurrently_published_reference(binding_store, monkeypatch):  # noqa: F811
    from langboard_shared.tasks.docling import DocumentEmbeddingCache as module

    cache = CachedDocumentEmbeddings(Provider(), **config())
    cache.embed_documents(["alpha"])
    with DbSession.atomic() as db:
        db.exec(update(DocumentEmbeddingCacheReference).values(expires_at=SafeDateTime.now() - timedelta(seconds=1)))
    original = module.release_cache_references

    def publication_wins(token, **kwargs):
        retain(cache)
        original(token, **kwargs)

    monkeypatch.setattr(module, "release_cache_references", publication_wins)
    assert recover_expired_cache_references() == 1
    assert len(rows()) == 1 and rows(DocumentEmbeddingCacheReference)[0].expires_at is None


def test_cache_migration_is_additive_and_retains_live_references(monkeypatch):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from langboard_shared.core.db.DbEngine import DbEngine
    from sqlalchemy import create_engine, inspect, text

    path = Path(__file__).parents[2] / "langboard/migrations/versions/20261021060000-0b7a16ef037b.py"
    spec = importlib.util.spec_from_file_location("embedding_cache_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.down_revision == "f9c053de926a"
    engine = create_engine("sqlite://")
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE card_attachment(id BIGINT PRIMARY KEY, filename TEXT)"))
            connection.execute(text("INSERT INTO card_attachment VALUES(1,'original.pdf')"))
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
            assert connection.execute(text("SELECT filename FROM card_attachment")).scalar_one() == "original.pdf"
        with cached_document_embeddings(Provider(), **config()) as cache:
            cache.embed_documents(["alpha"])
            retain(cache)
        with engine.begin() as connection:
            with (
                Operations.context(MigrationContext.configure(connection)),
                pytest.raises(RuntimeError, match="Cannot discard"),
            ):
                migration.downgrade()
            actual = inspect(connection).get_indexes("document_embedding_cache")
            assert any(index["unique"] and index["column_names"] == ["cache_key"] for index in actual)
        release_cache_references(cache.token)
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
            assert "document_embedding_cache" not in inspect(connection).get_table_names()
            assert connection.execute(text("SELECT filename FROM card_attachment")).scalar_one() == "original.pdf"
    finally:
        engine.dispose()


@pytest.mark.parametrize("timeout", [True, 0, 31, float("nan"), float("inf"), "10"])
def test_invalid_timeouts_are_rejected_before_database_or_model_calls(timeout):
    with pytest.raises(ValueError, match="Embedding timeout"):
        CachedDocumentEmbeddings(Provider(), **config(timeout_seconds=timeout))


def test_retry_preserves_successful_batches_after_late_timeout(binding_store, monkeypatch):  # noqa: F811
    provider = Provider()
    batches = []

    def infer(values):
        assert DbSession._atomic_session.get() is None
        batches.append(list(values))
        if len(batches) == 3:
            raise TimeoutError("last response uncertain")
        return [[float(len(text)), 2.0, 3.0] for text in values]

    provider.embed_documents = infer
    texts = [f"chunk-{index}" for index in range(70)]
    with pytest.raises(EmbeddingInferenceUncertain) as failed:
        with cached_document_embeddings(provider, **config()) as original:
            original.embed_documents(texts)
    assert isinstance(failed.value.__cause__, TimeoutError)
    assert sum(row.vector is not None for row in rows()) == 64
    completed = batches[0] + batches[1]
    with cached_document_embeddings(provider, **config()) as retry:
        assert retry.embed_documents(completed) == [[float(len(text)), 2.0, 3.0] for text in completed]
        retain(retry)
        with monkeypatch.context() as clock:
            clock.setattr("time.monotonic", Mock(side_effect=[0, retry.inference_budget_seconds + 1]))
            with pytest.raises(EmbeddingInferenceUncertain):
                retry.embed_documents(texts)
    assert [len(batch) for batch in batches] == [32, 32, 6]
    assert len(rows(DocumentEmbeddingCacheReference)) == 140
    release_cache_references(original.token)
    assert len(rows()) == 70 and len(rows(DocumentEmbeddingCacheReference)) == 70
    with cached_document_embeddings(provider, **config()) as published:
        assert published.embed_documents(completed) == [[float(len(text)), 2.0, 3.0] for text in completed]
        retain(published)
    release_cache_references(retry.token)
    assert sum(row.vector is not None for row in rows()) == 64
    release_cache_references(published.token)
    assert len(rows()) == 6 and rows(DocumentEmbeddingCacheReference) == []
    assert all(row.vector is None and row.lease_expires_at is None and row.lease_token == original.token for row in rows())
    assert [len(batch) for batch in batches] == [32, 32, 6]


def test_cleanup_helpers_do_not_require_optional_inference_dependencies():
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import builtins
original = builtins.__import__
def base_only(name, *args, **kwargs):
    if name.startswith("langchain"):
        raise ImportError("optional inference dependencies unavailable")
    return original(name, *args, **kwargs)
builtins.__import__ = base_only
from langboard_shared.tasks.docling.DocumentEmbeddingCache import release_cache_references
release_cache_references(None)
from langboard_shared.tasks.docling import DocumentEmbeddingTask, DocumentCleanupTask
""",
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr


def test_async_workers_share_inflight_inference_without_blocking_loop(binding_store):  # noqa: F811
    provider = Provider()

    async def run():
        entered, finish = asyncio.Event(), asyncio.Event()

        async def infer(values):
            entered.set()
            await finish.wait()
            return Provider.embed_documents(provider, values)

        provider.aembed_documents = infer
        provider.embed_documents = Mock(side_effect=AssertionError("sync inference forbidden"))
        with cached_document_embeddings(provider, **config()) as first:
            with cached_document_embeddings(provider, **config()) as second:
                one = asyncio.create_task(first.aembed_documents(["same file"]))
                await asyncio.wait_for(entered.wait(), 1)
                two = asyncio.create_task(second.aembed_documents(["same file"]))
                await asyncio.sleep(0.02)
                assert not two.done()
                finish.set()
                results = await asyncio.wait_for(asyncio.gather(one, two), 2)
                assert results[0] == results[1]
                # Store callbacks must reuse warmed vectors without sync inference.
                assert first.embed_documents(["same file"]) == results[0]
                retain(first)
                retain(second)
        assert len(provider.calls) == 1
        provider.embed_documents.assert_not_called()
        release_cache_references(first.token)
        release_cache_references(second.token)
        assert rows() == []

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["timeout", "cancelled", "invalid-vector"])
def test_async_failure_keeps_finished_batches_and_fences_retry(binding_store, failure, monkeypatch):  # noqa: F811
    provider = Provider()
    batches = []

    async def infer(values):
        await asyncio.sleep(0)
        assert DbSession._atomic_session.get() is None
        batches.append(list(values))
        if len(batches) == 3:
            if failure == "cancelled":
                raise asyncio.CancelledError()
            if failure == "timeout":
                raise TimeoutError("uncertain response")
            return [[float("nan"), 2.0, 3.0] for _ in values]
        return [[float(len(text)), 2.0, 3.0] for text in values]

    provider.aembed_documents = infer
    provider.embed_documents = Mock(side_effect=AssertionError("sync inference forbidden"))
    texts = [f"chunk-{index}" for index in range(70)]

    async def run():
        error = {"timeout": TimeoutError, "cancelled": asyncio.CancelledError, "invalid-vector": ValueError}[failure]
        with pytest.raises(EmbeddingInferenceUncertain) as failed:
            with cached_document_embeddings(provider, **config()) as first:
                await first.aembed_documents(texts)
        assert isinstance(failed.value.__cause__, error)
        assert sum(row.vector is not None for row in rows()) == 64
        completed = batches[0] + batches[1]
        with cached_document_embeddings(provider, **config()) as retry:
            assert await retry.aembed_documents(completed) == [[float(len(text)), 2.0, 3.0] for text in completed]
            retain(retry)
            with monkeypatch.context() as clock:
                clock.setattr("time.monotonic", Mock(side_effect=[0, retry.inference_budget_seconds + 1]))
                with pytest.raises(EmbeddingInferenceUncertain):
                    await retry.aembed_documents(texts)
        assert [len(batch) for batch in batches] == [32, 32, 6]
        assert len(rows(DocumentEmbeddingCacheReference)) == 140
        assert all(row.lease_token == first.token and row.lease_expires_at is None for row in rows() if row.vector is None)
        provider.embed_documents.assert_not_called()
        release_cache_references(first.token)
        assert len(rows()) == 70 and len(rows(DocumentEmbeddingCacheReference)) == 70
        release_cache_references(retry.token)
        assert len(rows()) == 6 and rows(DocumentEmbeddingCacheReference) == []
        with cached_document_embeddings(provider, **config()) as later:
            with monkeypatch.context() as clock:
                clock.setattr("time.monotonic", Mock(side_effect=[0, later.inference_budget_seconds + 1]))
                with pytest.raises(EmbeddingInferenceUncertain):
                    await later.aembed_documents(batches[2])
        assert [len(batch) for batch in batches] == [32, 32, 6]
        release_cache_references(later.token)
        assert len(rows()) == 6

    asyncio.run(run())


def test_native_publication_rolls_back_when_staged_cache_references_expire(document_source):  # noqa: F811
    from langboard_shared.domain.models import CardMetadata
    from langboard_shared.tasks.docling.DocumentEmbeddingCache import EmbeddingCacheUnavailable

    metadata, card, attachment, document = document_source
    with cached_document_embeddings(Provider(), **config()) as cache:
        cache.embed_documents(["first", "second"])
        with DbSession.atomic() as db:
            first = db.exec(SqlBuilder.select.table(DocumentEmbeddingCacheReference)
                            .order_by(DocumentEmbeddingCacheReference.id)).first()
            first.expires_at = SafeDateTime.now() - timedelta(hours=9)
            db.update(first)
        recover_expired_cache_references()
        candidate = {"status": "indexed", "source_generation": document["generation"], "pointer": {
            "board_uid": card.project_id.to_short_code(), "card_uid": card.get_uid(),
            "attachment_uid": attachment.get_uid(), "content_hash": document["content_hash"],
            "chunk_ids": ["new-first", "new-second"],
            "storage": {"cache_use_token": cache.token, "cache_reference_count": 2},
        }}
        with pytest.raises(EmbeddingCacheUnavailable):
            metadata.publish_document_embedding(
                card, attachment.get_uid(), document["generation"], document["content_hash"],
                candidate, expected_embedding=document["embedding"],
            )
        saved = metadata.get_document_by_attachment_uid(CardMetadata, card, attachment.get_uid())
        assert saved["embedding"] == document["embedding"]
        assert all(ref.expires_at is not None for ref in rows(DocumentEmbeddingCacheReference))
