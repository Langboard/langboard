"""Durable native indexing for provenance-bound comment compaction metadata.

The existing private CardMetadata row is both completion outbox and active pointer.
Exact serialized-value CAS fences competing deliveries without changing attachments.
"""

import json
from contextlib import ExitStack
from datetime import timedelta
from hashlib import sha256
from uuid import uuid4
from sqlalchemy import delete, update
from ...core.broker import Broker
from ...core.db import DbSession, SqlBuilder
from ...core.security.CollaborationChannel import CollaborationChannel
from ...core.types import SafeDateTime
from ...domain.models import (
    Card,
    CardComment,
    CardCommentCompaction,
    CardMetadata,
    InternalBot,
    Project,
    ProjectActivity,
    ProjectRole,
    User,
)
from ...domain.models.DocumentVectorCleanup import DocumentVectorCleanup
from ...domain.models.InternalBot import InternalBotType
from ...domain.services import DomainService
from ...Env import Env
from ...helpers import InfraHelper
from ...infrastructure.repositories.factory.CardCommentCompactionRepository import provider_revision
from .DocumentCleanupTask import record_cleanup
from .DocumentEmbedding import (
    create_document_embeddings,
    resolve_embedding_snapshot,
    snapshot_embedding_config,
    validate_embedding_config,
)
from .DocumentEmbeddingCache import CHUNK_VERSION, activate_cache_references, cached_document_embeddings
from .DocumentVectorGeneration import embedding_fingerprint
from .DocumentVectorStore import (
    _prepare_vector_generation,
    _write_vector_generation,
    document_source_filter,
    open_document_vector_store,
)


TASK_NAME = "langboard.compaction_metadata_index"
PREFIX = "internal.comment_compaction."
LEASE_SECONDS = 600


def enqueue_index(metadata_id):
    try:
        Broker.celery.send_task(TASK_NAME, args=[int(metadata_id)], time_limit=600, soft_time_limit=570)
        return True
    except Exception:
        Broker.logger.warning("Compaction index dispatch unavailable; durable request retained")
        return False


def _data(row):
    value = json.loads(row.value)
    if value.get("source") != "comment_compaction" or value.get("derived") is not True:
        raise ValueError("Invalid compaction metadata")
    return value


def _cas(db, row, data):
    value = json.dumps(data, ensure_ascii=False, sort_keys=True)
    changed = db.exec(update(CardMetadata).execution_options(synchronize_session=False).where(
        CardMetadata.id == row.id, CardMetadata.value == row.value,
    ).values(value=value, updated_at=SafeDateTime.now())) == 1
    return changed


def _row(db, metadata_id):
    return db.exec(SqlBuilder.select.table(CardMetadata).where(CardMetadata.id == metadata_id,
        CardMetadata.key.startswith(PREFIX)).execution_options(populate_existing=True).with_for_update()).first()


