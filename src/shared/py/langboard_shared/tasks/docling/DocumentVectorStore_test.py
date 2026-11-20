from unittest.mock import Mock
import pytest
from langchain_core.embeddings import Embeddings
from langchain_qdrant import QdrantVectorStore
from qdrant_client import QdrantClient, models
from langboard_shared.tasks.docling.DocumentRetrievalSettings import DocumentRetrievalSettings
from langboard_shared.tasks.docling.DocumentSplitter import DocumentSplitterSettings
from langboard_shared.tasks.docling.DocumentVectorStore import (
    delete_vector_generation,
    open_qdrant_store,
    stage_vector_generation,
)


class Fixture(Embeddings):
    def embed_documents(self, texts):
        return [self.embed_query(text) for text in texts]

    def embed_query(self, text):
        return [float("alpha" in text), float("beta" in text), 0.1]


@pytest.mark.parametrize("trusted", [False, True])
def test_official_qdrant_client_uses_native_ca_bundle(tmp_path, monkeypatch, trusted):
    """Real TLS transport, with only Qdrant's read-only wire responses simulated."""
    import json
    import shutil
    import ssl
    import subprocess
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    openssl = shutil.which("openssl")
    if not openssl:
        pytest.skip("OpenSSL is required to generate a disposable TLS certificate")
    cert, key = tmp_path / "certificate.pem", tmp_path / "key.pem"
    subprocess.run(
        [
            openssl,
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=IP:127.0.0.1",
            "-keyout",
            str(key),
            "-out",
            str(cert),
        ],
        check=True,
        capture_output=True,
        timeout=15,
    )
    paths = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            paths.append(self.path)
            result = (
                {"title": "qdrant", "version": "1.19.1", "commit": "fixture"}
                if self.path == "/"
                else {"result": {"exists": False}, "status": "ok", "time": 0.0}
            )
            body = json.dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"https://127.0.0.1:{server.server_port}"
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    if trusted:
        monkeypatch.setenv("SSL_CERT_FILE", str(cert))
    settings = DocumentRetrievalSettings(store="qdrant", external_url=endpoint, dimensions=3)
    try:
        if trusted:
            with open_qdrant_store(settings, None, "a" * 64, {endpoint}, create=False) as store:
                assert store is None
            assert any(path.endswith("/exists") for path in paths)
        else:
            with pytest.raises(Exception, match=r"(?i)certificate.verify.failed"):
                with open_qdrant_store(settings, None, "a" * 64, {endpoint}, create=False):
                    pass
            assert not paths
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_official_store_persists_sources_filters_and_deletes(tmp_path):
    client = QdrantClient(path=str(tmp_path / "vectors"))
    client.create_collection("documents", vectors_config=models.VectorParams(size=3, distance=models.Distance.COSINE))
    store = QdrantVectorStore(client, "documents", embedding=Fixture())
    source = dict(
        board_uid="board",
        card_uid="card",
        attachment_uid="attachment",
        content_hash="hash",
        embedding_fingerprint="a" * 64,
    )
    pointer = stage_vector_generation(
        store,
        source=source,
        text="alpha 한글 日本語 中文",
        splitter=DocumentSplitterSettings(),
        storage={"type": "qdrant"},
    )
    other = stage_vector_generation(
        store,
        source={**source, "board_uid": "other"},
        text="alpha private",
        splitter=DocumentSplitterSettings(),
        storage={"type": "qdrant"},
    )
    filter = models.Filter(
        must=[models.FieldCondition(key="metadata.board_uid", match=models.MatchValue(value="board"))]
    )
    hits = store.similarity_search("alpha", k=5, filter=filter)
    assert len(hits) == 1 and hits[0].metadata["content_hash"] == "hash"
    client.close()
    client = QdrantClient(path=str(tmp_path / "vectors"))
    store = QdrantVectorStore(client, "documents", embedding=Fixture())
    assert client.retrieve("documents", ids=pointer["chunk_ids"])
    delete_vector_generation(store, pointer)
    delete_vector_generation(store, pointer)
    assert not client.retrieve("documents", ids=pointer["chunk_ids"])
    assert client.retrieve("documents", ids=other["chunk_ids"])
    client.close()


