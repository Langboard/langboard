"""Share private conversion work using short transactions and fenced leases."""

import re
import time
from contextlib import contextmanager
from copy import deepcopy
from datetime import timedelta
from hashlib import sha256
from importlib import metadata
from json import dumps, loads
from math import isfinite
from pathlib import PurePath
from urllib.parse import urlsplit
from uuid import uuid4
from sqlalchemy import delete, exists, or_, update
from sqlalchemy.exc import IntegrityError
from ...core.db import DbSession, SqlBuilder
from ...core.types import SafeDateTime
from ...domain.models.DocumentExtractionCache import DocumentExtractionCache as Cache
from ...domain.models.DocumentExtractionCache import DocumentExtractionCacheReference as Reference
from .DocumentKeywords import keyword_languages, normalize_keywords


CONVERSION_VERSION = "v1"
MAX_OUTPUT_BYTES = 8 * 1024 * 1024
PENDING_REFERENCE_HOURS = 8
VISION_KEYS = {"binding_uid", "base_url", "model_name", "model", "reasoning_effort", "top_p", "keyword_languages"}


class ExtractionCacheUnavailable(RuntimeError):
    pass


class PublishedExtractionUnavailable(ValueError):
    pass


def _token(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
        raise ValueError("Invalid server-owned extraction reference")


def _source(source_type, source_uid, generation=None):
    if source_type not in {"card_attachment", "project_attachment", "wiki_attachment"}:
        raise ValueError("Invalid extraction source type")
    if not isinstance(source_uid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", source_uid):
        raise ValueError("Invalid server-owned extraction source")
    if generation is not None:
        _token(generation)


def _lock_entry(db, identifier):
    return db.exec(
        SqlBuilder.select.table(Cache)
        .where(Cache.id == identifier)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).first()


def _remove_unreferenced(db, identifiers):
    now = SafeDateTime.now()
    for identifier in sorted(set(identifiers)):
        if _lock_entry(db, identifier) is None:
            continue
        db.exec(
            update(Cache)
            .execution_options(synchronize_session=False)
            .where(Cache.id == identifier, Cache.lease_expires_at <= now)
            .values(lease_token=None, lease_expires_at=None)
        )
        # The predicate also fences rows on SQLite, where FOR UPDATE is ignored.
        db.exec(
            delete(Cache)
            .execution_options(synchronize_session=False)
            .where(Cache.id == identifier, Cache.lease_token.is_(None), ~exists().where(Reference.cache_id == Cache.id))
        )


def _compatible_published_cache_ids(db, content_hash, filename, vision_config, embedding_config):
    """Use committed source provenance; API images need not contain worker parsers."""
    from ...domain.models import (
        CardAttachment,
        CardMetadata,
        ProjectAttachment,
        ProjectAttachmentDocument,
        ProjectWikiAttachment,
        WikiAttachmentDocument,
    )
    from ...domain.services.factory.DoclingMetadataService import DoclingMetadataService
    from ...domain.services.factory.ProjectDocumentService import ProjectDocumentService
    from ...domain.services.factory.WikiDocumentService import WikiDocumentService
    from ...helpers import InfraHelper
    from .DocumentEmbeddingCache import CHUNK_VERSION

    identifiers = set()
    embedding_tokens = set()

    def retain(reference, embedding):
        identifiers.add(reference.cache_id)
        pointer = embedding.get("pointer") or {}
        if (isinstance(embedding_config, dict) and embedding.get("status") == "indexed"
                and embedding.get("source_generation") == reference.generation
                and embedding.get("config") == embedding_config and embedding.get("chunk_version") == CHUNK_VERSION
                and pointer.get("content_hash") == content_hash):
            token = (pointer.get("storage") or {}).get("cache_use_token")
            if isinstance(token, str) and re.fullmatch(r"[0-9a-f]{32}", token):
                embedding_tokens.add(token)

    for source_type, model, document_model, service in (
        ("project_attachment", ProjectAttachment, ProjectAttachmentDocument, ProjectDocumentService),
        ("wiki_attachment", ProjectWikiAttachment, WikiAttachmentDocument, WikiDocumentService),
        ("card_attachment", CardAttachment, CardMetadata, None),
    ):
        active = SqlBuilder.select.table(Reference).where(
            Reference.source_type == source_type, Reference.expires_at.is_(None),
        )
        after_id = 0
        while True:
            references = db.exec(active.where(Reference.id > after_id).order_by(Reference.id).limit(100)).all()
            if not references:
                break
            attachments = db.exec(SqlBuilder.select.table(model).where(
                model.id.in_([InfraHelper.convert_id(ref.source_uid) for ref in references]),
                *([model.content_sha256 == content_hash] if service is not None else []),
            )).all()
            compatible = [row for row in attachments if service is None
                          or PurePath(row.filename).suffix.lower() == PurePath(filename).suffix.lower()]
            by_uid = {row.get_uid(): row for row in compatible}
            documents_by_attachment = {}
            metadata_by_card = {}
            if compatible:
                if service is not None:
                    documents_by_attachment = {document.attachment_id: document for document in db.exec(
                        SqlBuilder.select.table(document_model).where(
                            document_model.attachment_id.in_([row.id for row in compatible]),
                        )
                    ).all()}
                else:
                    metadata_by_card = {row.card_id: row for row in db.exec(
                        SqlBuilder.select.table(CardMetadata).where(
                            CardMetadata.card_id.in_([row.card_id for row in compatible]),
                            CardMetadata.key == "__system.docling_documents",
                        )
                    ).all()}
            for reference in references:
                if reference.source_uid not in by_uid:
                    continue
                attachment = by_uid[reference.source_uid]
                if service is not None:
                    document = documents_by_attachment.get(attachment.id)
                    if (document is not None and document.status == "indexed"
                            and document.generation == reference.generation and document.vision_config == vision_config
                            and document.source_revision == service.source_revision(attachment)):
                        retain(reference, document.embedding or {})
                else:
                    metadata_row = metadata_by_card.get(attachment.card_id)
                    try:
                        documents = loads(metadata_row.value) if metadata_row else []
                    except (ValueError, TypeError):
                        documents = []
                    for document in documents if isinstance(documents, list) else []:
                        if not isinstance(document, dict):
                            continue
                        document = DoclingMetadataService.published_document(document)
                        published_filename = document.get("filename") or attachment.filename
                        if (document.get("attachment_uid") == reference.source_uid and document.get("status") == "indexed"
                                and document.get("generation") == reference.generation
                                and document.get("content_hash") == content_hash
                                and PurePath(published_filename).suffix.lower() == PurePath(filename).suffix.lower()
                                and document.get("vision_config") == vision_config and not document.get("location_invalidated")):
                            retain(reference, document.get("embedding") or {})
            if len(references) < 100:
                break
            after_id = references[-1].id
    return identifiers, embedding_tokens


def reserve_document_references(
    db, *, source_type, source_uid, generation, content_hash, filename, vision_config, embedding_config=None,
    previous_token=None,
):
    """Own completed source results at acceptance; workers still fence parser versions."""
    _source(source_type, source_uid, generation)
    if not content_hash or not isinstance(vision_config, dict) or not vision_config.get("binding_uid"):
        return False
    candidates, embedding_tokens = _compatible_published_cache_ids(
        db, content_hash, filename, vision_config, embedding_config,
    )
    previous = db.exec(SqlBuilder.select.table(Reference).where(
        Reference.source_type == source_type, Reference.source_uid == source_uid,
    )).all()
    locked = {identifier: _lock_entry(db, identifier)
              for identifier in sorted(candidates | {ref.cache_id for ref in previous})}
    for identifier in sorted(candidates):
        entry = locked[identifier]
        if entry is None or entry.markdown is None:
            continue
        db.insert(Reference(
            cache_id=entry.id, use_token=generation, source_type=source_type, source_uid=source_uid,
            generation=generation, expires_at=SafeDateTime.now() + timedelta(hours=PENDING_REFERENCE_HOURS),
        ))
    if isinstance(embedding_config, dict) or previous_token is not None:
        from .DocumentEmbeddingCache import reserve_cache_references

        reserve_cache_references(db, token=generation, source_tokens=embedding_tokens, previous_token=previous_token)
    return bool(candidates)


def release_document_reservation(db, source_type, source_uid, generation, *, embedding_config=None):
    """Release only this request's bounded use, preserving published generations."""
    _source(source_type, source_uid)
    if not isinstance(generation, str) or not re.fullmatch(r"[0-9a-f]{32}", generation):
        return  # Historical generations never acquired an acceptance reservation.
    source_references = db.exec(SqlBuilder.select.table(Reference).where(
        Reference.source_type == source_type, Reference.source_uid == source_uid,
    )).all()
    references = [ref for ref in source_references if ref.use_token == generation and ref.expires_at is not None]
    for identifier in sorted({ref.cache_id for ref in source_references}):
        _lock_entry(db, identifier)
    db.exec(delete(Reference).execution_options(synchronize_session=False).where(
        Reference.use_token == generation, Reference.source_type == source_type, Reference.source_uid == source_uid,
        Reference.expires_at.is_not(None),
    ))
    _remove_unreferenced(db, [ref.cache_id for ref in references])
    if references and isinstance(embedding_config, dict):
        from .DocumentEmbeddingCache import release_cache_references

        release_cache_references(generation)


def transfer_document_reservation(db, source_type, source_uid, previous, generation, *, embedding_config=None):
    """Card worker claims rotate generation; transfer acceptance ownership with it."""
    _source(source_type, source_uid, generation)
    changed = db.exec(update(Reference).execution_options(synchronize_session=False).where(
        Reference.use_token == previous, Reference.source_type == source_type, Reference.source_uid == source_uid,
        Reference.expires_at.is_not(None),
    ).values(use_token=generation, generation=generation))
    if changed and isinstance(embedding_config, dict):
        from ...domain.models.DocumentEmbeddingCache import DocumentEmbeddingCacheReference

        db.exec(update(DocumentEmbeddingCacheReference).execution_options(synchronize_session=False).where(
            DocumentEmbeddingCacheReference.use_token == previous,
            DocumentEmbeddingCacheReference.expires_at.is_not(None),
        ).values(use_token=generation))


def activate_extraction_reference(db, token, *, source_type, source_uid, generation):
    """Activate only an existing valid use inside native source publication."""
    _token(token)
    _source(source_type, source_uid, generation)
    if generation is None:
        raise ValueError("An authoritative extraction generation is required")
    now = SafeDateTime.now()
    reference = db.exec(SqlBuilder.select.table(Reference).where(Reference.use_token == token)).first()
    if reference is None:
        return False
    old = db.exec(
        SqlBuilder.select.table(Reference).where(
            Reference.source_type == source_type, Reference.source_uid == source_uid, Reference.use_token != token
        )
    ).all()
    entries = {
        identifier: _lock_entry(db, identifier)
        for identifier in sorted({reference.cache_id, *(ref.cache_id for ref in old)})
    }
    entry = entries[reference.cache_id]
    if entry is None or entry.markdown is None:
        return False
    valid = or_(
        Reference.expires_at > now,
        (
            Reference.expires_at.is_(None)
            & (Reference.source_type == source_type)
            & (Reference.source_uid == source_uid)
            & (Reference.generation == generation)
        ),
    )
    changed = db.exec(
        update(Reference)
        .execution_options(synchronize_session=False)
        .where(Reference.id == reference.id, valid)
        .values(expires_at=None, source_type=source_type, source_uid=source_uid, generation=generation)
    )
    if changed != 1:
        return False
    db.exec(
        delete(Reference)
        .execution_options(synchronize_session=False)
        .where(
            Reference.source_type == source_type, Reference.source_uid == source_uid, Reference.use_token != token,
            or_(Reference.use_token != generation, Reference.expires_at.is_(None)),
        )
    )
    _remove_unreferenced(db, [ref.cache_id for ref in old])
    return True


def published_extraction_identity(source_type, source_uid, generation):
    """Read the exact committed conversion identity, never today's parser settings."""
    _source(source_type, source_uid, generation)
    if generation is None:
        raise ValueError("An authoritative extraction generation is required")
    with DbSession.use(readonly=False) as db:
        entries = db.exec(
            SqlBuilder.select.table(Cache)
            .join(Reference, Reference.cache_id == Cache.id)
            .where(
                Reference.source_type == source_type,
                Reference.source_uid == source_uid,
                Reference.generation == generation,
                Reference.expires_at.is_(None),
                Cache.markdown.is_not(None),
            )
        ).all()
        if len(entries) != 1:
            raise PublishedExtractionUnavailable(
                "Published extraction identity unavailable; explicitly reprocess this attachment"
            )
        return entries[0].cache_key


def release_extraction_source(db, source_type, source_uid):
    """Release a deleted source while preserving every other active or pending use."""
    _source(source_type, source_uid)
    references = db.exec(
        SqlBuilder.select.table(Reference).where(
            Reference.source_type == source_type, Reference.source_uid == source_uid
        )
    ).all()
    for identifier in sorted({ref.cache_id for ref in references}):
        _lock_entry(db, identifier)
    removed = db.exec(
        delete(Reference)
        .execution_options(synchronize_session=False)
        .where(Reference.source_type == source_type, Reference.source_uid == source_uid)
    )
    _remove_unreferenced(db, [ref.cache_id for ref in references])
    return removed


def recover_expired_extraction_references(*, limit=100):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Extraction cleanup limit must be between 1 and 100")
    now = SafeDateTime.now()
    with DbSession.atomic() as db:
        references = db.exec(
            SqlBuilder.select.table(Reference)
            .where(Reference.expires_at <= now)
            .order_by(Reference.expires_at)
            .limit(limit)
        ).all()
        # Discover the whole lock set before acquiring any row lock. Publication
        # can exchange old/new cache rows, so every path uses the same ordering.
        expired = db.exec(
            SqlBuilder.select.table(Cache)
            .where(Cache.lease_expires_at <= now)
            .order_by(Cache.lease_expires_at)
            .limit(limit)
        ).all()
        identifiers = sorted({*(ref.cache_id for ref in references), *(entry.id for entry in expired)})
        for identifier in identifiers:
            _lock_entry(db, identifier)
        removed = 0
        if references:
            removed = db.exec(
                delete(Reference)
                .execution_options(synchronize_session=False)
                .where(Reference.id.in_([ref.id for ref in references]), Reference.expires_at <= now)
            )
        _remove_unreferenced(db, identifiers)
        return removed


class CachedDocumentExtraction:
    def __init__(self, *, binding_uid, content_hash, vision_config, filename, timeout_seconds, on_validate=None):
        if not isinstance(binding_uid, str) or not binding_uid:
            raise ValueError("An authoritative vision binding is required")
        if not isinstance(content_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", content_hash):
            raise ValueError("An authoritative content hash is required")
        if not isinstance(vision_config, dict) or set(vision_config) - VISION_KEYS:
            raise ValueError("Only credential-free vision snapshot fields are allowed")
        for key, value in vision_config.items():
            if key == "keyword_languages":
                continue
            if key == "top_p":
                if value is not None and (type(value) not in (int, float) or not isfinite(value)):
                    raise ValueError("Invalid vision sampling snapshot")
            elif value is not None and not isinstance(value, str):
                raise ValueError("Vision snapshot fields must be scalar values")
        base_url = vision_config.get("base_url")
        if base_url:
            parsed = urlsplit(base_url)
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError("Vision endpoint must not contain credentials or query parameters")
        if vision_config.get("binding_uid", binding_uid) != binding_uid:
            raise ValueError("Vision snapshot binding mismatch")
        if type(timeout_seconds) not in (int, float) or not isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("Conversion timeout must be positive and finite")
        if not isinstance(filename, str):
            raise ValueError("A source format is required")
        self.suffix = PurePath(filename).suffix.lower()
        self.languages = keyword_languages(vision_config)
        versions = []
        for distribution in ("docling", "docling-core", "docling-slim", "pillow", "pypdfium2", "lxml"):
            try:
                versions.append(metadata.version(distribution))
            except metadata.PackageNotFoundError:
                versions.append(None)
        self.key = sha256(
            dumps(
                [CONVERSION_VERSION, versions, binding_uid, content_hash, vision_config, self.suffix],
                sort_keys=True,
                allow_nan=False,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        self.timeout_seconds = timeout_seconds
        self.on_validate = on_validate
        self.token = uuid4().hex
        self.retained = False
        self.entry_id = None
        self._ready = False

    def retain(self):
        self.retained = True

    def _validate(self):
        if DbSession.has_active_transaction():
            raise RuntimeError("Extraction must run outside a source transaction")
        if self.on_validate is not None:
            self.on_validate()

    def _normalize(self, result):
        if not isinstance(result, dict) or not isinstance(result.get("markdown"), str):
            raise ValueError("Malformed document conversion result")
        markdown = result["markdown"]
        if len(markdown.encode()) > MAX_OUTPUT_BYTES:
            raise ValueError("Conversion markdown exceeds size limit")
        document = result.get("document_json")
        if document is not None:
            if not isinstance(document, dict) or document.get("schema_name") != "DoclingDocument":
                raise ValueError("Malformed document structure")
            if len(dumps(document, allow_nan=False).encode()) > MAX_OUTPUT_BYTES:
                raise ValueError("Conversion structure exceeds size limit")
            document = deepcopy(document)
            document["name"] = f"document{self.suffix}"
            if isinstance(document.get("origin"), dict):
                document["origin"]["filename"] = f"document{self.suffix}"
        return {
            "markdown": markdown,
            "keywords": normalize_keywords(result.get("keywords"), self.languages),
            "document_json": document,
        }

    def _claim(self):
        now = SafeDateTime.now()
        with DbSession.atomic() as db:
            query = SqlBuilder.select.table(Cache).where(Cache.cache_key == self.key)
            entry = db.exec(query.with_for_update().execution_options(populate_existing=True)).first()
            if entry is None:
                try:
                    with db.savepoint():
                        entry = Cache(cache_key=self.key)
                        db.insert(entry)
                except IntegrityError:
                    entry = db.exec(query.with_for_update().execution_options(populate_existing=True)).first()
                    if entry is None:
                        raise
            reference = db.exec(
                SqlBuilder.select.table(Reference).where(
                    Reference.cache_id == entry.id, Reference.use_token == self.token
                )
            ).first()
            if reference is None:
                db.insert(
                    Reference(
                        cache_id=entry.id,
                        use_token=self.token,
                        expires_at=now + timedelta(hours=PENDING_REFERENCE_HOURS),
                    )
                )
            self.entry_id = entry.id
            owned = False
            if entry.markdown is None:
                owned = (
                    db.exec(
                        update(Cache)
                        .execution_options(synchronize_session=False)
                        .where(
                            Cache.id == entry.id,
                            Cache.markdown.is_(None),
                            or_(Cache.lease_token.is_(None), Cache.lease_expires_at <= now),
                        )
                        .values(
                            lease_token=self.token,
                            lease_expires_at=now + timedelta(seconds=2 * self.timeout_seconds + 30),
                        )
                    )
                    == 1
                )
            ready = (
                None
                if entry.markdown is None
                else {"markdown": entry.markdown, "keywords": entry.keywords, "document_json": entry.document_json}
            )
            return ready, owned

    def get_or_convert(self, convert):
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            self._validate()
            ready, owned = self._claim()
            self._validate()
            if ready is not None:
                result = self._normalize(ready)
                self._validate()
                self._ready = True
                return result
            if owned:
                self._validate()
                result = self._normalize(convert())
                self._validate()
                with DbSession.atomic() as db:
                    changed = db.exec(
                        update(Cache)
                        .execution_options(synchronize_session=False)
                        .where(
                            Cache.id == self.entry_id,
                            Cache.lease_token == self.token,
                            Cache.lease_expires_at > SafeDateTime.now(),
                        )
                        .values(**result, lease_token=None, lease_expires_at=None)
                    )
                    if changed != 1:
                        raise ExtractionCacheUnavailable("Conversion lease no longer belongs to this attempt")
                self._validate()
                self._ready = True
                return deepcopy(result)
            if time.monotonic() >= deadline:
                raise ExtractionCacheUnavailable("Concurrent document conversion remains pending")
            time.sleep(min(0.1, max(0, deadline - time.monotonic())))
            self._validate()
            if time.monotonic() >= deadline:
                raise ExtractionCacheUnavailable("Concurrent document conversion remains pending")

    def _close(self):
        if self.entry_id is None:
            return
        with DbSession.atomic() as db:
            _lock_entry(db, self.entry_id)
            db.exec(
                update(Cache)
                .execution_options(synchronize_session=False)
                .where(Cache.id == self.entry_id, Cache.lease_token == self.token)
                .values(lease_token=None, lease_expires_at=None)
            )
            if not self.retained:
                # Activated references belong to native source state even when a later step fails.
                db.exec(
                    delete(Reference)
                    .execution_options(synchronize_session=False)
                    .where(Reference.use_token == self.token, Reference.expires_at.is_not(None))
                )
            _remove_unreferenced(db, [self.entry_id])


@contextmanager
def cached_document_extraction(**kwargs):
    cache = CachedDocumentExtraction(**kwargs)
    try:
        yield cache
    except BaseException:
        # Native publication can fail after validated conversion succeeds. Keep
        # its pending reference for bounded retry reuse; expiry cleanup removes
        # abandoned results without publishing an attachment or source pointer.
        if cache._ready:
            cache.retain()
        raise
    finally:
        cache._close()