def _context(service, db, row):
    """Re-read producer authority and every cited original on the primary."""
    data = _data(row)
    record = db.exec(SqlBuilder.select.table(CardCommentCompaction).where(
        CardCommentCompaction.card_id == row.card_id,
        CardCommentCompaction.id == InfraHelper.convert_id(data["compaction_uid"]),
    ).execution_options(populate_existing=True)).first()
    card = db.exec(SqlBuilder.select.table(Card).where(Card.id == row.card_id)
                  .execution_options(populate_existing=True)).first()
    if not record or record.status != "completed" or not card:
        raise ValueError("Compaction unavailable")
    project = db.exec(SqlBuilder.select.table(Project).where(Project.id == card.project_id)
                     .execution_options(populate_existing=True).with_for_update()).first()
    card = db.exec(SqlBuilder.select.table(Card).where(Card.id == row.card_id)
                  .execution_options(populate_existing=True).with_for_update()).first()
    if project is None or project.deleted_at is not None or card.project_id != project.id:
        raise ValueError("Compaction unavailable")
    compactor = service.card_comment_compaction
    if record.user_id is None:
        authority = data.get("automatic_authority")
        if not isinstance(authority, dict) or not authority.get("rule_uid"):
            raise ValueError("Automatic compaction authority unavailable")
        project, binding, card, current_authority = compactor._automatic_context(
            db, project.id, record.internal_bot_id, card.id, rule_id=InfraHelper.convert_id(authority["rule_uid"]))
        if current_authority != authority:
            raise ValueError("Automatic compaction authority changed")
    else:
        actor = db.exec(SqlBuilder.select.table(User).where(User.id == record.user_id)
                        .execution_options(populate_existing=True).with_for_update()).first()
        db.exec(SqlBuilder.select.table(ProjectRole).where(ProjectRole.user_id == record.user_id,
            ProjectRole.project_id == project.id).with_for_update()).first()
        project, card, _ = compactor._authorize(actor, project, card, CollaborationChannel.Api, write=True)
        # Manual producers need the same provider fence as automatic authority:
        # settings/default changes must wait until pointer publication commits.
        db.exec(SqlBuilder.select.table(InternalBot).where(InternalBot.id == record.internal_bot_id)
                .execution_options(populate_existing=True).with_for_update()).first()
        binding = service.internal_bot.get_comment_compaction_binding()
    # Fence cited edit/delete writers until publication or query validation commits.
    for model, kind in ((CardComment, "comment"), (ProjectActivity, "activity")):
        ids = [InfraHelper.convert_id(item["evidence_ref"].split(":", 1)[1])
               for item in record.source_manifest if item["evidence_ref"].startswith(kind + ":")]
        if ids:
            db.exec(SqlBuilder.select.table(model).where(model.id.in_(ids)).order_by(model.id).with_for_update()).all()
    block = compactor._completed_block(db, project, card, record, binding, {"uid": record.get_uid(), "status": record.status})
    if block["status"] != "completed" or data.get("source_revision") != record.source_revision:
        raise ValueError("Compaction invalidated")
    # A newer valid cumulative summary retires this derived search generation.
    later = db.exec(SqlBuilder.select.table(CardCommentCompaction).where(
        CardCommentCompaction.card_id == card.id, CardCommentCompaction.status == "completed",
        CardCommentCompaction.id > record.id,
    ).execution_options(populate_existing=True)).all()
    refs = set(block["evidence_refs"])
    for candidate in later:
        if refs.intersection(item["evidence_ref"] for item in candidate.source_manifest):
            newer = compactor._completed_block(db, project, card, candidate, binding,
                {"uid": candidate.get_uid(), "status": candidate.status})
            if newer["status"] == "completed":
                raise ValueError("Compaction superseded")
    embedding = db.exec(SqlBuilder.select.table(InternalBot).where(
        InternalBot.bot_type == InternalBotType.DocumentEmbedding, InternalBot.is_default == True,  # noqa: E712
        InternalBot.deleted_at.is_(None),
    ).execution_options(populate_existing=True).with_for_update()).first()
    if not embedding:
        raise ValueError("Embedding unavailable")
    snapshot = snapshot_embedding_config(embedding.value, embedding.get_uid())
    if snapshot is None:
        raise ValueError("Embedding disabled")
    # Domain-separated lineage comes only from the validated completed producer.
    extraction_identity = sha256(json.dumps([
        "comment-compaction-terms-v1", record.provider_revision, record.policy_version,
    ], separators=(",", ":")).encode()).hexdigest()
    return data, project, card, block, embedding, snapshot, extraction_identity


def _text(block):
    # Only validated keywords/entities enter the shared persistent embedding cache.
    draft = block["draft"]
    terms = [item["value"] for item in draft.get("keywords", [])]
    terms.extend(term for item in draft.get("entities", []) for term in [item["name"], *item.get("aliases", [])])
    return "\n".join(terms)


