"""Retain successful inference through downstream failure without sharing source data."""

import asyncio
import os
from contextlib import closing
from datetime import timedelta
from hashlib import sha256
from json import dumps
from unittest.mock import Mock
from uuid import uuid4
import httpx
import langchain_openai
import pytest
from sqlalchemy import create_engine, text, update
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import DocumentEmbeddingCache, DocumentEmbeddingCacheReference
from langboard_shared.tasks.docling.DocumentEmbedding import create_document_embeddings
from langboard_shared.tasks.docling.DocumentEmbeddingCache import (
    EmbeddingCacheUnavailable,
    EmbeddingInferenceUncertain,
    activate_cache_references,
    cached_document_embeddings,
    recover_expired_cache_references,
    release_cache_references,
)
from langboard_shared.tasks.docling.DocumentSplitter import DocumentSplitterSettings


@pytest.fixture(params=["sqlite://", "postgresql-test"])
def cache_store(monkeypatch, request):
    schema = None
    admin = None
    if request.param == "postgresql-test":
        url = os.environ.get("LANGBOARD_FILE_TEST_DATABASE_URL")
        if not url:
            pytest.skip("A disposable PostgreSQL fixture is required")
        admin = create_engine(url)
        assert admin.dialect.name == "postgresql"
        schema = "sdk_retry_" + uuid4().hex
        with admin.begin() as connection:
            connection.execute(text(f"CREATE SCHEMA {schema}"))
        engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    else:
        engine = create_engine("sqlite://")
    try:
        DocumentEmbeddingCache.__table__.create(engine)
        DocumentEmbeddingCacheReference.__table__.create(engine)
        monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
        yield
    finally:
        engine.dispose()
        if admin is not None:
            try:
                with admin.begin() as connection:
                    connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))
            finally:
                admin.dispose()


def settings(**changes):
    return dict(binding_uid="private-binding", content_hash=sha256(b"file").hexdigest(),
                extraction_identity=sha256(b"committed-extraction").hexdigest(),
                fingerprint=sha256(b"model").hexdigest(), dimensions=3, timeout_seconds=1, **changes)


def test_successful_inference_is_reused_after_vector_staging_failure(cache_store):
    from langchain_core.embeddings import Embeddings

    class Provider(Embeddings):
        calls = 0

        def embed_documents(self, texts):
            self.calls += 1
            assert DbSession._atomic_session.get() is None
            return [[1.0, 2.0, 3.0] for _ in texts]

        def embed_query(self, text):
            raise AssertionError("Query inference is unrelated to attachment retry")

    provider = Provider()
    with pytest.raises(RuntimeError, match="vector store unavailable"):
        with cached_document_embeddings(provider, **settings()) as first:
            first.embed_documents(["private document"])
            # The real staging boundary may fail after remote inference succeeds.
            raise RuntimeError("vector store unavailable")
    with cached_document_embeddings(provider, **settings()) as retry:
        assert retry.embed_documents(["private document"]) == [[1.0, 2.0, 3.0]]
        assert provider.calls == 1
        with DbSession.use(readonly=False) as db:
            references = db.exec(SqlBuilder.select.table(DocumentEmbeddingCacheReference)).all()
            assert all(ref.expires_at is not None for ref in references)
            assert "private document" not in str(references)
    with DbSession.atomic() as db:
        db.exec(update(DocumentEmbeddingCacheReference).values(
            expires_at=SafeDateTime.now() - timedelta(seconds=1)
        ))
    assert recover_expired_cache_references() == 1
    with DbSession.use(readonly=False) as db:
        assert db.exec(SqlBuilder.select.table(DocumentEmbeddingCache)).all() == []


def test_chunk_configuration_and_version_are_part_of_cache_identity(cache_store):
    from langchain_core.embeddings import Embeddings

    class Provider(Embeddings):
        def embed_documents(self, texts):
            return [[1.0, 2.0, 3.0] for _ in texts]

        def embed_query(self, text):
            raise AssertionError

    provider = Provider()
    provider.embed_documents = Mock(wraps=provider.embed_documents)
    for chunk_size, version in ((1000, "v1"), (2000, "v1"), (2000, "v2")):
        with cached_document_embeddings(
            provider, **settings(), splitter=DocumentSplitterSettings(chunk_size=chunk_size), chunk_version=version
        ) as cache:
            cache.embed_documents(["same text in different chunking configuration"])
            cache.retain()
    assert provider.embed_documents.call_count == 3


