"""Opt-in REST acceptance against a disposable, authenticated Qdrant server."""

import os
from uuid import uuid4
import httpx
import pytest
from langchain_core.embeddings import Embeddings
from qdrant_client import QdrantClient
from langboard_shared.tasks.docling.DocumentRetrievalSettings import DocumentRetrievalSettings
from langboard_shared.tasks.docling.DocumentSplitter import DocumentSplitterSettings
from langboard_shared.tasks.docling.DocumentVectorQuery import search_vector_generation
from langboard_shared.tasks.docling.DocumentVectorStore import (
    delete_vector_generation,
    open_qdrant_store,
    stage_vector_generation,
)


class DeterministicEmbeddings(Embeddings):
    """No model service, inference runtime or paid embedding calls."""

    def embed_documents(self, texts):
        return [self.embed_query(text) for text in texts]

    def embed_query(self, text):
        return [float("alpha" in text), float("beta" in text), 0.1]


def test_external_qdrant_authentication_persistence_and_source_fences():
    endpoint = os.environ.get("LANGBOARD_TEST_QDRANT_URL", "").rstrip("/")
    key = os.environ.get("LANGBOARD_TEST_QDRANT_API_KEY", "")
    if not endpoint or not key:
        pytest.skip("Set LANGBOARD_TEST_QDRANT_URL and LANGBOARD_TEST_QDRANT_API_KEY for disposable-server acceptance")
    assert endpoint.startswith("https://"), "Disposable-server acceptance requires verified HTTPS"

    # Require authenticated transport; an accidentally unsecured test server fails.
    with httpx.Client(timeout=5) as transport:
        for headers in ({}, {"api-key": uuid4().hex}):
            assert transport.get(endpoint + "/collections", headers=headers).status_code in (401, 403)

    prefix = "langboard_acceptance_" + uuid4().hex
    first = DocumentRetrievalSettings(
        store="qdrant", external_url=endpoint, external_api_key=key, dimensions=3, collection_prefix=prefix
    )
    second = first.model_copy(update={"collection_prefix": prefix + "_other"})
    fingerprint = "a" * 64
    source = dict(
        board_uid="board",
        card_uid="card",
        attachment_uid="attachment",
        content_hash="hash",
        embedding_fingerprint=fingerprint,
    )
    admin = QdrantClient(url=endpoint, api_key=key, timeout=5)
    pointers = []
    try:
        for settings, sources in (
            (
                first,
                [
                    (source, "alpha 한글 日本語 中文"),
                    (source, "alpha old generation"),
                    ({**source, "board_uid": "other"}, "alpha other board"),
                ],
            ),
            (second, [(source, "alpha other installation")]),
        ):
            with open_qdrant_store(settings, DeterministicEmbeddings(), fingerprint, {endpoint}) as store:
                for metadata, text in sources:
                    pointers.append(
                        stage_vector_generation(
                            store,
                            source=metadata,
                            text=text,
                            splitter=DocumentSplitterSettings(),
                            storage={"type": "qdrant", "collection_prefix": settings.collection_prefix},
                        )
                    )

        # New transport clients must retrieve persisted points and exact sources.
        with open_qdrant_store(first, DeterministicEmbeddings(), fingerprint, {endpoint}, create=False) as store:
            for search_type in ("similarity", "mmr"):
                settings = first.model_copy(update={"search_type": search_type})
                hits = search_vector_generation(store, pointers[0], "alpha", settings)
                assert [hit["content"] for hit in hits] == ["alpha 한글 日本語 中文"]
                assert hits[0]["chunk_id"] in pointers[0]["chunk_ids"]

        with pytest.raises(ValueError, match="configuration"):
            with open_qdrant_store(first.model_copy(update={"dimensions": 4}), None, fingerprint, {endpoint}):
                pass
        with pytest.raises(ValueError, match="approved"):
            with open_qdrant_store(first, None, fingerprint, set()):
                pass

        with open_qdrant_store(first, None, fingerprint, {endpoint}, create=False) as store:
            delete_vector_generation(store, pointers[0])
            delete_vector_generation(store, pointers[0])
        with open_qdrant_store(first, DeterministicEmbeddings(), fingerprint, {endpoint}, create=False) as store:
            assert search_vector_generation(store, pointers[0], "alpha", first) == []
            assert [hit["content"] for hit in search_vector_generation(store, pointers[1], "alpha", first)] == [
                "alpha old generation"
            ]
            assert [hit["content"] for hit in search_vector_generation(store, pointers[2], "alpha", first)] == [
                "alpha other board"
            ]
        with open_qdrant_store(second, DeterministicEmbeddings(), fingerprint, {endpoint}, create=False) as store:
            assert [hit["content"] for hit in search_vector_generation(store, pointers[3], "alpha", second)] == [
                "alpha other installation"
            ]
    finally:
        # Only this invocation's random namespaces are eligible for cleanup.
        try:
            for settings in (first, second):
                collection = settings.collection_prefix + "_" + fingerprint
                if admin.collection_exists(collection):
                    admin.delete_collection(collection)
        finally:
            admin.close()