def _settings(binding, snapshot):
    private = resolve_embedding_snapshot(snapshot, binding.value)
    config, settings = validate_embedding_config(private)
    fingerprint = embedding_fingerprint(provider=config["base_url"], model=config["model_name"],
                                        dimensions=settings.dimensions, version="v1")
    return private, settings, fingerprint


def _retire(db, row, data):
    previous = (data.get("index") or {}).get("pointer")
    if _cas(db, row, {**data, "index": {"status": "retired"}}):
        record_cleanup(db, previous)


async def index_metadata(service, metadata_id):
    token = uuid4().hex
    with DbSession.atomic() as db:
        row = _row(db, metadata_id)
        if row is None:
            return False
        data = _data(row)
        old = data.get("index") or {}
        if old.get("status") == "running" and old.get("expires_at", "") > SafeDateTime.now().isoformat():
            return False
        try:
            data, project, card, block, binding, snapshot, extraction_identity = _context(service, db, row)
        except Exception:
            _retire(db, row, data)
            return False
        text = _text(block)
        if not text.strip():
            _retire(db, row, data)
            return False
        revision = provider_revision(binding)
        if (old.get("status") == "indexed" and old.get("provider_revision") == revision
            and old.get("config") == snapshot and old.get("chunk_version") == CHUNK_VERSION):
            return True
        claim = {**data, "index": {"status": "running", "token": token,
            "expires_at": (SafeDateTime.now() + timedelta(seconds=LEASE_SECONDS)).isoformat(),
            **({"pointer": old["pointer"]} if old.get("pointer") else {})}}
        if not _cas(db, row, claim):
            return False
    pointer = None
    cleanup_id = None
    try:
        private, settings, fingerprint = _settings(binding, snapshot)
        embeddings = create_document_embeddings(private, set(Env.get_from_env("MODEL_PROVIDER_ALLOWED_BASE_URLS", "").split(",")))
        content_hash = sha256(text.encode()).hexdigest()
        with cached_document_embeddings(embeddings, binding_uid=binding.get_uid(), content_hash=content_hash,
            extraction_identity=extraction_identity,
            fingerprint=fingerprint, dimensions=settings.dimensions, timeout_seconds=settings.timeout_seconds,
            splitter=settings.splitter) as cache:
            with open_document_vector_store(settings, cache, fingerprint, Env.DATA_DIR / "document-retrieval",
                set(Env.get_from_env("DOCUMENT_VECTOR_ALLOWED_BASE_URLS", "").split(","))) as store:
                source = {"source_type": "comment_compaction", "board_uid": project.get_uid(),
                    "card_uid": card.get_uid(), "compaction_uid": block["uid"],
                    "source_revision": block["source_revision"], "content_hash": content_hash,
                    "embedding_fingerprint": fingerprint}
                storage = {"type": settings.store, "dimensions": settings.dimensions, "cache_use_token": cache.token}
                if settings.store == "qdrant":
                    storage.update(endpoint=str(settings.external_url).rstrip("/"), binding_uid=binding.get_uid(),
                                   collection_prefix=settings.collection_prefix)
                chunks, pointer = _prepare_vector_generation(source=source, text=text,
                    splitter=settings.splitter, storage=storage, document_json=None)
                storage["cache_reference_count"] = len({chunk.page_content for chunk in chunks})
                # A worker killed after inference/write leaves a durable cleanup receipt.
                # Its lease exceeds the bounded inference/task budget.
                with DbSession.atomic() as db:
                    cleanup = DocumentVectorCleanup(pointer=pointer,
                        due_at=SafeDateTime.now() + timedelta(seconds=LEASE_SECONDS))
                    db.insert(cleanup)
                    cleanup_id = cleanup.id
                await cache.aembed_documents([chunk.page_content for chunk in chunks])
                _write_vector_generation(store, chunks, pointer)
                with DbSession.atomic() as db:
                    row = _row(db, metadata_id)
                    current = _data(row) if row else {}
                    if ((current.get("index") or {}).get("token") != token
                        or (current.get("index") or {}).get("expires_at", "") <= SafeDateTime.now().isoformat()):
                        raise ValueError("Index claim replaced")
                    fresh, fresh_project, fresh_card, fresh_block, fresh_binding, fresh_snapshot, fresh_identity = _context(service, db, row)
                    if (provider_revision(fresh_binding) != revision or snapshot != fresh_snapshot
                        or project.id != fresh_project.id or card.id != fresh_card.id or _text(fresh_block) != text
                        or fresh_identity != extraction_identity
                        or current["index"]["expires_at"] <= SafeDateTime.now().isoformat()):
                        raise ValueError("Index source changed")
                    if not _cas(db, row, {**fresh, "index": {"status": "indexed", "pointer": pointer,
                        "provider_revision": revision, "config": snapshot, "chunk_version": CHUNK_VERSION}}):
                        raise ValueError("Index claim replaced")
                    activate_cache_references(db, cache.token, expected_count=storage["cache_reference_count"])
                    record_cleanup(db, old.get("pointer"))
                    db.exec(delete(DocumentVectorCleanup).where(DocumentVectorCleanup.id == cleanup_id))
                cache.retain()
                return True
    except Exception:
        # Never persist provider messages, credentials or inferred text as errors.
        with DbSession.atomic() as db:
            if pointer:
                record_cleanup(db, pointer)
                if cleanup_id is not None:
                    db.exec(delete(DocumentVectorCleanup).where(DocumentVectorCleanup.id == cleanup_id))
            row = _row(db, metadata_id)
            if row:
                current = _data(row)
                if (current.get("index") or {}).get("token") == token:
                    _retire(db, row, current)
        return False


