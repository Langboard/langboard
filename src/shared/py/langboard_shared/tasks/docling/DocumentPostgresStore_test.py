"""Real pgvector storage, without a model service or production database."""

import os
from uuid import uuid4
import pytest
from langchain_core.embeddings import Embeddings
from langboard_shared.tasks.docling.DocumentPostgresStore import open_postgres_vector_store


@pytest.fixture(autouse=True)
def isolated_postgres():
    from langboard_shared.Env import Env

    url = os.environ.get("LANGBOARD_VECTOR_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requires an explicitly disposable LANGBOARD_VECTOR_TEST_DATABASE_URL with pgvector")
    if Env.MAIN_DATABASE_URL != url:
        pytest.fail("Run in an isolated process with MAIN_DATABASE_URL matching the disposable vector test URL")
    return url


class Fixture(Embeddings):
    def embed_documents(self, texts):
        return [self.embed_query(text) for text in texts]

    def embed_query(self, text):
        return [float("alpha" in text), float("beta" in text), 0.1]


def test_real_postgres_store_persistence_filter_delete_and_missing_read_does_not_create_schema(isolated_postgres):
    from psycopg import connect
    from sqlalchemy.engine import make_url

    url = make_url(isolated_postgres).set(drivername="postgresql").render_as_string(hide_password=False)
    fingerprint = uuid4().hex * 2
    with open_postgres_vector_store(Fixture(), fingerprint, dimensions=3, create=False) as store:
        assert store is None
    with connect(url) as connection:
        assert connection.execute("SELECT to_regnamespace(%s)", ("lbv_" + fingerprint[:58],)).fetchone()[0] is None
    first, second = str(uuid4()), str(uuid4())
    with open_postgres_vector_store(Fixture(), fingerprint, dimensions=3) as store:
        assert store.add_texts(
            ["alpha 한글 日本語 中文", "alpha foreign board"],
            [{"board_uid": "one"}, {"board_uid": "other"}],
            ids=[first, second],
        ) == [first, second]
    with open_postgres_vector_store(Fixture(), fingerprint, dimensions=3, create=False) as store:
        hits = store.similarity_search_with_score("alpha", k=5, filter={"board_uid": "one"})
        assert len(hits) == 1 and hits[0][0].id == first
        assert "한글 日本語 中文" in hits[0][0].page_content
    with pytest.raises(ValueError, match="dimensions"):
        with open_postgres_vector_store(Fixture(), fingerprint, dimensions=4):
            pass
    with open_postgres_vector_store(None, fingerprint, dimensions=3, create=False) as store:
        store.delete([first])
        assert store.get_by_ids([first]) == []
        assert store.get_by_ids([second])[0].id == second


def test_facade_generation_search_and_deletion_keep_authoritative_source_scope(tmp_path):
    from langboard_shared.tasks.docling.DocumentRetrievalSettings import DocumentRetrievalSettings
    from langboard_shared.tasks.docling.DocumentSplitter import DocumentSplitterSettings
    from langboard_shared.tasks.docling.DocumentVectorQuery import search_vector_generation
    from langboard_shared.tasks.docling.DocumentVectorStore import (
        delete_vector_generation,
        open_document_vector_store,
        stage_vector_generation,
    )

    fingerprint = uuid4().hex * 2
    settings = DocumentRetrievalSettings(dimensions=3)
    assert settings.store == "postgres"
    source = dict(
        board_uid="board",
        card_uid="card",
        attachment_uid="file",
        content_hash="hash",
        embedding_fingerprint=fingerprint,
    )
    with open_document_vector_store(settings, Fixture(), fingerprint, tmp_path / "unused", set()) as store:
        pointer = stage_vector_generation(
            store,
            source=source,
            text="alpha 한글 日本語 中文",
            splitter=DocumentSplitterSettings(),
            storage={"type": "postgres", "dimensions": 3},
        )
        foreign = stage_vector_generation(
            store,
            source={**source, "board_uid": "other"},
            text="alpha foreign",
            splitter=DocumentSplitterSettings(),
            storage={"type": "postgres", "dimensions": 3},
        )
    assert not (tmp_path / "unused").exists()
    with open_document_vector_store(
        settings, Fixture(), fingerprint, tmp_path / "unused", set(), create=False
    ) as store:
        hits = search_vector_generation(store, pointer, "alpha", settings)
        assert len(hits) == 1 and hits[0]["source"]["board_uid"] == "board"
        assert "한글 日本語 中文" in hits[0]["content"]
        delete_vector_generation(store, pointer)
        assert search_vector_generation(store, pointer, "alpha", settings) == []
        assert len(search_vector_generation(store, foreign, "alpha", settings)) == 1


def test_native_cleanup_uses_recorded_postgres_without_resolving_embedding_credentials(tmp_path):
    from langboard_shared.tasks.docling.DocumentEmbeddingTask import remove_recorded_embedding
    from langboard_shared.tasks.docling.DocumentSplitter import DocumentSplitterSettings
    from langboard_shared.tasks.docling.DocumentVectorStore import stage_vector_generation

    fingerprint = uuid4().hex * 2
    source = dict(
        board_uid="board",
        card_uid="card",
        attachment_uid="file",
        content_hash="hash",
        embedding_fingerprint=fingerprint,
    )
    with open_postgres_vector_store(Fixture(), fingerprint, dimensions=3) as store:
        pointer = stage_vector_generation(
            store,
            source=source,
            text="alpha content",
            splitter=DocumentSplitterSettings(),
            storage={"type": "postgres", "dimensions": 3},
        )
    remove_recorded_embedding(pointer)
    with open_postgres_vector_store(None, fingerprint, dimensions=3, create=False) as store:
        assert store.get_by_ids(pointer["chunk_ids"]) == []


def test_different_embedding_dimensions_use_independent_persistent_model_schemas():
    class Four(Fixture):
        def embed_query(self, text):
            return [*super().embed_query(text), 0.2]

    first, second = uuid4().hex * 2, uuid4().hex * 2
    identifiers = [str(uuid4()), str(uuid4())]
    for fingerprint, embeddings, dimensions, identifier in [
        (first, Fixture(), 3, identifiers[0]),
        (second, Four(), 4, identifiers[1]),
    ]:
        with open_postgres_vector_store(embeddings, fingerprint, dimensions=dimensions) as store:
            store.add_texts(["alpha"], [{"board_uid": "one"}], ids=[identifier])
    with open_postgres_vector_store(Fixture(), first, dimensions=3, create=False) as store:
        assert len(store.get_by_ids(identifiers)) == 1
        assert store.get_by_ids(identifiers)[0].id == identifiers[0]
    with open_postgres_vector_store(Four(), second, dimensions=4, create=False) as store:
        assert len(store.get_by_ids(identifiers)) == 1
        assert store.get_by_ids(identifiers)[0].id == identifiers[1]
