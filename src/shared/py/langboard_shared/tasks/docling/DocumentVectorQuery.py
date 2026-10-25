"""Bounded generation-scoped queries; callers own current authorization checks."""

from math import isfinite
from .DocumentSource import document_source_identity
from .DocumentVectorGeneration import MAX_ATTACHMENT_CHUNKS
from .DocumentVectorStore import document_source_filter


def search_vector_generation(store, pointer: dict, query: str, settings, *, max_tokens: int = 4000) -> list[dict]:

    if not isinstance(query, str) or not query.strip() or len(query) > 1000:
        raise ValueError("Query must contain 1..1000 characters")
    if type(max_tokens) is not int or not 128 <= max_tokens <= 4000:
        raise ValueError("Invalid excerpt token budget")
    ids = pointer.get("chunk_ids")
    if (
        not isinstance(ids, list)
        or not 1 <= len(ids) <= MAX_ATTACHMENT_CHUNKS
        or not all(isinstance(i, str) and i for i in ids)
    ):
        raise ValueError("Reindex this attachment before vector retrieval")
    source = document_source_identity(pointer, require_generation=True)
    filter = document_source_filter(pointer.get("storage") or {}, source)
    if settings.search_type == "mmr":
        if settings.score_threshold is not None:
            raise ValueError("MMR with a score threshold is not supported")
        documents = store.max_marginal_relevance_search(
            query, k=settings.k, fetch_k=settings.fetch_k, lambda_mult=settings.lambda_mult, filter=filter
        )
        hits = [(document, None) for document in documents]
    else:
        hits = store.similarity_search_with_score(query, k=settings.k, filter=filter)
    return project_vector_hits(pointer, hits, settings, max_tokens=max_tokens)


def project_vector_hits(pointer: dict, hits, settings, *, max_tokens: int = 4000) -> list[dict]:
    """Project already retrieved native hits against one authoritative active pointer."""
    if type(max_tokens) is not int or not 1 <= max_tokens <= 4000:
        raise ValueError("Invalid excerpt token budget")
    ids = pointer.get("chunk_ids")
    if (not isinstance(ids, list) or not 1 <= len(ids) <= MAX_ATTACHMENT_CHUNKS
        or not all(isinstance(identifier, str) and identifier for identifier in ids)):
        raise ValueError("Invalid active vector generation")
    source = document_source_identity(pointer, require_generation=True)
    import tiktoken
    encoding = tiktoken.get_encoding("cl100k_base")
    remaining = min(max_tokens, settings.max_return_tokens)
    result = []
    for document, score in hits[: settings.k]:
        metadata = document.metadata
        identifier = document.id or metadata.get("_id")
        try:
            actual_source = document_source_identity(metadata, require_generation=True)
        except ValueError:
            continue
        expected_kind = source.get("source_type", "card_attachment")
        actual_kind = actual_source.get("source_type", "card_attachment")
        if (
            str(identifier) not in ids
            or actual_kind != expected_kind
            or any(actual_source.get(key) != value for key, value in source.items() if key != "source_type")
        ):
            continue
        if score is not None and (type(score) not in (int, float) or not isfinite(score)):
            continue
        if settings.score_threshold is not None and (score is None or score < settings.score_threshold):
            continue
        tokens = encoding.encode(document.page_content, disallowed_special=())
        if not remaining:
            break
        # Token boundaries can split a UTF-8 character. Drop only the incomplete
        # trailing bytes rather than fabricating a replacement character.
        content = encoding.decode(tokens[:remaining], errors="ignore")
        remaining -= min(len(tokens), remaining)
        pages = metadata.get("pages")
        pages = sorted({page for page in pages if type(page) is int and page > 0}) if isinstance(pages, list) else []
        result.append(
            {
                "chunk_id": str(identifier),
                "content": content,
                "source": source,
                **({"similarity_score": score} if source.get("source_type") == "comment_compaction" else {}),
                **({"pages": pages, "provenance_scope": metadata.get("provenance_scope", "unknown")} if pages else {}),
                **({"page": metadata["page"]} if type(metadata.get("page")) is int and metadata["page"] > 0 else {}),
            }
        )
    return result