def _query_embeddings(provider):
    from langchain_core.embeddings import Embeddings

    class QueryEmbeddings(Embeddings):
        """Request-local query inference reuse without persistent text or vectors."""
        def __init__(self):
            self.vectors = {}
            self.failed = False

        def embed_query(self, text):
            if self.failed:
                raise ValueError("Query provider unavailable for this request")
            if text not in self.vectors:
                try:
                    self.vectors[text] = provider.embed_query(text)
                except Exception:
                    self.failed = True
                    raise
            return self.vectors[text]

        def embed_documents(self, texts):
            # The official LangGraph store uses the document method for queries.
            return [self.embed_query(text) for text in texts]

    return QueryEmbeddings()


class SemanticSearchRequest(ExitStack):
    """Own verified query providers and native stores for one search request."""

    def __init__(self):
        super().__init__()
        self.stores = {}

    def store(self, binding, snapshot, revision, private, settings, fingerprint):
        provider_urls = set(Env.get_from_env("MODEL_PROVIDER_ALLOWED_BASE_URLS", "").split(","))
        store_urls = set(Env.get_from_env("DOCUMENT_VECTOR_ALLOWED_BASE_URLS", "").split(","))
        directory = Env.DATA_DIR / "document-retrieval"
        # Include current credentials in this ephemeral identity without hashing,
        # persisting, or exposing them. A settings write also changes revision.
        key = (binding.get_uid(), revision, binding.value, json.dumps(snapshot, sort_keys=True),
               fingerprint, str(directory), Env.MAIN_DATABASE_URL if settings.store == "postgres" else None,
               tuple(sorted(provider_urls)), tuple(sorted(store_urls)))
        if key not in self.stores:
            # Failures are request-local too: another board must not retry the
            # same unavailable provider/store once per readable board.
            self.stores[key] = None
            provider = create_document_embeddings(private, provider_urls)
            close = getattr(provider, "close", None) or getattr(getattr(provider, "root_client", None), "close", None)
            if close:
                self.callback(close)
            self.stores[key] = self.enter_context(open_document_vector_store(
                settings, _query_embeddings(provider), fingerprint, directory, store_urls, create=False))
        return self.stores[key]

    def close(self):
        try:
            super().close()
        finally:
            self.stores.clear()

    def __exit__(self, *exc):
        try:
            return super().__exit__(*exc)
        except Exception:
            # ExitStack still attempts every cleanup. Optional provider/store
            # teardown must not remove native SQL search coverage or expose
            # provider errors. False preserves any original request exception.
            return False
        finally:
            self.stores.clear()


