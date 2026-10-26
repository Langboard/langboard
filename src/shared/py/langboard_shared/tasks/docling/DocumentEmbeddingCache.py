"""Reuse model outputs without sharing attachment pointers or authorization.

Only content hashes, vectors and opaque generation references are persisted.
Short primary transactions fence inference; no database lock spans a model call.
"""

import asyncio
import re
import time
from contextlib import contextmanager
from datetime import timedelta, timezone
from hashlib import sha256
from json import dumps
from math import isfinite
from uuid import uuid4
from sqlalchemy import delete, update
from sqlalchemy.exc import IntegrityError
from ...core.db import DbSession, SqlBuilder
from ...core.types import SafeDateTime
from ...domain.models.DocumentEmbeddingCache import DocumentEmbeddingCache, DocumentEmbeddingCacheReference
from .DocumentEmbedding import embedding_inference_budget_seconds, validated_embeddings
from .DocumentSplitter import DocumentSplitterSettings


PENDING_REFERENCE_HOURS = 8
INFERENCE_BATCH_SIZE = 32
CHUNK_VERSION = "v1"


class EmbeddingCacheUnavailable(RuntimeError):
    pass


class EmbeddingInferenceUncertain(EmbeddingCacheUnavailable):
    def __init__(self):
        super().__init__("Inference dispatched or outcome uncertain; automatic resend disabled; reconciliation required")


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def activate_cache_references(db, token, *, expected_count=None):
    if token is not None:
        if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{32}", token):
            raise ValueError("Invalid server-owned embedding cache reference")
        if expected_count is not None and (type(expected_count) is not int or expected_count < 1):
            raise ValueError("Invalid embedding cache reference count")
        references = db.exec(SqlBuilder.select.table(DocumentEmbeddingCacheReference).where(
            DocumentEmbeddingCacheReference.use_token == token,
        )).all()
        if not references or (expected_count is not None and len(references) != expected_count):
            raise EmbeddingCacheUnavailable("Embedding cache references expired before publication")
        identifiers = sorted({ref.cache_id for ref in references})
        entries = db.exec(SqlBuilder.select.table(DocumentEmbeddingCache).where(
            DocumentEmbeddingCache.id.in_(identifiers),
        ).order_by(DocumentEmbeddingCache.id).with_for_update().execution_options(populate_existing=True)).all()
        if len(entries) != len(identifiers) or any(entry.vector is None for entry in entries):
            raise EmbeddingCacheUnavailable("Embedding cache results unavailable before publication")
        changed = db.exec(
            update(DocumentEmbeddingCacheReference)
            .where(
                DocumentEmbeddingCacheReference.use_token == token,
            )
            .values(expires_at=None)
        )
        if changed != len(references):
            raise EmbeddingCacheUnavailable("Embedding cache references changed before publication")


def release_cache_references(token, *, expired_only=False):
    if token is None:
        return
    if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{32}", token):
        raise ValueError("Invalid server-owned embedding cache reference")
    with DbSession.atomic() as db:
        due = DocumentEmbeddingCacheReference.expires_at <= SafeDateTime.now()
        query = SqlBuilder.select.table(DocumentEmbeddingCacheReference).where(
            DocumentEmbeddingCacheReference.use_token == token,
        )
        if expired_only:
            query = query.where(due)
        references = db.exec(query).all()
        for cache_id in sorted({ref.cache_id for ref in references}):
            entry = db.exec(
                SqlBuilder.select.table(DocumentEmbeddingCache)
                .where(
                    DocumentEmbeddingCache.id == cache_id,
                )
                .with_for_update()
            ).first()
            if entry is None:
                continue
            removal = delete(DocumentEmbeddingCacheReference).where(
                DocumentEmbeddingCacheReference.cache_id == cache_id,
                DocumentEmbeddingCacheReference.use_token == token,
            )
            if expired_only:
                removal = removal.where(due)
            db.exec(removal.execution_options(synchronize_session=False))
            other = db.exec(
                SqlBuilder.select.table(DocumentEmbeddingCacheReference)
                .where(
                    DocumentEmbeddingCacheReference.cache_id == cache_id,
                )
                .limit(1)
            ).first()
            if other is None and entry.lease_token is None:
                db.delete(entry)


