"""Retain successful inference through downstream failure without sharing source data."""

from datetime import timedelta
from hashlib import sha256
from unittest.mock import Mock
import pytest
from sqlalchemy import create_engine, update
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import DocumentEmbeddingCache, DocumentEmbeddingCacheReference
from langboard_shared.tasks.docling.DocumentEmbeddingCache import (
    cached_document_embeddings,
    recover_expired_cache_references,
)
from langboard_shared.tasks.docling.DocumentSplitter import DocumentSplitterSettings


@pytest.fixture
def cache_store(monkeypatch):
    engine = create_engine("sqlite://")
    DocumentEmbeddingCache.__table__.create(engine)
    DocumentEmbeddingCacheReference.__table__.create(engine)
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    yield
    engine.dispose()


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