def search_metadata(service, user, project, card, query, *, channel, request=None):
    """Query every indexed generation on one currently authorized card."""
    if card is None:
        return []
    return search_board_metadata(service, user, project, query, channel=channel, card=card, request=request).get(card.id, [])


def search_board_metadata(service, user, project, query, *, channel, card=None, request=None):
    """One bounded native board query ranks every indexed card age before ACL checks.

    Vector payloads are internal candidates. Only current authorized source
    projections can leave this function; query inference receives no source text.
    """
    from .DocumentSource import document_source_identity

    if not isinstance(query, str) or not query.strip() or len(query) > 1000:
        return {}
    resolved = service.card.resolve_visibility_context(project, user, channel)
    if resolved is None or not resolved[1].active or not resolved[1].project_member:
        return {}
    project, _ = resolved
    requested_card_id = None
    try:
        if card is not None:
            project, card, _ = service.card_comment_compaction._authorize(user, project, card, channel)
            requested_card_id = card.id
        with DbSession.atomic() as db:
            # Index publication uses canonical JSON in _cas. Probe the durable
            # catalog before opening a fingerprint store shared with other boards.
            catalog = SqlBuilder.select.table(CardMetadata).join(Card, Card.id == CardMetadata.card_id).where(
                Card.project_id == project.id, CardMetadata.key.startswith(PREFIX),
                CardMetadata.value.contains('"status": "indexed"'),
            )
            if requested_card_id is not None:
                catalog = catalog.where(CardMetadata.card_id == requested_card_id)
            if db.exec(catalog.limit(1)).first() is None:
                return {}
            binding = db.exec(SqlBuilder.select.table(InternalBot).where(
                InternalBot.bot_type == InternalBotType.DocumentEmbedding, InternalBot.is_default == True,  # noqa: E712
                InternalBot.deleted_at.is_(None),
            ).execution_options(populate_existing=True)).first()
            if binding is None:
                return {}
            snapshot = snapshot_embedding_config(binding.value, binding.get_uid())
            if snapshot is None:
                return {}
            revision = provider_revision(binding)
        private, settings, fingerprint = _settings(binding, snapshot)
        import tiktoken
        from .DocumentVectorQuery import project_vector_hits

        with ExitStack() as local:
            if request is None:
                request = local.enter_context(SemanticSearchRequest())
            store = request.store(binding, snapshot, revision, private, settings, fingerprint)
            if store is None:
                return {}
            constraints = {"source_type": "comment_compaction", "board_uid": project.get_uid(),
                           "embedding_fingerprint": fingerprint}
            if requested_card_id is not None:
                constraints["card_uid"] = card.get_uid()
            source_filter = document_source_filter({"type": settings.store}, constraints)
            hits = store.similarity_search_with_score(query, k=100, filter=source_filter)
    except Exception:
        return {}
    result, count, remaining = {}, 0, 4000
    encoding = tiktoken.get_encoding("cl100k_base")
    for document, score in hits[:100]:
        if count >= 20 or remaining <= 0:
            break
        try:
            source = document_source_identity(document.metadata, require_generation=True)
            if (source.get("source_type") != "comment_compaction" or source["board_uid"] != project.get_uid()
                or source["embedding_fingerprint"] != fingerprint):
                continue
            with DbSession.atomic() as db:
                row = db.exec(SqlBuilder.select.table(CardMetadata).where(
                    CardMetadata.card_id == InfraHelper.convert_id(source["card_uid"]),
                    CardMetadata.key == f"{PREFIX}{source['compaction_uid']}.search",
                ).execution_options(populate_existing=True).with_for_update()).first()
                data, fresh_project, card, block, current_binding, current_snapshot, _ = _context(service, db, row)
                service.card_comment_compaction._authorize(user, fresh_project, card, channel)
                state = data.get("index") or {}
                if (fresh_project.id != project.id or (requested_card_id is not None and card.id != requested_card_id)
                    or state.get("status") != "indexed"
                    or state.get("chunk_version") != CHUNK_VERSION or state.get("config") != snapshot
                    or current_snapshot != snapshot or state.get("provider_revision") != revision
                    or provider_revision(current_binding) != revision):
                    continue
                projected = project_vector_hits(state["pointer"], [(document, score)], settings, max_tokens=remaining)
                if not projected:
                    continue
                hit = {"compaction_uid": block["uid"], "source_revision": block["source_revision"],
                       "evidence_refs": block["evidence_refs"], **projected[0]}
                result.setdefault(card.id, []).append(hit)
                count += 1
                remaining -= len(encoding.encode(hit["content"], disallowed_special=()))
        except Exception:
            continue
    # No additional vector or inference calls occur during final source validation.
    for card_id, candidates in list(result.items()):
        with DbSession.use(readonly=False) as db:
            card = db.exec(SqlBuilder.select.table(Card).where(Card.id == card_id)).first()
        checked = validate_metadata_hits(service, user, project, card, candidates, channel=channel)
        if checked:
            result[card_id] = checked
        else:
            del result[card_id]
    return result