def recover_expired_cache_references(*, limit=100):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Cache cleanup limit must be between 1 and 100")
    with DbSession.use(readonly=False) as db:
        references = db.exec(
            SqlBuilder.select.table(DocumentEmbeddingCacheReference)
            .where(
                DocumentEmbeddingCacheReference.expires_at <= SafeDateTime.now(),
            )
            .order_by(DocumentEmbeddingCacheReference.expires_at)
            .limit(limit)
        ).all()
    tokens = {ref.use_token for ref in references}
    for token in tokens:
        release_cache_references(token, expired_only=True)
    return len(tokens)


class CachedDocumentEmbeddings:
    def __init__(
        self, embeddings, *, binding_uid, content_hash, extraction_identity, fingerprint, dimensions, timeout_seconds,
        splitter=None, chunk_version=CHUNK_VERSION,
    ):
        if not isinstance(binding_uid, str) or not binding_uid:
            raise ValueError("An authoritative embedding binding is required")
        if not all(
            isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
            for value in (content_hash, extraction_identity, fingerprint)
        ):
            raise ValueError("Authoritative content, extraction and model hashes are required")
        if type(timeout_seconds) not in (int, float) or not isfinite(timeout_seconds) or not 1 <= timeout_seconds <= 30:
            raise ValueError("Embedding timeout must be finite and between 1 and 30 seconds")
        self.embeddings = validated_embeddings(embeddings, dimensions)
        if not isinstance(chunk_version, str) or not chunk_version:
            raise ValueError("An authoritative chunk version is required")
        splitter = DocumentSplitterSettings.model_validate(splitter or {})
        self.namespace = [
            "v3", binding_uid, content_hash, extraction_identity, fingerprint, chunk_version,
            splitter.model_dump(mode="json"), dimensions,
        ]
        self.timeout_seconds = timeout_seconds
        self.inference_budget_seconds = embedding_inference_budget_seconds(timeout_seconds)
        self.token = uuid4().hex
        self.retained = False
        self.uncertain = False
        self._ready = {}

    def retain(self):
        self.retained = True

    def _key(self, text):
        return sha256(
            dumps([*self.namespace, sha256(text.encode()).hexdigest()], separators=(",", ":")).encode()
        ).hexdigest()

    def _claim(self, key):
        now = SafeDateTime.now()
        with DbSession.atomic() as db:
            query = SqlBuilder.select.table(DocumentEmbeddingCache).where(DocumentEmbeddingCache.cache_key == key)
            entry = db.exec(query.with_for_update().execution_options(populate_existing=True)).first()
            if entry is None:
                try:
                    with db.savepoint():
                        entry = DocumentEmbeddingCache(cache_key=key)
                        db.insert(entry)
                except IntegrityError:
                    entry = db.exec(query.with_for_update().execution_options(populate_existing=True)).first()
                    if entry is None:
                        raise
            reference = db.exec(
                SqlBuilder.select.table(DocumentEmbeddingCacheReference).where(
                    DocumentEmbeddingCacheReference.cache_id == entry.id,
                    DocumentEmbeddingCacheReference.use_token == self.token,
                )
            ).first()
            if reference is None:
                db.insert(
                    DocumentEmbeddingCacheReference(
                        cache_id=entry.id,
                        use_token=self.token,
                        expires_at=now + timedelta(hours=PENDING_REFERENCE_HOURS),
                    )
                )
            owned = False
            if entry.vector is None:
                expired = entry.lease_expires_at is not None and _utc(entry.lease_expires_at) <= now
                if entry.lease_token is None or expired:
                    owned = (
                        db.exec(
                            update(DocumentEmbeddingCache)
                            .where(
                                DocumentEmbeddingCache.id == entry.id,
                                DocumentEmbeddingCache.vector.is_(None),
                                DocumentEmbeddingCache.lease_token == entry.lease_token,
                                DocumentEmbeddingCache.lease_expires_at == entry.lease_expires_at,
                            )
                            .values(
                                lease_token=self.token,
                                lease_expires_at=now + timedelta(seconds=self.inference_budget_seconds),
                            )
                        )
                        == 1
                    )
            return entry.id, entry.vector, owned

    def _release_claims(self, identifiers):
        with DbSession.atomic() as db:
            db.exec(
                update(DocumentEmbeddingCache)
                .where(
                    DocumentEmbeddingCache.id.in_(identifiers),
                    DocumentEmbeddingCache.lease_token == self.token,
                )
                .values(lease_token=None, lease_expires_at=None)
            )

    def _refresh_references(self, identifiers):
        # Expiry recovery may remove a dispatched worker's references while its
        # durable inference fence survives. Reattach the completed results before
        # returning them, so publication and later source cleanup share ownership.
        now = SafeDateTime.now()
        with DbSession.atomic() as db:
            entries = db.exec(SqlBuilder.select.table(DocumentEmbeddingCache).where(
                DocumentEmbeddingCache.id.in_(identifiers),
            ).order_by(DocumentEmbeddingCache.id).with_for_update().execution_options(populate_existing=True)).all()
            if len(entries) != len(set(identifiers)) or any(entry.vector is None for entry in entries):
                raise EmbeddingCacheUnavailable("Embedding cache results expired before publication")
            references = db.exec(SqlBuilder.select.table(DocumentEmbeddingCacheReference).where(
                DocumentEmbeddingCacheReference.cache_id.in_(identifiers),
                DocumentEmbeddingCacheReference.use_token == self.token,
            )).all()
            referenced = {ref.cache_id for ref in references}
            for entry in entries:
                if entry.id not in referenced:
                    db.insert(DocumentEmbeddingCacheReference(
                        cache_id=entry.id, use_token=self.token,
                        expires_at=now + timedelta(hours=PENDING_REFERENCE_HOURS),
                    ))
            db.exec(update(DocumentEmbeddingCacheReference).where(
                DocumentEmbeddingCacheReference.cache_id.in_(identifiers),
                DocumentEmbeddingCacheReference.use_token == self.token,
                DocumentEmbeddingCacheReference.expires_at.is_not(None),
            ).values(expires_at=now + timedelta(hours=PENDING_REFERENCE_HOURS)))

    def _renew_claims(self, identifiers):
        with DbSession.atomic() as db:
            changed = db.exec(
                update(DocumentEmbeddingCache)
                .where(
                    DocumentEmbeddingCache.id.in_(identifiers),
                    DocumentEmbeddingCache.lease_token == self.token,
                    DocumentEmbeddingCache.lease_expires_at > SafeDateTime.now(),
                )
                .values(lease_expires_at=SafeDateTime.now() + timedelta(seconds=self.inference_budget_seconds))
            )
            if changed != len(identifiers):
                raise EmbeddingCacheUnavailable("Inference lease no longer belongs to this attempt")

    def _begin_inference(self, identifiers):
        # NULL expiry with a token is a durable dispatched/unknown outcome.
        # It must survive process loss, reference expiry and arbitrary suspension:
        # the provider has no contract for recovering or deduplicating this POST.
        with DbSession.atomic() as db:
            changed = db.exec(
                update(DocumentEmbeddingCache)
                .where(
                    DocumentEmbeddingCache.id.in_(identifiers),
                    DocumentEmbeddingCache.lease_token == self.token,
                    DocumentEmbeddingCache.lease_expires_at > SafeDateTime.now(),
                )
                .values(lease_expires_at=None)
            )
            if changed != len(identifiers):
                raise EmbeddingCacheUnavailable("Inference lease no longer belongs to this attempt")

    def embed_documents(self, texts):
        steps = self._embedding_steps(texts)
        result = None
        while True:
            try:
                operation, values = steps.send(result)
            except StopIteration as done:
                return done.value
            try:
                if operation == "embed":
                    result = self.embeddings.embed_documents(values)
                else:
                    time.sleep(values)
                    result = None
            except BaseException as error:
                steps.throw(error)
                raise

    async def aembed_documents(self, texts):
        steps = self._embedding_steps(texts)
        result = None
        while True:
            try:
                operation, values = steps.send(result)
            except StopIteration as done:
                return done.value
            try:
                if operation == "embed":
                    result = await self.embeddings.aembed_documents(values)
                else:
                    await asyncio.sleep(values)
                    result = None
            except BaseException as error:
                steps.throw(error)
                raise

    def _embedding_steps(self, texts):
        if not isinstance(texts, list) or any(not isinstance(text, str) for text in texts):
            raise ValueError("Embedding input must be a list of strings")
        if not texts:
            return []
        if all(text in self._ready for text in texts):
            return [self._ready[text] for text in texts]
        unique = {self._key(text): text for text in texts}
        entries, vectors, owned = {}, {}, {}
        attempted = set()
        try:
            for key in sorted(unique):
                identifier, vector, claimed = self._claim(key)
                entries[key] = identifier
                if vector is not None:
                    vectors[key] = vector
                elif claimed:
                    owned[key] = identifier
                if len(entries) % INFERENCE_BATCH_SIZE == 0 and owned:
                    self._renew_claims(list(owned.values()))
            # Publish our disjoint claims before waiting for another worker.
            # This prevents overlapping batches from waiting on each other.
            claims = list(owned.items())
            for offset in range(0, len(claims), INFERENCE_BATCH_SIZE):
                remaining = claims[offset:]
                self._renew_claims([identifier for _, identifier in remaining])
                batch = remaining[:INFERENCE_BATCH_SIZE]
                identifiers = [identifier for _, identifier in batch]
                self._begin_inference(identifiers)
                attempted.update(identifiers)
                generated = yield "embed", [unique[key] for key, _ in batch]
                with DbSession.atomic() as db:
                    for (key, identifier), vector in zip(batch, generated, strict=True):
                        changed = db.exec(
                            update(DocumentEmbeddingCache)
                            .where(
                                DocumentEmbeddingCache.id == identifier,
                                DocumentEmbeddingCache.lease_token == self.token,
                                DocumentEmbeddingCache.lease_expires_at.is_(None),
                            )
                            .values(vector=vector, lease_token=None, lease_expires_at=None)
                        )
                        if changed != 1:
                            raise EmbeddingCacheUnavailable("Inference lease no longer belongs to this attempt")
                        vectors[key] = vector
                    self._refresh_references(identifiers)
            deadline = time.monotonic() + self.inference_budget_seconds
            while len(vectors) < len(unique):
                with DbSession.use(readonly=False) as db:
                    rows = db.exec(
                        SqlBuilder.select.table(DocumentEmbeddingCache)
                        .where(
                            DocumentEmbeddingCache.id.in_([entries[key] for key in unique if key not in vectors]),
                        )
                        .execution_options(populate_existing=True)
                    ).all()
                by_id = {entry.id: entry for entry in rows}
                for key in unique.keys() - vectors.keys():
                    entry = by_id.get(entries[key])
                    if entry is None or (entry.vector is None and entry.lease_token is None):
                        raise EmbeddingCacheUnavailable("Concurrent inference did not complete")
                    if entry.vector is not None:
                        vectors[key] = entry.vector
                if len(vectors) == len(unique):
                    break
                if time.monotonic() >= deadline:
                    raise EmbeddingInferenceUncertain()
                yield "wait", 0.1

            from langchain_core.embeddings import Embeddings

            # Validate cached results too; corrupt rows cannot reach a vector index.
            class CachedResults(Embeddings):
                def embed_documents(self, values):
                    return [vectors[self_key(value)] for value in values]

                def embed_query(self, text):
                    raise NotImplementedError

            self_key = self._key
            result = validated_embeddings(CachedResults(), self.namespace[-1]).embed_documents(texts)
            self._refresh_references(list(entries.values()))
            self._ready.update(zip(texts, result, strict=True))
            return result
        except BaseException as error:
            from openai import APIStatusError

            # Explicit client rejection has no inference result to reconcile.
            # Timeout/conflict/server errors remain ambiguous across providers.
            if isinstance(error, APIStatusError) and error.status_code in {400, 401, 403, 404, 413, 422, 429}:
                attempted.clear()
            self.uncertain = bool(attempted) or bool(vectors)
            undispatched = set(owned.values()) - attempted
            if undispatched:
                self._release_claims(list(undispatched))
            if attempted:
                raise EmbeddingInferenceUncertain() from error
            raise

    async def aembed_query(self, text):
        return await self.embeddings.aembed_query(text)

    def embed_query(self, text):
        # User queries are never persisted in an attachment-content cache.
        return self.embeddings.embed_query(text)


@contextmanager
def cached_document_embeddings(embeddings, **kwargs):
    cache = CachedDocumentEmbeddings(embeddings, **kwargs)
    try:
        yield cache
    except BaseException:
        # Successful inference can precede a failed vector-store write. Keep its
        # pending references for bounded retry reuse; the normal expiry worker
        # removes abandoned results. No attachment pointer is published here.
        if cache._ready:
            cache.uncertain = True
        raise
    finally:
        if not cache.retained and not cache.uncertain:
            release_cache_references(cache.token)