def search_board_documents(service, repository, project, query, *, context, request):
    """Retrieve bounded native candidates; source text never enters query inference."""
    from ...core.db import DbSession, SqlBuilder
    from ...domain.models import Card, CardMetadata, InternalBot
    from ...domain.models.InternalBot import InternalBotType
    from ...domain.services.factory.DoclingMetadataService import DOCLING_DOCUMENTS_METADATA_KEY
    from ...domain.services.ProjectDocumentQuery import _binding
    from ...infrastructure.repositories.factory.CardCommentCompactionRepository import provider_revision
    from .CompactionMetadataIndexTask import _settings
    from .DocumentEmbedding import snapshot_embedding_config

    if not isinstance(query, str) or not query.strip() or len(query) > 1000:
        return []
    try:
        with DbSession.use(readonly=False) as db:
            catalog = SqlBuilder.select.table(CardMetadata).join(Card, Card.id == CardMetadata.card_id).where(
                Card.project_id == project.id, CardMetadata.key == DOCLING_DOCUMENTS_METADATA_KEY,
                CardMetadata.value.contains('"pointer"'),
            )
            if db.exec(catalog.limit(1)).first() is None:
                return []
        configured = getattr(request, "document_query_binding", None)
        if configured is False:
            return []
        if configured is None:
            request.document_query_binding = False
            with DbSession.use(readonly=False) as db:
                binding = db.exec(SqlBuilder.select.table(InternalBot).where(
                    InternalBot.bot_type == InternalBotType.DocumentEmbedding,
                    InternalBot.is_default == True, InternalBot.deleted_at.is_(None),  # noqa: E712
                ).execution_options(populate_existing=True)).first()
                if binding is None:
                    return []
                snapshot = snapshot_embedding_config(binding.value, binding.get_uid())
                if snapshot is None:
                    return []
                revision = provider_revision(binding)
            private, settings, fingerprint = _settings(binding, snapshot)
            configured = (binding, snapshot, revision, private, settings, fingerprint)
            request.document_query_binding = configured
        binding, snapshot, revision, private, settings, fingerprint = configured
        if getattr(request, "document_remaining_tokens", 4000) <= 0:
            return []
        store = request.store(binding, snapshot, revision, private, settings, fingerprint)
        if store is None:
            return []
        constraints = {"board_uid": project.get_uid(), "embedding_fingerprint": fingerprint}
        source_filter = document_source_filter({"type": settings.store}, constraints)
        if settings.search_type == "mmr":
            hits = [(document, None) for document in store.max_marginal_relevance_search(
                query, k=100, fetch_k=100, lambda_mult=settings.lambda_mult, filter=source_filter)]
        else:
            hits = store.similarity_search_with_score(query, k=100, filter=source_filter)
        hits = hits[:100]
        sources = repository.vector_document_sources(
            project, [document.metadata.get("attachment_uid") for document, _ in hits], context=context)
        import tiktoken
        result = []
        remaining = min(getattr(request, "document_remaining_tokens", 4000), settings.max_return_tokens)
        seen = set()
        for document, score in hits:
            source = sources.get(document.metadata.get("attachment_uid"))
            if (source is None or remaining <= 0 or len(result) >= min(20, settings.k)
                or document.metadata.get("attachment_uid") in seen):
                continue
            state = source["document"]
            embedding = state["embedding"]
            pointer = embedding["pointer"]
            recorded = embedding.get("config") or state.get("embedding_config") or {}
            try:
                verified = _binding(service, recorded, pointer)
                if (verified[2] != fingerprint or recorded.get("binding_uid") != binding.get_uid()
                    or verified[1].store != settings.store):
                    continue
            except Exception:
                continue
            projected = project_vector_hits(pointer, [(document, score)], settings, max_tokens=remaining)
            if projected:
                hit = projected[0]
                seen.add(pointer["attachment_uid"])
                remaining -= len(tiktoken.get_encoding("cl100k_base").encode(hit["content"], disallowed_special=()))
                result.append({"attachment_uid": pointer["attachment_uid"], "state": source,
                               "hit": hit, "config": recorded, "binding": verified})
        request.document_remaining_tokens = remaining
        return result
    except Exception:
        # Preserve keyword fallback without repeating a timed-out optional store
        # once per readable board. The next user request gets a fresh attempt.
        request.document_query_binding = False
        return []


def validate_board_document_hits(service, repository, project, candidates, *, context):
    """Fence grants, active pointers and provider changes after every external query."""
    from ...domain.services.ProjectDocumentQuery import _binding

    if not candidates:
        return {}
    sources = repository.vector_document_sources(
        project, [candidate["attachment_uid"] for candidate in candidates], context=context)
    result = {}
    for candidate in candidates[:20]:
        source = sources.get(candidate["attachment_uid"])
        if source != candidate["state"]:
            continue
        try:
            document = source["document"]
            pointer = document["embedding"]["pointer"]
            if _binding(service, candidate["config"], pointer) != candidate["binding"]:
                continue
        except Exception:
            continue
        hit = candidate["hit"]
        matches = result.setdefault(source["card_id"], [])
        if len(matches) < 2 and not any(match["attachment_uid"] == candidate["attachment_uid"] for match in matches):
            matches.append({
                "attachment_uid": candidate["attachment_uid"], "filename": source["filename"],
                "snippet": hit["content"][:500], "chunk_id": hit["chunk_id"],
                "generation": pointer["generation"], "content_hash": pointer["content_hash"],
                **{key: hit[key] for key in ("page", "pages", "provenance_scope") if key in hit},
            })
    return result
