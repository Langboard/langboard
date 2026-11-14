"""Authorized native project document projections and bounded semantic retrieval."""

from copy import deepcopy
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from ...Env import Env
from ...tasks.docling.DocumentKeywords import KEYWORD_LANGUAGES, normalize_keywords


class DocumentDto(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ProjectDocumentSource(DocumentDto):
    source_type: Literal["project_attachment"] = "project_attachment"
    project_uid: str
    attachment_uid: str
    name: str
    url: str
    board_url: str


class ProjectDocumentRead(DocumentDto):
    source: ProjectDocumentSource
    status: Literal["pending", "processing", "indexed", "failed"]
    generation: str
    text: str = Field(max_length=8000)
    total_chars: int = Field(ge=0)
    next_offset: int | None
    keywords: dict[str, list[str]]
    embedding_status: str


class ProjectDocumentMatch(DocumentDto):
    chunk_id: str
    content: str
    source: ProjectDocumentSource
    pages: list[int] = Field(default_factory=list)
    page: int | None = None


class ProjectDocumentSearch(DocumentDto):
    source: ProjectDocumentSource
    generation: str
    matches: list[ProjectDocumentMatch]


class ProjectDocumentReceipt(DocumentDto):
    attachment_uid: str
    generation: str
    status: Literal["pending", "processing", "indexed", "failed"]


def _readable(service, user, project_uid, attachment_uid):
    result = service.project_document.get_readable(user, project_uid, attachment_uid)
    if result is None:
        raise ValueError("Project document unavailable")
    return result


def _source(project, attachment):
    return ProjectDocumentSource(
        project_uid=project.get_uid(),
        attachment_uid=attachment.get_uid(),
        name=attachment.filename,
        url=attachment.file.path,
        board_url=f"/board/{project.get_uid()}/cloud",
    )


def _identity(attachment, document):
    return deepcopy(
        (
            getattr(attachment, "project_wiki_id", None),
            attachment.file,
            attachment.content_sha256,
            attachment.filename,
            document.source_revision,
            document.generation,
            document.status,
            document.embedding_config,
            document.embedding,
            document.text_source,
            document.vector_source,
        )
    )


def _published_attachment(attachment, snapshot):
    if not snapshot:
        return attachment
    from ...core.storage import FileModel

    published = deepcopy(attachment)
    published.file = FileModel.model_validate(snapshot["file"])
    published.filename = snapshot["filename"]
    published.content_sha256 = snapshot["content_sha256"]
    return published


def read_project_document(
    service,
    user,
    project_uid,
    attachment_uid,
    *,
    offset=0,
    limit=4000,
    source_factory=_source,
    read_model=ProjectDocumentRead,
):
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 8000:
        raise ValueError("Invalid project document read bounds")
    project, attachment, document = _readable(service, user, project_uid, attachment_uid)
    identity = _identity(attachment, document)
    text = document.markdown if document.text_source or document.status == "indexed" else ""
    text = text or ""
    end = min(offset + limit, len(text))
    keywords = document.keywords or {}
    response = read_model(
        source=source_factory(project, _published_attachment(attachment, document.text_source)),
        status=document.status,
        generation=(document.text_source or {}).get("generation", document.generation),
        text=text[offset:end],
        total_chars=len(text),
        next_offset=end if end < len(text) else None,
        keywords=normalize_keywords(keywords, list(KEYWORD_LANGUAGES)),
        embedding_status=(document.embedding or {}).get("status", "unavailable"),
    )
    _, latest_attachment, latest = _readable(service, user, project_uid, attachment_uid)
    if _identity(latest_attachment, latest) != identity:
        raise ValueError("Project document changed; retry read")
    return response


def request_project_document_processing(service, user, project_uid, attachment_uid):
    receipt = service.project_document.request(user, project_uid, attachment_uid, force=False)
    if receipt is None:
        raise ValueError("Project document unavailable")
    return ProjectDocumentReceipt.model_validate(receipt)


def _binding(service, snapshot, pointer):
    from ...tasks.docling.DocumentEmbedding import resolve_embedding_snapshot, validate_embedding_config
    from ...tasks.docling.DocumentRetrievalSettings import DEFAULT_COLLECTION_PREFIX
    from ...tasks.docling.DocumentVectorGeneration import embedding_fingerprint
    from ..models.InternalBot import InternalBotType

    binding = service.internal_bot.get_current_by_id_like(snapshot.get("binding_uid"))
    if not binding or binding.bot_type != InternalBotType.DocumentEmbedding:
        raise ValueError("Project document embedding binding unavailable")
    _, current = validate_embedding_config(binding.value)
    if not current.enabled:
        raise ValueError("Project document retrieval disabled")
    private = resolve_embedding_snapshot(snapshot, binding.value)
    config, settings = validate_embedding_config(private)
    fingerprint = embedding_fingerprint(
        provider=config["base_url"], model=config["model_name"], dimensions=settings.dimensions, version="v1"
    )
    storage = pointer.get("storage") or {}
    if (
        fingerprint != pointer.get("embedding_fingerprint")
        or storage.get("type") != settings.store
        or (
            settings.store == "qdrant"
            and (
                storage.get("endpoint") != str(settings.external_url).rstrip("/")
                or storage.get("collection_prefix", DEFAULT_COLLECTION_PREFIX) != settings.collection_prefix
            )
        )
    ):
        raise ValueError("Project document embedding configuration changed")
    return private, settings, fingerprint


def search_project_document(
    service,
    user,
    project_uid,
    attachment_uid,
    query,
    *,
    max_tokens=2000,
    source_factory=_source,
    search_model=ProjectDocumentSearch,
    match_model=ProjectDocumentMatch,
    source_identity=None,
):
    from ...tasks.docling.DocumentEmbedding import create_document_embeddings
    from ...tasks.docling.DocumentVectorQuery import search_vector_generation
    from ...tasks.docling.DocumentVectorStore import open_document_vector_store

    if (
        not isinstance(query, str)
        or not query.strip()
        or len(query) > 1000
        or type(max_tokens) is not int
        or not 128 <= max_tokens <= 4000
    ):
        raise ValueError("Invalid project document query bounds")
    project, attachment, document = _readable(service, user, project_uid, attachment_uid)
    identity = _identity(attachment, document)
    embedding = deepcopy(document.embedding or {})
    pointer = embedding.get("pointer")
    if not isinstance(pointer, dict) or not pointer.get("chunk_ids"):
        raise ValueError("Project document vectors unavailable")
    published_attachment = _published_attachment(attachment, document.vector_source)
    published_generation = (document.vector_source or {}).get("generation", document.generation)
    expected_source = (
        source_identity(project, published_attachment)
        if source_identity
        else {
            "source_type": "project_attachment",
            "board_uid": project.get_uid(),
            "attachment_uid": attachment.get_uid(),
            "content_hash": published_attachment.content_sha256,
        }
    )
    if (
        any(pointer.get(key) != value for key, value in expected_source.items())
        or any(key in pointer for key in ("card_uid", "wiki_uid") if key not in expected_source)
        or embedding.get("source_generation") != published_generation
    ):
        raise ValueError("Project document vectors do not match current source")
    published_config = (
        document.vector_source.get("embedding_config") if document.vector_source else document.embedding_config
    )
    snapshot = deepcopy(embedding.get("config") or published_config or {})
    try:
        private, settings, fingerprint = _binding(service, snapshot, pointer)
        embeddings = create_document_embeddings(
            private, set(Env.get_from_env("MODEL_PROVIDER_ALLOWED_BASE_URLS", "").split(","))
        )
        with open_document_vector_store(
            settings,
            embeddings,
            fingerprint,
            Env.DATA_DIR / "document-retrieval",
            set(Env.get_from_env("DOCUMENT_VECTOR_ALLOWED_BASE_URLS", "").split(",")),
            create=False,
        ) as store:
            if store is None:
                raise ValueError("Project document vectors unavailable")
            hits = search_vector_generation(store, pointer, query, settings, max_tokens=max_tokens)
    except Exception:
        raise ValueError("Project document vector retrieval unavailable") from None
    latest_project, latest_attachment, latest = _readable(service, user, project_uid, attachment_uid)
    if identity != _identity(latest_attachment, latest):
        raise ValueError("Project document changed; retry search")
    try:
        latest_private, latest_settings, latest_fingerprint = _binding(service, snapshot, pointer)
        if (latest_private, latest_settings, latest_fingerprint) != (private, settings, fingerprint):
            raise ValueError("Binding changed")
    except Exception:
        raise ValueError("Project document embedding binding unavailable") from None
    source = source_factory(latest_project, _published_attachment(latest_attachment, latest.vector_source))
    return search_model(
        source=source,
        generation=published_generation,
        matches=[
            match_model(
                chunk_id=hit["chunk_id"],
                content=hit["content"],
                source=source,
                pages=hit.get("pages", []),
                page=hit.get("page"),
            )
            for hit in hits
        ],
    )
