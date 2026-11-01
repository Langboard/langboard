"""Official LangChain VectorStore integration; source pointers stay in Langboard."""

import re
from contextlib import contextmanager
from typing import TYPE_CHECKING
from uuid import uuid4
from .DocumentSource import document_source_identity
from .DocumentSplitter import split_document, split_structural_document
from .DocumentVectorGeneration import MAX_ATTACHMENT_CHUNKS


if TYPE_CHECKING:
    from langchain_core.vectorstores import VectorStore
    from .DocumentRetrievalSettings import DocumentRetrievalSettings


class VectorGenerationCleanupError(RuntimeError):
    """Retain unpublished IDs for durable cleanup without exposing provider errors."""

    def __init__(self, pointer: dict):
        super().__init__("Vector generation cleanup must be retried")
        self.pointer = pointer


@contextmanager
def open_qdrant_store(
    settings: "DocumentRetrievalSettings", embeddings, fingerprint: str, allowed_urls: set[str], *, create=True
):
    """Connect only to an explicitly approved endpoint, using the official integration."""
    from langchain_qdrant import QdrantVectorStore
    from qdrant_client import QdrantClient, models
    from .DocumentEmbedding import validated_embeddings

    endpoint = str(settings.external_url).rstrip("/")
    if endpoint not in {url.strip().rstrip("/") for url in allowed_urls if url.strip()}:
        raise ValueError("Vector endpoint must be approved in DOCUMENT_VECTOR_ALLOWED_BASE_URLS")
    if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise ValueError("Invalid embedding fingerprint")
    client = QdrantClient(
        url=endpoint,
        api_key=settings.external_api_key.get_secret_value() if settings.external_api_key else None,
        timeout=settings.timeout_seconds,
    )
    try:
        collection = settings.collection_prefix + "_" + fingerprint
        if not client.collection_exists(collection):
            if not create:
                yield None
                return
            try:
                client.create_collection(
                    collection,
                    vectors_config=models.VectorParams(size=settings.dimensions, distance=models.Distance.COSINE),
                )
            except Exception:
                # Another worker may have created it. Still validate its dimensions below.
                if not client.collection_exists(collection):
                    raise
        vectors = client.get_collection(collection).config.params.vectors
        if (
            not isinstance(vectors, models.VectorParams)
            or vectors.size != settings.dimensions
            or vectors.distance != models.Distance.COSINE
        ):
            raise ValueError("Vector collection does not match the recorded embedding configuration")
        yield QdrantVectorStore(
            client=client,
            collection_name=collection,
            embedding=validated_embeddings(embeddings, settings.dimensions) if embeddings is not None else None,
            validate_embeddings=embeddings is not None,
            validate_collection_config=False,
        )
    finally:
        client.close()


@contextmanager
def open_document_vector_store(
    settings, embeddings, fingerprint: str, directory, allowed_urls: set[str], *, create=True
):
    """Keep backend connection details outside MCP and document processing."""
    from .DocumentSqliteStore import open_sqlite_vector_store

    if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise ValueError("Invalid embedding fingerprint")
    if settings.store == "sqlite":
        path = directory / (fingerprint + ".sqlite")
        if create:
            directory.mkdir(parents=True, exist_ok=True)
        elif not path.is_file():
            yield None
            return
        context = open_sqlite_vector_store(
            path, embeddings, dimensions=settings.dimensions, timeout_seconds=settings.timeout_seconds
        )
    elif settings.store == "postgres":
        from .DocumentPostgresStore import open_postgres_vector_store

        context = open_postgres_vector_store(
            embeddings, fingerprint, dimensions=settings.dimensions, timeout_seconds=settings.timeout_seconds, create=create
        )
    elif settings.store == "qdrant":
        context = open_qdrant_store(settings, embeddings, fingerprint, allowed_urls, create=create)
    else:
        raise ValueError("Unsupported vector storage")
    with context as store:
        yield store


def document_source_filter(storage: dict, source: dict):
    """Translate exact source constraints to the official backend filter format."""
    if storage.get("type") in {"sqlite", "postgres"}:
        return source
    if storage.get("type") != "qdrant":
        raise ValueError("Unsupported vector storage")
    from qdrant_client import models

    return models.Filter(
        must=[
            models.FieldCondition(key="metadata." + key, match=models.MatchValue(value=value))
            for key, value in source.items()
        ]
    )


def stage_vector_generation(
    store: "VectorStore", *, source: dict, text: str, splitter, storage: dict, document_json: dict | None = None
) -> dict:
    """No active pointer changes until the caller's authoritative source fence commits."""
    chunks, pointer = _prepare_vector_generation(
        source=source, text=text, splitter=splitter, storage=storage, document_json=document_json
    )
    return _write_vector_generation(store, chunks, pointer)


async def astage_vector_generation(
    store: "VectorStore", *, embeddings, source: dict, text: str, splitter, storage: dict,
    document_json: dict | None = None,
) -> dict:
    """Await native provider inference; the store consumes already validated cached vectors."""
    chunks, pointer = _prepare_vector_generation(
        source=source, text=text, splitter=splitter, storage=storage, document_json=document_json
    )
    await embeddings.aembed_documents([chunk.page_content for chunk in chunks])
    if storage.get("cache_use_token") is not None:
        storage["cache_reference_count"] = len({chunk.page_content for chunk in chunks})
    return _write_vector_generation(store, chunks, pointer)


def _prepare_vector_generation(*, source, text, splitter, storage, document_json):
    document_source_identity(source)
    if not text.strip():
        raise ValueError("An empty transcription cannot replace a searchable generation")
    generation = uuid4().hex
    metadata = {**source, "generation": generation}
    chunks = (
        split_structural_document(document_json, splitter, metadata=metadata)
        if document_json is not None
        else split_document(text, splitter, metadata=metadata)
    )
    if not 1 <= len(chunks) <= MAX_ATTACHMENT_CHUNKS:
        raise ValueError("Attachment exceeds the embedding chunk limit")
    ids = [str(uuid4()) for _ in chunks]
    pointer = {**source, "generation": generation, "chunk_count": len(chunks), "chunk_ids": ids, "storage": storage}
    return chunks, pointer


def _write_vector_generation(store, chunks, pointer):
    ids = pointer["chunk_ids"]
    try:
        returned = store.add_documents(chunks, ids=ids)
        if returned != ids:
            raise ValueError("Vector store did not preserve the requested chunk identifiers")
    except Exception:
        delete_vector_generation(store, pointer)
        raise
    return pointer


def delete_vector_generation(store: "VectorStore", pointer: dict) -> None:
    ids = pointer.get("chunk_ids")
    if (
        not isinstance(ids, list)
        or not 1 <= len(ids) <= MAX_ATTACHMENT_CHUNKS
        or not all(isinstance(i, str) and i for i in ids)
    ):
        raise ValueError("Invalid recorded chunk identifiers")
    try:
        if store.delete(ids=ids) is False:
            raise RuntimeError("Vector store did not complete deletion")
    except Exception:
        raise VectorGenerationCleanupError(pointer) from None
