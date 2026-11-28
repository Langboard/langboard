"""Process authorized native project sources using the shared document facilities."""

from copy import deepcopy
from json import dumps, loads
from os import unlink
from pathlib import Path
from tempfile import NamedTemporaryFile
from ...core.broker import Broker
from ...core.broker.TaskParameters import TaskParameters
from ...core.storage import Storage
from ...domain.models.InternalBot import InternalBotType
from ...domain.services import DomainService
from ...Env import Env
from .DoclingMetadataTask import _BoundedDownload, _convert_to_markdown, _current_vision_config
from .DocumentEmbedding import create_document_embeddings, resolve_embedding_snapshot, validate_embedding_config
from .DocumentEmbeddingCache import CHUNK_VERSION, cached_document_embeddings
from .DocumentExtractionCache import (
    PublishedExtractionUnavailable,
    cached_document_extraction,
    published_extraction_identity,
)
from .DocumentKeywords import keyword_languages, normalize_keywords
from .DocumentVectorGeneration import embedding_fingerprint
from .DocumentVectorStore import (
    VectorGenerationCleanupError,
    astage_vector_generation,
    delete_vector_generation,
    open_document_vector_store,
)


def _payload(request):
    value = loads(request)
    if not isinstance(value, dict):
        return None
    if not all(isinstance(value.get(key), str) and value[key] for key in ("attachment_uid", "generation")):
        return None
    return value["attachment_uid"], value["generation"]


@Broker.wrap_async_task_decorator
async def index_project_attachment(request: str):
    service = DomainService()
    try:
        payload = _payload(request)
        if payload:
            _index_project_attachment(service, *payload)
    finally:
        service.close()


@Broker.wrap_async_task_decorator
async def embed_project_attachment(request: str):
    service = DomainService()
    try:
        payload = _payload(request)
        if payload:
            await _embed_project_attachment(service, *payload)
    finally:
        service.close()


def _source_fence(service, attachment_uid, generation, document, *, status, documents=None):
    documents = documents or service.project_document
    current = documents.load_source(attachment_uid, generation)
    if not current:
        raise InterruptedError("Document source is no longer authorized")
    latest = current[2]
    if latest.status != status or any(
        getattr(latest, key) != getattr(document, key)
        for key in ("generation", "source_revision", "vision_config", "embedding_config")
    ):
        raise InterruptedError("Document processing snapshot changed")
    return current


def _index_project_attachment(service, attachment_uid, generation, *, documents=None, embedding_task=None):
    documents = documents or service.project_document
    temp_path = ""
    indexed = False
    try:
        source = documents.load_source(attachment_uid, generation)
        if not source or source[2].status != "pending":
            return
        snapshot = deepcopy(source[2])
        _current_vision_config(service, snapshot.vision_config)
        source = documents.claim(attachment_uid, generation)
        if not source:
            return
        project, attachment, document = source
        document = deepcopy(document)
        private = _current_vision_config(service, document.vision_config)

        attachment = deepcopy(attachment)

        def fence(*_):
            current = _source_fence(
                service, attachment_uid, generation, document, status="processing", documents=documents
            )
            if any(
                getattr(current[1], key) != getattr(attachment, key) for key in ("filename", "file", "content_sha256")
            ):
                raise InterruptedError("Document source changed")
            _current_vision_config(service, document.vision_config)

        fence()
        with NamedTemporaryFile(delete=False, suffix=Path(attachment.filename).suffix) as temporary:
            temp_path = temporary.name
            writer = _BoundedDownload(temporary, Env.MAX_FILE_SIZE_MB * 1024 * 1024)
            downloaded = Storage.download_file(attachment.file, writer)
        fence()
        if not downloaded or not writer.size or writer.digest.hexdigest() != attachment.content_sha256:
            raise ValueError("Document download does not match authoritative content")
        keywords = {}
        languages = keyword_languages(private)

        def collect(value):
            for language, words in normalize_keywords(value, languages).items():
                keywords[language] = normalize_keywords(
                    {language: keywords.get(language, []) + words}, [language], limit=32
                ).get(language, [])

        def convert():
            structure = []
            markdown = _convert_to_markdown(
                temp_path,
                on_progress=fence,
                vision_value=dumps(private),
                on_keywords=collect,
                on_document=structure.append,
            )
            return {"markdown": markdown, "keywords": keywords, "document_json": structure[0] if structure else None}

        with cached_document_extraction(
            binding_uid=document.vision_config["binding_uid"],
            content_hash=writer.digest.hexdigest(),
            vision_config=document.vision_config,
            filename=attachment.filename,
            timeout_seconds=Env.DOCLING_CONVERSION_TIMEOUT_SECONDS,
            on_validate=fence,
        ) as cache:
            result = cache.get_or_convert(convert)
            fence()
            indexed = documents.publish_extraction(
                attachment_uid,
                generation,
                markdown=result["markdown"],
                keywords=result["keywords"],
                document_json=result["document_json"],
                extraction_cache_token=cache.token,
            )
            if indexed:
                cache.retain()
    except Exception:
        documents.mark_failed(attachment_uid, generation, phase="extraction")
    finally:
        if temp_path:
            try:
                unlink(temp_path)
            except OSError:
                pass
    if indexed and isinstance(document.embedding_config, dict):
        try:
            args, kwargs = TaskParameters(dumps(dict(attachment_uid=attachment_uid, generation=generation))).pack()
            Broker.celery.send_task(
                embedding_task or "langboard_shared.tasks.docling.ProjectDocumentTask.embed_project_attachment",
                args=args,
                kwargs=kwargs,
                time_limit=600,
                soft_time_limit=570,
            )
        except Exception:
            documents.mark_failed(attachment_uid, generation, phase="embedding")


