"""Embed only a queued, current transcription snapshot; never scan existing attachments."""

from json import loads
from sqlite3 import OperationalError
from ...core.broker import Broker
from ...core.routing import SocketTopic
from ...domain.models import CardMetadata
from ...domain.models.InternalBot import InternalBotType
from ...domain.services import DomainService
from ...Env import Env
from .DocumentEmbedding import create_document_embeddings, resolve_embedding_snapshot, validate_embedding_config
from .DocumentEmbeddingCache import (
    CHUNK_VERSION,
    EmbeddingInferenceUncertain,
    cached_document_embeddings,
    release_cache_references,
)
from .DocumentExtractionCache import PublishedExtractionUnavailable, published_extraction_identity
from .DocumentRetrievalSettings import DEFAULT_COLLECTION_PREFIX, DocumentRetrievalSettings
from .DocumentSqliteStore import remove_document_generation
from .DocumentVectorGeneration import embedding_fingerprint
from .DocumentVectorStore import (
    VectorGenerationCleanupError,
    delete_vector_generation,
    open_document_vector_store,
    open_qdrant_store,
)
from .DocumentVectorStore import (
    astage_vector_generation as stage_vector_generation,
)


@Broker.wrap_async_task_decorator
async def index_transcribed_attachment(request: str):
    service = DomainService()
    try:
        payload = loads(request)
        if isinstance(payload, dict):
            await embed_transcription(
                service, payload.get("attachment_uid"), payload.get("generation"), payload.get("request_uid")
            )
    finally:
        service.close()


@Broker.wrap_async_task_decorator({"autoretry_for": (OperationalError,), "retry_backoff": True, "max_retries": 3})
async def remove_attachment_embedding(request: str):
    """Only the committed attachment lifecycle supplies this server-owned pointer."""
    remove_recorded_embedding(loads(request))


def remove_recorded_embedding(pointer: dict) -> None:
    """Release shared results only after this attachment generation is removed."""
    _remove_recorded_vectors(pointer)
    release_cache_references((pointer.get("storage") or {}).get("cache_use_token"))


def _remove_recorded_vectors(pointer: dict) -> None:
    """Delete the recorded namespace even after current indexing settings change."""
    if not isinstance(pointer, dict):
        raise ValueError("A recorded embedding pointer is required")
    storage = pointer.get("storage") or {}
    if storage.get("type", "sqlite") == "sqlite":
        remove_document_generation(Env.DATA_DIR / "document-retrieval", pointer)
        return
    if storage.get("type") == "postgres":
        from .DocumentPostgresStore import open_postgres_vector_store

        with open_postgres_vector_store(
            None, pointer["embedding_fingerprint"], dimensions=storage["dimensions"], create=False
        ) as store:
            if store is not None:
                delete_vector_generation(store, pointer)
        return
    if storage.get("type") != "qdrant":
        raise ValueError("Unsupported recorded vector storage")
    service = DomainService()
    try:
        binding = service.internal_bot.get_current_by_id_like(storage.get("binding_uid"))
        if not binding or binding.bot_type != InternalBotType.DocumentEmbedding:
            raise ValueError("Recorded vector binding is unavailable")
        _, current = validate_embedding_config(binding.value)
        if str(current.external_url).rstrip("/") != storage.get("endpoint"):
            raise ValueError("Recorded vector endpoint changed; cleanup requires the original connection")
        settings = DocumentRetrievalSettings(
            store="qdrant",
            external_url=current.external_url,
            external_api_key=current.external_api_key,
            dimensions=storage["dimensions"],
            collection_prefix=storage.get("collection_prefix", DEFAULT_COLLECTION_PREFIX),
        )
        allowed = set(Env.get_from_env("DOCUMENT_VECTOR_ALLOWED_BASE_URLS", "").split(","))
        with open_qdrant_store(settings, None, pointer["embedding_fingerprint"], allowed, create=False) as store:
            if store is not None:
                delete_vector_generation(store, pointer)
    finally:
        service.close()