def test_partial_failure_cleans_requested_ids():
    store = Mock()
    store.add_documents.side_effect = RuntimeError("fixture failure")
    source = dict(board_uid="b", card_uid="c", attachment_uid="a", content_hash="h", embedding_fingerprint="a" * 64)
    with pytest.raises(RuntimeError):
        stage_vector_generation(
            store, source=source, text="alpha", splitter=DocumentSplitterSettings(), storage={"type": "qdrant"}
        )
    assert store.delete.call_args.kwargs["ids"] == store.add_documents.call_args.kwargs["ids"]


def test_unapproved_endpoint_never_constructs_client(monkeypatch):
    client = Mock()
    monkeypatch.setattr("qdrant_client.QdrantClient", client)
    settings = DocumentRetrievalSettings(store="qdrant", external_url="https://fixture.invalid")
    with pytest.raises(ValueError, match="approved"):
        with open_qdrant_store(settings, Fixture(), "a" * 64, set()):
            pass
    client.assert_not_called()


def test_connection_factory_validates_collection_and_supports_provider_free_cleanup(monkeypatch, tmp_path):
    client = QdrantClient(path=str(tmp_path / "factory"))
    monkeypatch.setattr("qdrant_client.QdrantClient", lambda **_: client)
    fingerprint = "a" * 64
    settings = DocumentRetrievalSettings(store="qdrant", external_url="https://fixture.invalid", dimensions=3)
    source = dict(board_uid="b", card_uid="c", attachment_uid="a", content_hash="h", embedding_fingerprint=fingerprint)
    with open_qdrant_store(settings, Fixture(), fingerprint, {"https://fixture.invalid"}) as store:
        pointer = stage_vector_generation(
            store, source=source, text="alpha", splitter=DocumentSplitterSettings(), storage={"type": "qdrant"}
        )
    client = QdrantClient(path=str(tmp_path / "factory"))
    with open_qdrant_store(settings, None, fingerprint, {"https://fixture.invalid"}, create=False) as store:
        delete_vector_generation(store, pointer)
        assert not client.retrieve("langboard_documents_" + fingerprint, ids=pointer["chunk_ids"])
    client = QdrantClient(path=str(tmp_path / "factory"))
    settings.dimensions = 4
    with pytest.raises(ValueError, match="configuration"):
        with open_qdrant_store(settings, None, fingerprint, {"https://fixture.invalid"}, create=False):
            pass


@pytest.mark.parametrize("vector", [[1.0], [float("nan"), 0.0, 0.0], [True, 0.0, 0.0]])
def test_external_store_rejects_invalid_provider_vectors_before_write(monkeypatch, tmp_path, vector):
    client = QdrantClient(path=str(tmp_path / "invalid"))
    monkeypatch.setattr("qdrant_client.QdrantClient", lambda **_: client)
    settings = DocumentRetrievalSettings(store="qdrant", external_url="https://fixture.invalid", dimensions=3)
    provider = Fixture()
    provider.embed_documents = lambda texts: [vector for _ in texts]
    with open_qdrant_store(settings, provider, "a" * 64, {"https://fixture.invalid"}) as store:
        with pytest.raises(ValueError, match="dimension"):
            stage_vector_generation(
                store,
                source=dict(
                    board_uid="b", card_uid="c", attachment_uid="a", content_hash="h", embedding_fingerprint="a" * 64
                ),
                text="alpha",
                splitter=DocumentSplitterSettings(),
                storage={"type": "qdrant"},
            )
        assert client.count("langboard_documents_" + "a" * 64).count == 0