def validate_metadata_hits(service, user, project, card, hits, *, channel):
    """Final local pass after all remote queries: discard replaced or revoked hits."""
    valid = []
    for hit in hits[:100]:
        try:
            uid = hit["compaction_uid"]
            with DbSession.atomic() as db:
                row = db.exec(SqlBuilder.select.table(CardMetadata).where(CardMetadata.card_id == card.id,
                    CardMetadata.key == f"{PREFIX}{uid}.search").execution_options(populate_existing=True)
                    .with_for_update()).first()
                data, fresh_project, fresh_card, block, binding, snapshot, _ = _context(service, db, row)
                service.card_comment_compaction._authorize(user, fresh_project, fresh_card, channel)
                state = data.get("index") or {}
                source = hit.get("source") or {}
                pointer = state.get("pointer") or {}
                if (state.get("status") == "indexed" and state.get("chunk_version") == CHUNK_VERSION
                    and state.get("provider_revision") == provider_revision(binding)
                    and state.get("config") == snapshot and fresh_project.id == project.id
                    and source.get("generation") == pointer.get("generation")
                    and source.get("source_revision") == block["source_revision"]
                    and hit.get("chunk_id") in pointer.get("chunk_ids", [])):
                    valid.append(hit)
        except Exception:
            continue
    return valid


def recover_indexes(*, limit=100):
    """Reconcile durable rows, including queue loss, expired claims and retired sources."""
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Index recovery limit must be between 1 and 100")
    with DbSession.use(readonly=False) as db:
        rows = db.exec(SqlBuilder.select.table(CardMetadata).where(CardMetadata.key.startswith(PREFIX))
                      .order_by(CardMetadata.updated_at, CardMetadata.id).limit(limit)).all()
    for row in rows:
        enqueue_index(row.id)
        # Fair recovery across indexed and pending rows; no provider work here.
        with DbSession.atomic() as db:
            db.exec(update(CardMetadata).where(CardMetadata.id == row.id, CardMetadata.value == row.value)
                    .values(updated_at=SafeDateTime.now()))
    return len(rows)


@Broker.wrap_async_task_decorator({"name": TASK_NAME, "acks_late": True, "reject_on_worker_lost": True})
async def index_compaction_metadata(metadata_id: int):
    service = DomainService()
    try:
        await index_metadata(service, metadata_id)
    finally:
        service.close()


if __name__ == "__main__":
    recover_indexes()
