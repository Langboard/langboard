"""Retain successful inference through downstream failure without sharing source data."""

import asyncio
import os
import time
from contextlib import closing
from datetime import timedelta
from hashlib import sha256
from json import dumps
from unittest.mock import Mock
from uuid import uuid4
import anyio
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
    cached_document_embeddings,
    recover_expired_cache_references,
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
def test_official_retry_after_preserves_ownership_and_waiters(cache_store, monkeypatch, use_async):
    now = SafeDateTime.now()
    elapsed = [0.0]
    monkeypatch.setattr(SafeDateTime, "now", lambda: now + timedelta(seconds=elapsed[0]))
    monkeypatch.setattr(time, "monotonic", lambda: elapsed[0])
    requests, delays = [], []
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
        elapsed[0] += 1  # Both HTTP attempts consume their configured timeout budget.
        if len(requests) == 1:
            return httpx.Response(503, headers={"retry-after": "120"}, json={
                "error": {"message": "retry fixture", "type": "server_error"},
            })
        return httpx.Response(200, json={
            "object": "list", "model": "fixture-embedding",
            "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2, 0.3]}],
            "usage": {"prompt_tokens": 1, "total_tokens": 1},
        })

    async def run():
        with httpx.Client(transport=httpx.MockTransport(handle)) as sync_client:
            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as async_client:
                monkeypatch.setattr(langchain_openai, "OpenAIEmbeddings", lambda **kwargs: constructor(
                    **kwargs, http_client=sync_client, http_async_client=async_client,
                ))
                provider = create_document_embeddings(value, {"https://fixture.invalid/v1"})
                assert provider.max_retries == 1
                with (
                    cached_document_embeddings(provider, **settings()) as first,
                    cached_document_embeddings(provider, **settings()) as second,
                    closing(second._embedding_steps(["same chunk"])) as waiter,
                ):
                    def advance_delay(delay):
                        delays.append(delay)
                        assert next(waiter) == ("wait", 0.1)
                        elapsed[0] += delay
                        _, _, owned = second._claim(second._key("same chunk"))
                        assert not owned, "Supported SDK retry must retain the first uploader's lease"
                        assert next(waiter) == ("wait", 0.1)

                    async def async_delay(delay):
                        advance_delay(delay)

                    # Only replace clocks and sleeps; the official SDK parses and retries the response.
                    monkeypatch.setattr(time, "sleep", advance_delay)
                    monkeypatch.setattr(anyio, "sleep", async_delay)
                    expected = [[0.1, 0.2, 0.3]]
                    result = (
                        await first.aembed_documents(["same chunk"])
                        if use_async else first.embed_documents(["same chunk"])
                    )
                    assert result == expected
                    with pytest.raises(StopIteration) as completed:
                        next(waiter)
                    assert completed.value.value == expected
                    assert second.embed_documents(["same chunk"]) == expected

    asyncio.run(run())
    assert len(requests) == 2 and delays == [120.0]