def test_common_factory_read_missing_sqlite_does_not_create_files(tmp_path):
    from langboard_shared.tasks.docling.DocumentVectorStore import open_document_vector_store

    directory = tmp_path / "absent"
    with open_document_vector_store(
        DocumentRetrievalSettings(store="sqlite", dimensions=3), Fixture(), "a" * 64, directory, set(), create=False
    ) as store:
        assert store is None
    assert not directory.exists()


def test_common_factory_external_store_does_not_create_local_directory(monkeypatch, tmp_path):
    from contextlib import nullcontext
    from langboard_shared.tasks.docling import DocumentVectorStore as module

    sentinel = object()
    connect = Mock(return_value=nullcontext(sentinel))
    monkeypatch.setattr(module, "open_qdrant_store", connect)
    directory = tmp_path / "absent"
    settings = DocumentRetrievalSettings(store="qdrant", external_url="https://fixture.invalid")
    with module.open_document_vector_store(
        settings, Fixture(), "a" * 64, directory, {"https://fixture.invalid"}, create=False
    ) as store:
        assert store is sentinel
    assert connect.call_args.kwargs["create"] is False
    assert not directory.exists()


def test_instance_collections_preserve_independent_search_and_deletion(monkeypatch, tmp_path):
    directory = str(tmp_path / "shared-server")
    monkeypatch.setattr("qdrant_client.QdrantClient", lambda **_: QdrantClient(path=directory))
    first = DocumentRetrievalSettings(
        store="qdrant", external_url="https://fixture.invalid", dimensions=3, collection_prefix="first_documents"
    )
    second = first.model_copy(update={"collection_prefix": "second_documents"})
    fingerprint = "a" * 64
    source = dict(board_uid="b", card_uid="c", attachment_uid="a", content_hash="h", embedding_fingerprint=fingerprint)
    pointers = []
    for settings, text in ((first, "alpha first"), (second, "alpha second")):
        with open_qdrant_store(settings, Fixture(), fingerprint, {"https://fixture.invalid"}) as store:
            pointers.append(
                stage_vector_generation(
                    store, source=source, text=text, splitter=DocumentSplitterSettings(), storage={"type": "qdrant"}
                )
            )
            assert [doc.page_content for doc in store.similarity_search("alpha")] == [text]
    with open_qdrant_store(first, None, fingerprint, {"https://fixture.invalid"}, create=False) as store:
        delete_vector_generation(store, pointers[0])
    with open_qdrant_store(second, Fixture(), fingerprint, {"https://fixture.invalid"}, create=False) as store:
        assert [doc.page_content for doc in store.similarity_search("alpha")] == ["alpha second"]


@pytest.mark.parametrize("recorded_prefix", [None, "original_documents"])
def test_cleanup_uses_recorded_collection_after_configuration_change(monkeypatch, tmp_path, recorded_prefix):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from langboard_shared.domain.models.InternalBot import InternalBotType
    from langboard_shared.tasks.docling import DocumentEmbeddingTask as task

    directory = str(tmp_path / "cleanup-server")
    monkeypatch.setattr("qdrant_client.QdrantClient", lambda **_: QdrantClient(path=directory))
    original = DocumentRetrievalSettings(
        store="qdrant",
        external_url="https://fixture.invalid",
        dimensions=3,
        **({"collection_prefix": recorded_prefix} if recorded_prefix else {}),
    )
    current = original.model_copy(update={"collection_prefix": "new_documents"})
    fingerprint = "a" * 64
    source = dict(board_uid="b", card_uid="c", attachment_uid="a", content_hash="h", embedding_fingerprint=fingerprint)
    storage = {"type": "qdrant", "endpoint": "https://fixture.invalid", "binding_uid": "binding", "dimensions": 3}
    if recorded_prefix:
        storage["collection_prefix"] = recorded_prefix
    with open_qdrant_store(original, Fixture(), fingerprint, {"https://fixture.invalid"}) as store:
        pointer = stage_vector_generation(
            store, source=source, text="alpha old", splitter=DocumentSplitterSettings(), storage=storage
        )
    with open_qdrant_store(current, Fixture(), fingerprint, {"https://fixture.invalid"}) as store:
        stage_vector_generation(
            store, source=source, text="alpha current", splitter=DocumentSplitterSettings(), storage=storage
        )
    service = SimpleNamespace(
        internal_bot=SimpleNamespace(
            get_current_by_id_like=lambda _: SimpleNamespace(bot_type=InternalBotType.DocumentEmbedding, value="current")
        ),
        close=Mock(),
    )
    monkeypatch.setattr(task, "DomainService", lambda: service)
    monkeypatch.setattr(task, "validate_embedding_config", lambda _: ({}, current))
    monkeypatch.setattr(task, "Env", SimpleNamespace(get_from_env=lambda *_: "https://fixture.invalid"))
    task.remove_recorded_embedding(pointer)
    service.close.assert_called_once()
    with open_qdrant_store(original, Fixture(), fingerprint, {"https://fixture.invalid"}, create=False) as store:
        assert store.similarity_search("alpha") == []
    with open_qdrant_store(current, Fixture(), fingerprint, {"https://fixture.invalid"}, create=False) as store:
        assert [doc.page_content for doc in store.similarity_search("alpha")] == ["alpha current"]