@pytest.mark.parametrize("use_async", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize("failure", ["response-loss", "server-error"])
def test_official_ambiguous_failure_never_repeats_inference(cache_store, monkeypatch, use_async, failure):
    async def no_delay(seconds):
        pass

    monkeypatch.setattr("time.sleep", lambda seconds: None)
    monkeypatch.setattr("anyio.sleep", no_delay)

    now = SafeDateTime.now()
    elapsed = [0.0]
    monkeypatch.setattr(SafeDateTime, "now", lambda: now + timedelta(seconds=elapsed[0]))
    requests = []
    constructor = langchain_openai.OpenAIEmbeddings
    value = dumps({
        "agent_llm": "OpenAI Compatible",
        "base_url": "https://fixture.invalid/v1",
        "model_name": "fixture-embedding",
        "retrieval": {"enabled": True, "timeout_seconds": 1},
    })

    def handle(request):
        assert DbSession._atomic_session.get() is None
        requests.append(request)
        if failure == "response-loss":
            # Provider accepted the body; the client never receives its result.
            raise httpx.ReadError("accepted response lost", request=request)
        return httpx.Response(503, headers={"retry-after": "120"}, json={
            "error": {"message": "unknown execution outcome", "type": "server_error"},
        })

    async def run():
        with httpx.Client(transport=httpx.MockTransport(handle)) as sync_client:
            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as async_client:
                monkeypatch.setattr(langchain_openai, "OpenAIEmbeddings", lambda **kwargs: constructor(
                    **kwargs, http_client=sync_client, http_async_client=async_client,
                ))
                provider = create_document_embeddings(value, {"https://fixture.invalid/v1"})
                with cached_document_embeddings(provider, **settings()) as first:
                    with pytest.raises(EmbeddingInferenceUncertain):
                        if use_async:
                            await first.aembed_documents(["same chunk"])
                        else:
                            first.embed_documents(["same chunk"])
                assert len(requests) == 1
                elapsed[0] += 9 * 60 * 60
                recover_expired_cache_references()
                with cached_document_embeddings(provider, **settings()) as second:
                    _, vector, owned = second._claim(second._key("same chunk"))
                    assert vector is None and not owned
                    elapsed_clock = [0.0]
                    monkeypatch.setattr("time.monotonic", lambda: elapsed_clock[0])
                    with closing(second._embedding_steps(["same chunk"])) as waiter:
                        assert next(waiter) == ("wait", 0.1)
                        elapsed_clock[0] += second.inference_budget_seconds + 1
                        with pytest.raises(EmbeddingInferenceUncertain, match="automatic resend disabled"):
                            next(waiter)
                assert len(requests) == 1

    asyncio.run(run())


def test_late_worker_keeps_ownership_and_publishes_without_duplicate(cache_store, monkeypatch):
    from langchain_core.embeddings import Embeddings

    now = SafeDateTime.now()
    elapsed = [0.0]
    monkeypatch.setattr(SafeDateTime, "now", lambda: now + timedelta(seconds=elapsed[0]))

    class Provider(Embeddings):
        def embed_documents(self, texts):
            raise AssertionError("Generator drives the controlled dispatch boundary")

        def embed_query(self, text):
            raise AssertionError

    with (
        cached_document_embeddings(Provider(), **settings()) as first,
        cached_document_embeddings(Provider(), **settings()) as second,
        closing(first._embedding_steps(["same chunk"])) as worker,
    ):
        assert next(worker) == ("embed", ["same chunk"])
        elapsed[0] += first.inference_budget_seconds + 1
        _, _, owned = second._claim(second._key("same chunk"))
        assert not owned
        with pytest.raises(StopIteration) as completed:
            worker.send([[1.0, 2.0, 3.0]])
        assert completed.value.value == [[1.0, 2.0, 3.0]]
        assert second.embed_documents(["same chunk"]) == [[1.0, 2.0, 3.0]]


def test_late_success_restores_expired_reference_before_publication_and_cleanup(cache_store):
    from langchain_core.embeddings import Embeddings

    class Provider(Embeddings):
        def embed_documents(self, texts):
            raise AssertionError("Controlled inference boundary; do not dispatch twice")

        def embed_query(self, text):
            raise AssertionError

    with cached_document_embeddings(Provider(), **settings()) as cache:
        with closing(cache._embedding_steps(["same chunk"])) as worker:
            assert next(worker) == ("embed", ["same chunk"])
            with DbSession.atomic() as db:
                db.exec(update(DocumentEmbeddingCacheReference).values(
                    expires_at=SafeDateTime.now() - timedelta(hours=9)
                ))
            assert recover_expired_cache_references() == 1
            with pytest.raises(StopIteration):
                worker.send([[1.0, 2.0, 3.0]])
        with DbSession.atomic() as db:
            refs = db.exec(SqlBuilder.select.table(DocumentEmbeddingCacheReference)).all()
            assert len(refs) == 1, "Late successful inference lost its source reference"
            assert refs[0].expires_at is not None
            activate_cache_references(db, cache.token, expected_count=1)
        cache.retain()
    with cached_document_embeddings(Provider(), **settings()) as duplicate:
        assert duplicate.embed_documents(["same chunk"]) == [[1.0, 2.0, 3.0]]
        with DbSession.atomic() as db:
            activate_cache_references(db, duplicate.token, expected_count=1)
        duplicate.retain()
    release_cache_references(cache.token)
    with DbSession.use(readonly=False) as db:
        assert len(db.exec(SqlBuilder.select.table(DocumentEmbeddingCache)).all()) == 1
    release_cache_references(duplicate.token)
    with DbSession.use(readonly=False) as db:
        assert db.exec(SqlBuilder.select.table(DocumentEmbeddingCache)).all() == []
        assert db.exec(SqlBuilder.select.table(DocumentEmbeddingCacheReference)).all() == []


def test_expired_staged_cache_cannot_be_published_without_references(cache_store):
    from langchain_core.embeddings import Embeddings

    class Provider(Embeddings):
        def embed_documents(self, texts):
            return [[1.0, 2.0, 3.0] for _ in texts]

        def embed_query(self, text):
            raise AssertionError

    with cached_document_embeddings(Provider(), **settings()) as cache:
        cache.embed_documents(["same chunk"])
        with DbSession.atomic() as db:
            db.exec(update(DocumentEmbeddingCacheReference).values(
                expires_at=SafeDateTime.now() - timedelta(hours=9)
            ))
        recover_expired_cache_references()
        with pytest.raises(EmbeddingCacheUnavailable):
            with DbSession.atomic() as db:
                activate_cache_references(db, cache.token)


def test_partial_reference_expiry_cannot_publish_a_complete_generation(cache_store):
    from langchain_core.embeddings import Embeddings

    class Provider(Embeddings):
        def embed_documents(self, texts):
            return [[1.0, 2.0, 3.0] for _ in texts]

        def embed_query(self, text):
            raise AssertionError

    with cached_document_embeddings(Provider(), **settings()) as cache:
        cache.embed_documents(["first", "first", "second"])
        with DbSession.atomic() as db:
            first = db.exec(SqlBuilder.select.table(DocumentEmbeddingCacheReference)
                            .order_by(DocumentEmbeddingCacheReference.id)).first()
            first.expires_at = SafeDateTime.now() - timedelta(hours=9)
            db.update(first)
        recover_expired_cache_references()
        with pytest.raises(EmbeddingCacheUnavailable):
            with DbSession.atomic() as db:
                activate_cache_references(db, cache.token, expected_count=2)
        with DbSession.use(readonly=False) as db:
            refs = db.exec(SqlBuilder.select.table(DocumentEmbeddingCacheReference)).all()
            assert len(refs) == 1 and refs[0].expires_at is not None


def test_official_rejection_releases_claim_but_undispatched_batches_remain_retryable(cache_store):
    from langchain_core.embeddings import Embeddings
    from openai import RateLimitError

    class Provider(Embeddings):
        def embed_documents(self, texts):
            raise AssertionError("Controlled dispatch boundary")

        def embed_query(self, text):
            raise AssertionError

    texts = [f"chunk {index}" for index in range(33)]
    with cached_document_embeddings(Provider(), **settings()) as first:
        worker = first._embedding_steps(texts)
        assert next(worker)[0] == "embed"
        response = httpx.Response(429, request=httpx.Request("POST", "https://fixture.invalid/v1/embeddings"))
        with pytest.raises(RateLimitError):
            worker.throw(RateLimitError("rejected before inference", response=response, body=None))
        assert not first.uncertain
        with cached_document_embeddings(Provider(), **settings()) as second:
            assert all(second._claim(second._key(value))[2] for value in texts)


def test_ambiguous_batch_releases_only_undispatched_claims(cache_store):
    from langchain_core.embeddings import Embeddings

    class Provider(Embeddings):
        def embed_documents(self, texts):
            raise AssertionError

        def embed_query(self, text):
            raise AssertionError

    texts = [f"chunk {index}" for index in range(33)]
    with cached_document_embeddings(Provider(), **settings()) as first:
        worker = first._embedding_steps(texts)
        _, dispatched = next(worker)
        with pytest.raises(EmbeddingInferenceUncertain):
            worker.throw(RuntimeError("response lost"))
        with cached_document_embeddings(Provider(), **settings()) as second:
            for value in texts:
                assert second._claim(second._key(value))[2] == (value not in dispatched)


def test_rejected_later_batch_preserves_successful_inference_for_retry(cache_store):
    from langchain_core.embeddings import Embeddings
    from openai import RateLimitError

    class Provider(Embeddings):
        def embed_documents(self, texts):
            raise AssertionError

        def embed_query(self, text):
            raise AssertionError

    texts = [f"chunk {index}" for index in range(33)]
    with cached_document_embeddings(Provider(), **settings()) as first:
        worker = first._embedding_steps(texts)
        _, completed = next(worker)
        _, rejected = worker.send([[1.0, 2.0, 3.0] for _ in completed])
        response = httpx.Response(429, request=httpx.Request("POST", "https://fixture.invalid/v1/embeddings"))
        with pytest.raises(RateLimitError):
            worker.throw(RateLimitError("rejected", response=response, body=None))
    with cached_document_embeddings(Provider(), **settings()) as second:
        for value in completed:
            _, vector, owned = second._claim(second._key(value))
            assert vector == [1.0, 2.0, 3.0] and not owned
        assert all(second._claim(second._key(value))[2] for value in rejected)
