"""Transactional cleanup outbox; recover only due requests, never scan attachments."""

from datetime import timedelta
from uuid import uuid4
from sqlalchemy import delete, update
from ...core.broker import Broker
from ...core.db import DbSession, SqlBuilder
from ...core.types import SafeDateTime
from ...domain.models.DocumentVectorCleanup import DocumentVectorCleanup


TASK_NAME = "langboard.document_vector_cleanup"
LEASE_SECONDS = 120


def record_cleanup(db, pointer: dict | None, *, dispatch: bool = True) -> bool:
    """Called inside the transaction removing the last authoritative source pointer."""
    if not isinstance(pointer, dict) or not (pointer.get("chunk_ids") or pointer.get("namespace")):
        return False
    # Keep only server-owned source/connection identifiers, never provider credentials.
    recorded = {
        key: pointer[key]
        for key in (
            "source_type",
            "board_uid",
            "card_uid",
            "wiki_uid",
            "attachment_uid",
            "compaction_uid",
            "source_revision",
            "content_hash",
            "embedding_fingerprint",
            "generation",
            "chunk_count",
            "chunk_ids",
            "namespace",
        )
        if key in pointer
    }
    storage = pointer.get("storage")
    if isinstance(storage, dict):
        recorded["storage"] = {
            key: storage[key]
            for key in (
                "type",
                "endpoint",
                "binding_uid",
                "dimensions",
                "collection_prefix",
                "cache_use_token",
            )
            if key in storage
        }
    event = DocumentVectorCleanup(pointer=recorded)
    db.insert(event)
    if dispatch:
        db.after_commit(lambda: enqueue_cleanup(int(event.id)))
    return True


def enqueue_cleanup(event_id: int) -> bool:
    try:
        Broker.celery.send_task(TASK_NAME, args=[event_id])
        return True
    except Exception:
        Broker.logger.warning("Document cleanup dispatch unavailable; pending request retained")
        return False


def recover_cleanup(*, limit: int = 100) -> int:
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Cleanup recovery limit must be between 1 and 100")
    from .DocumentEmbeddingCache import recover_expired_cache_references
    from .DocumentExtractionCache import recover_expired_extraction_references

    recover_expired_cache_references(limit=limit)
    recover_expired_extraction_references(limit=limit)
    with DbSession.use(readonly=False) as db:
        pending = db.exec(
            SqlBuilder.select.table(DocumentVectorCleanup)
            .where(DocumentVectorCleanup.due_at <= SafeDateTime.now())
            .order_by(DocumentVectorCleanup.updated_at, DocumentVectorCleanup.due_at, DocumentVectorCleanup.id)
            .limit(limit)
        ).all()
    dispatched = 0
    for event in pending:
        if not enqueue_cleanup(int(event.id)):
            break
        # Broker acceptance is not a worker claim. Rotate the bounded prefix
        # without postponing eligibility or overwriting a worker's lease.
        with DbSession.atomic() as db:
            db.exec(
                update(DocumentVectorCleanup)
                .where(DocumentVectorCleanup.id == event.id, DocumentVectorCleanup.due_at == event.due_at)
                .values(updated_at=SafeDateTime.now())
            )
        dispatched += 1
    return dispatched


def process_cleanup(event_id: int) -> bool:
    from .DocumentEmbeddingTask import remove_recorded_embedding

    now = SafeDateTime.now()
    token = uuid4().hex
    with DbSession.atomic() as db:
        claimed = db.exec(
            update(DocumentVectorCleanup)
            .where(DocumentVectorCleanup.id == event_id, DocumentVectorCleanup.due_at <= now)
            .values(
                due_at=now + timedelta(seconds=LEASE_SECONDS),
                claim_token=token,
                attempt_count=DocumentVectorCleanup.attempt_count + 1,
            )
        )
        if not claimed:
            return False
        event = db.exec(
            SqlBuilder.select.table(DocumentVectorCleanup).where(DocumentVectorCleanup.id == event_id)
        ).first()
    # No database lock spans filesystem or external server work. Deletion is idempotent.
    try:
        remove_recorded_embedding(event.pointer)
    except Exception:
        with DbSession.atomic() as db:
            db.exec(
                update(DocumentVectorCleanup)
                .where(DocumentVectorCleanup.id == event_id, DocumentVectorCleanup.claim_token == token)
                .values(
                    claim_token=None,
                    due_at=SafeDateTime.now() + timedelta(seconds=min(3600, 15 * 2 ** min(event.attempt_count, 8))),
                    last_error="cleanup_failed_check_original_connection",
                )
            )
        return False
    with DbSession.atomic() as db:
        removed = db.exec(
            delete(DocumentVectorCleanup).where(
                DocumentVectorCleanup.id == event_id, DocumentVectorCleanup.claim_token == token
            )
        )
    return bool(removed)


@Broker.wrap_async_task_decorator({"name": TASK_NAME, "acks_late": True, "reject_on_worker_lost": True})
async def document_vector_cleanup_task(event_id: int):
    process_cleanup(event_id)


if __name__ == "__main__":
    recover_cleanup()