@pytest.mark.parametrize("backend", ["sqlite", "qdrant"])
def test_native_project_generation_stage_query_and_cleanup(tmp_path, backend):
    from langboard_shared.tasks.docling.DocumentSource_test import project_source
    from langboard_shared.tasks.docling.DocumentSqliteStore import open_sqlite_vector_store
    from langboard_shared.tasks.docling.DocumentVectorQuery import search_vector_generation

    client = None
    if backend == "qdrant":
        client = QdrantClient(path=str(tmp_path / "project-vectors"))
        client.create_collection("documents", vectors_config=models.VectorParams(size=3, distance=models.Distance.COSINE))
        store = QdrantVectorStore(client, "documents", embedding=Fixture())
        from contextlib import nullcontext
        context = nullcontext(store)
    else:
        context = open_sqlite_vector_store(tmp_path / "project.sqlite", Fixture(), dimensions=3)
    with context as store:
        source = project_source()
        pointer = stage_vector_generation(store, source=source, text="alpha project", splitter=DocumentSplitterSettings(),
                                          storage={"type": backend})
        assert pointer.items() >= source.items()
        assert "card_uid" not in pointer and "wiki_uid" not in pointer
        hits = search_vector_generation(store, pointer, "alpha", DocumentRetrievalSettings(dimensions=3))
        assert len(hits) == 1 and hits[0]["source"] == {**source, "generation": pointer["generation"]}
        delete_vector_generation(store, pointer)
        assert search_vector_generation(store, pointer, "alpha", DocumentRetrievalSettings(dimensions=3)) == []
    if client:
        client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("update", [{"source_type": "unknown"}, {"source_type": None}, {"card_uid": None},
                                    {"card_uid": "card"}, {"wiki_uid": None}, {"board_uid": ""}])
async def test_invalid_project_source_rejected_before_inference_or_write(update):
    from unittest.mock import AsyncMock
    from langboard_shared.tasks.docling.DocumentSource_test import project_source
    from langboard_shared.tasks.docling.DocumentVectorStore import astage_vector_generation

    store, embeddings = Mock(), Mock()
    embeddings.aembed_documents = AsyncMock()
    kwargs = dict(source={**project_source(), **update}, text="alpha", splitter=DocumentSplitterSettings(),
                  storage={"type": "sqlite"})
    with pytest.raises(ValueError):
        stage_vector_generation(store, **kwargs)
    with pytest.raises(ValueError):
        await astage_vector_generation(store, embeddings=embeddings, **kwargs)
    embeddings.aembed_documents.assert_not_called()
    assert not store.mock_calls