async def embed_transcription(service, attachment_uid: str, generation: str, request_uid: str | None = None) -> None:
    if not isinstance(attachment_uid, str) or not isinstance(generation, str):
        return
    attachment = service.card_attachment.get_by_id_like(attachment_uid)
    if not attachment or attachment.deleted_at is not None:
        return
    card = service.card.get_by_id_like(attachment.card_id)
    if not card or card.is_linked_resource:
        return
    document = service.docling_metadata.get_document_by_attachment_uid(CardMetadata, card, attachment_uid)
    if not document or document.get("generation") != generation or document.get("status") != "indexed":
        return
    project = service.project.get_by_id_like(card.project_id)
    if not project or project.deleted_at is not None:
        return
    snapshot = document.get("embedding_config")
    if not isinstance(snapshot, dict):
        return
    binding = service.internal_bot.get_current_by_id_like(snapshot.get("binding_uid"))
    content_hash = document.get("content_hash")
    old = document.get("embedding") or {}
    if request_uid is not None and old.get("request_uid") != request_uid:
        return
    try:
        if not binding or binding.bot_type != InternalBotType.DocumentEmbedding:
            raise ValueError("Embedding binding is unavailable")
        private = resolve_embedding_snapshot(snapshot, binding.value)
        config, settings = validate_embedding_config(private)
        allowed = set(Env.get_from_env("MODEL_PROVIDER_ALLOWED_BASE_URLS", "").split(","))
        embeddings = create_document_embeddings(private, allowed)
        fingerprint = embedding_fingerprint(
            provider=config["base_url"], model=config["model_name"], dimensions=settings.dimensions, version="v1"
        )
        if (
            old.get("status") == "indexed"
            and old.get("chunk_version", "v1") == CHUNK_VERSION
            and old.get("source_generation") == generation
            and isinstance(old.get("config"), dict)
            and old["config"].get("retrieval", {}).get("splitter") == snapshot.get("retrieval", {}).get("splitter")
            and (old.get("pointer") or {}).get("embedding_fingerprint") == fingerprint
            and ((old.get("pointer") or {}).get("storage") or {}).get("type", "sqlite") == settings.store
            and (
                settings.store in {"sqlite", "postgres"}
                or (
                    ((old.get("pointer") or {}).get("storage") or {}).get("endpoint")
                    == str(settings.external_url).rstrip("/")
                    and ((old.get("pointer") or {}).get("storage") or {}).get(
                        "collection_prefix", DEFAULT_COLLECTION_PREFIX
                    )
                    == settings.collection_prefix
                )
            )
        ):
            return
        # A completed redelivery creates no vectors and must preserve its index
        # even when conversion lineage is unavailable for a future generation.
        extraction_identity = published_extraction_identity("card_attachment", attachment_uid, generation)
        directory = Env.DATA_DIR / "document-retrieval"
        # No PostgreSQL row lock is held during remote inference.
        vector_allowed = set(Env.get_from_env("DOCUMENT_VECTOR_ALLOWED_BASE_URLS", "").split(","))
        with cached_document_embeddings(
            embeddings, binding_uid=snapshot["binding_uid"], content_hash=content_hash,
            extraction_identity=extraction_identity,
            fingerprint=fingerprint, dimensions=settings.dimensions, timeout_seconds=settings.timeout_seconds,
            splitter=settings.splitter,
        ) as cache:
            context = open_document_vector_store(settings, cache, fingerprint, directory, vector_allowed)
            with context as store:
                source = dict(
                    board_uid=project.get_uid(),
                    card_uid=card.get_uid(),
                    attachment_uid=attachment_uid,
                    content_hash=content_hash,
                    embedding_fingerprint=fingerprint,
                )
                storage = {"type": settings.store}
                if cache.token:
                    storage["cache_use_token"] = cache.token
                if settings.store == "postgres":
                    storage["dimensions"] = settings.dimensions
                if settings.store == "qdrant":
                    storage.update(
                        endpoint=str(settings.external_url).rstrip("/"),
                        binding_uid=snapshot["binding_uid"],
                        dimensions=settings.dimensions,
                        collection_prefix=settings.collection_prefix,
                    )
                pointer = await stage_vector_generation(
                    store,
                    embeddings=cache,
                    source=source,
                    text=(document.get("content") or {}).get("markdown", ""),
                    document_json=service.docling_metadata.get_structural_document(
                        card, attachment_uid, generation=generation, content_hash=content_hash
                    ),
                    splitter=settings.splitter,
                    storage=storage,
                )
                remove = delete_vector_generation
                try:
                    # Inference can outlive deletion or an endpoint change. Resolve
                    # the current primary binding before publishing its new pointer.
                    current_binding = service.internal_bot.get_current_by_id_like(snapshot.get("binding_uid"))
                    if not current_binding or current_binding.bot_type != InternalBotType.DocumentEmbedding:
                        raise ValueError("Embedding binding is unavailable")
                    resolve_embedding_snapshot(snapshot, current_binding.value)
                    committed = service.docling_metadata.publish_document_embedding(
                        card,
                        attachment_uid,
                        generation,
                        content_hash,
                        {
                            "status": "indexed", "pointer": pointer, "source_generation": generation,
                            "config": snapshot, "chunk_version": CHUNK_VERSION,
                        },
                        expected_embedding=old,
                    )
                except Exception:
                    remove(store, pointer)
                    raise
                if not committed:
                    remove(store, pointer)
                    return
                cache.retain()
    except Exception as error:
        if isinstance(error, VectorGenerationCleanupError):
            from ...core.db import DbSession
            from .DocumentCleanupTask import record_cleanup

            with DbSession.atomic() as db:
                record_cleanup(db, error.pointer)
        # Provider exceptions may contain credentials or document text. Never publish raw errors.
        service.docling_metadata.publish_document_embedding(
            card,
            attachment_uid,
            generation,
            content_hash,
            {
                **old, "status": "failed",
                "error": (
                    "Published extraction identity unavailable; explicitly reprocess this attachment. "
                    "The previous searchable generation is retained"
                    if isinstance(error, PublishedExtractionUnavailable)
                    else "Inference dispatched or outcome uncertain; automatic resend disabled; reconciliation required. "
                    "The previous searchable generation is retained"
                    if isinstance(error, EmbeddingInferenceUncertain)
                    else "Embedding failed; the previous searchable generation is retained"
                ),
            },
            expected_embedding=old,
        )
    service.docling_metadata.publish_update(CardMetadata, card, SocketTopic.BoardCard)