def _record_cleanup(pointer):
    from ...core.db import DbSession
    from .DocumentCleanupTask import record_cleanup

    with DbSession.atomic() as db:
        record_cleanup(db, pointer)


async def _embed_project_attachment(service, attachment_uid, generation, *, documents=None, source_factory=None):
    documents = documents or service.project_document
    source = documents.load_source(attachment_uid, generation)
    if not source or source[2].status != "indexed" or not isinstance(source[2].embedding_config, dict):
        return
    project, attachment, document = source
    document = deepcopy(document)
    snapshot = document.embedding_config
    old = deepcopy(document.embedding or {})
    try:
        extraction_identity = published_extraction_identity(documents.source_type, attachment_uid, generation)

        def fence():
            current = _source_fence(
                service, attachment_uid, generation, document, status="indexed", documents=documents
            )
            if (current[2].embedding or {}) != old:
                raise InterruptedError("Document embedding publication superseded")
            binding = service.internal_bot.get_current_by_id_like(snapshot.get("binding_uid"))
            if not binding or binding.bot_type != InternalBotType.DocumentEmbedding:
                raise ValueError("Embedding binding unavailable")
            return resolve_embedding_snapshot(snapshot, binding.value)

        private = fence()
        config, settings = validate_embedding_config(private)
        fingerprint = embedding_fingerprint(
            provider=config["base_url"], model=config["model_name"], dimensions=settings.dimensions, version="v1"
        )
        if (
            old.get("status") == "indexed"
            and old.get("source_generation") == generation
            and old.get("config") == snapshot
            and old.get("chunk_version") == CHUNK_VERSION
            and (old.get("pointer") or {}).get("embedding_fingerprint") == fingerprint
        ):
            return
        allowed = set(Env.get_from_env("MODEL_PROVIDER_ALLOWED_BASE_URLS", "").split(","))
        provider = create_document_embeddings(private, allowed)
        from langchain_core.embeddings import Embeddings

        class FencedEmbeddings(Embeddings):
            def embed_documents(self, texts):
                fence()
                result = provider.embed_documents(texts)
                fence()
                return result

            async def aembed_documents(self, texts):
                fence()
                result = await provider.aembed_documents(texts)
                fence()
                return result

            def embed_query(self, text):
                raise NotImplementedError

        with cached_document_embeddings(
            FencedEmbeddings(),
            binding_uid=snapshot["binding_uid"],
            content_hash=attachment.content_sha256,
            extraction_identity=extraction_identity,
            fingerprint=fingerprint,
            dimensions=settings.dimensions,
            timeout_seconds=settings.timeout_seconds,
            splitter=settings.splitter,
        ) as cache:
            vector_allowed = set(Env.get_from_env("DOCUMENT_VECTOR_ALLOWED_BASE_URLS", "").split(","))
            fence()
            with open_document_vector_store(
                settings,
                cache,
                fingerprint,
                Env.DATA_DIR / "document-retrieval",
                vector_allowed,
            ) as store:
                storage = {"type": settings.store, "cache_use_token": cache.token}
                if settings.store == "postgres":
                    storage["dimensions"] = settings.dimensions
                if settings.store == "qdrant":
                    storage.update(
                        endpoint=str(settings.external_url).rstrip("/"),
                        binding_uid=snapshot["binding_uid"],
                        dimensions=settings.dimensions,
                        collection_prefix=settings.collection_prefix,
                    )
                fence()
                pointer = await astage_vector_generation(
                    store,
                    embeddings=cache,
                    source={
                        **(
                            source_factory(project, attachment)
                            if source_factory
                            else {
                                "source_type": "project_attachment",
                                "board_uid": project.get_uid(),
                                "attachment_uid": attachment_uid,
                                "content_hash": attachment.content_sha256,
                            }
                        ),
                        "embedding_fingerprint": fingerprint,
                    },
                    text=document.markdown,
                    document_json=document.document_json,
                    splitter=settings.splitter,
                    storage=storage,
                )
                try:
                    fence()
                    published = documents.publish_embedding(
                        attachment_uid,
                        generation,
                        expected_embedding=old,
                        embedding=dict(
                            status="indexed",
                            pointer=pointer,
                            source_generation=generation,
                            config=snapshot,
                            chunk_version=CHUNK_VERSION,
                        ),
                    )
                except Exception:
                    delete_vector_generation(store, pointer)
                    raise
                if not published:
                    delete_vector_generation(store, pointer)
                    return
                cache.retain()
    except Exception as error:
        if isinstance(error, InterruptedError):
            return
        if isinstance(error, VectorGenerationCleanupError):
            _record_cleanup(error.pointer)
        documents.publish_embedding(
            attachment_uid,
            generation,
            expected_embedding=old,
            embedding={
                **old,
                "status": "failed",
                "error": (
                    "Published extraction identity unavailable; explicitly reprocess this attachment"
                    if isinstance(error, PublishedExtractionUnavailable)
                    else "Document embedding failed; request processing again"
                ),
            },
        )
