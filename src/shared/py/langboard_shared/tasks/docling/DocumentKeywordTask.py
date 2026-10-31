"""Durable, source-fenced keyword projections; recovery scans due work only."""

from datetime import timedelta
from hashlib import sha256
from json import dumps
from uuid import uuid4
from sqlalchemy import update
from ...core.broker import Broker
from ...core.db import DbSession, SqlBuilder
from ...core.types import SafeDateTime
from ...domain.models import Card, CardAttachment, CardMetadata, DocumentKeywordIndex, Project, ProjectColumn
from ...domain.services import DomainService
from ...domain.services.factory.SecretReferenceService import SecretReferenceUnavailable
from ...domain.services.KeywordSearchSettings import configuration, index_configuration, runtime_options
from ...helpers import InfraHelper


TASK_NAME = "langboard.document_keyword_index"
LEASE_SECONDS = 120


def configured_destination() -> dict[str, str]:
    config = index_configuration()
    return {key: config[key] for key in ("endpoint", "index")}


def fingerprint(destination: dict[str, str]) -> str:
    return sha256(dumps(destination, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def prepare_index(db, card: Card, attachment_uid: str, generation: str) -> str:
    """Capture an upload/request destination, waiting for that transcription only."""
    config = index_configuration()
    destination = {key: config[key] for key in ("endpoint", "index")}
    attachment_id = InfraHelper.convert_id(attachment_uid)
    current = db.exec(
        SqlBuilder.select.table(Card)
        .where(Card.id == card.id, Card.project_id == card.project_id, Card.deleted_at.is_(None))
        .with_for_update()
    ).first()
    attachment = db.exec(
        SqlBuilder.select.column(CardAttachment.id)
        .where(
            CardAttachment.id == attachment_id,
            CardAttachment.card_id == card.id,
            CardAttachment.deleted_at.is_(None),
        )
        .with_for_update()
    ).first()
    if current is None or attachment is None:
        raise ValueError("Keyword source is unavailable")
    destination_id = fingerprint(destination)
    job = db.exec(
        SqlBuilder.select.table(DocumentKeywordIndex)
        .where(
            DocumentKeywordIndex.attachment_id == attachment_id,
            DocumentKeywordIndex.destination_fingerprint == destination_id,
        )
        .with_for_update()
    ).first()
    if job is None:
        job = DocumentKeywordIndex(
            attachment_id=attachment_id,
            card_id=card.id,
            project_id=card.project_id,
            destination_fingerprint=destination_id,
            destination=destination,
            connection_id=config["connection_id"],
            source_generation=generation,
        )
        db.insert(job)
    else:
        job.connection_id = config["connection_id"]
        job.card_id = card.id
        job.project_id = card.project_id
        job.version += 1
        job.source_generation = generation
        job.operation = "index"
        job.due_at = None
        job.claim_token = None
        job.attempt_count = 0
        job.last_error = None
        db.update(job)
    return job.get_uid()


def activate_index(db, card: Card, attachment_uid: str, generation: str, job_uid: str) -> None:
    job = db.exec(
        SqlBuilder.select.table(DocumentKeywordIndex)
        .where(
            DocumentKeywordIndex.id == InfraHelper.convert_id(job_uid),
            DocumentKeywordIndex.card_id == card.id,
            DocumentKeywordIndex.attachment_id == InfraHelper.convert_id(attachment_uid),
            DocumentKeywordIndex.source_generation == generation,
            DocumentKeywordIndex.operation == "index",
        )
        .with_for_update()
    ).first()
    if job is None:
        return
    job.version += 1
    job.due_at = SafeDateTime.now()
    job.claim_token = None
    job.last_error = None
    db.update(job)
    db.after_commit(lambda: enqueue(int(job.id)))


def record_removal(db, card: Card | int, attachment_uid: str, *, dispatch: bool = True) -> None:
    _record_removal(
        db,
        (
            DocumentKeywordIndex.card_id == InfraHelper.convert_id(card),
            DocumentKeywordIndex.attachment_id == InfraHelper.convert_id(attachment_uid),
        ),
        dispatch=dispatch,
    )


def record_scope_removal(db, project: Project, card: Card | None = None) -> None:
    scope = (DocumentKeywordIndex.project_id == project.id,)
    if card is not None:
        scope += (DocumentKeywordIndex.card_id == card.id,)
    _record_removal(db, scope, dispatch=False)


def _record_removal(db, scope: tuple, *, dispatch: bool) -> None:
    identifiers = (
        db.exec(SqlBuilder.select.column(DocumentKeywordIndex.id).where(*scope).limit(100)).all() if dispatch else []
    )
    db.exec(
        update(DocumentKeywordIndex)
        .where(*scope)
        .values(
            version=DocumentKeywordIndex.version + 1,
            operation="remove",
            due_at=SafeDateTime.now(),
            claim_token=None,
            last_error=None,
        )
    )
    for identifier in identifiers:
        value = identifier[0] if isinstance(identifier, tuple) else identifier
        db.after_commit(lambda value=int(value): enqueue(value))


def enqueue(job_id: int) -> bool:
    try:
        Broker.celery.send_task(TASK_NAME, args=[job_id])
        return True
    except Exception:
        Broker.logger.warning("Keyword index dispatch unavailable; committed request retained")
        return False


def recover_keyword(*, limit: int = 100) -> int:
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Keyword recovery limit must be between 1 and 100")
    with DbSession.use(readonly=False) as db:
        jobs = db.exec(
            SqlBuilder.select.table(DocumentKeywordIndex)
            .where(DocumentKeywordIndex.due_at <= SafeDateTime.now())
            .order_by(DocumentKeywordIndex.updated_at, DocumentKeywordIndex.due_at, DocumentKeywordIndex.id)
            .limit(limit)
        ).all()
    count = 0
    for job in jobs:
        if not enqueue(int(job.id)):
            break
        # Rotate accepted-but-unconsumed deliveries without changing the
        # current source version or the worker's due-time lease.
        with DbSession.atomic() as db:
            db.exec(
                update(DocumentKeywordIndex)
                .where(
                    DocumentKeywordIndex.id == job.id,
                    DocumentKeywordIndex.version == job.version,
                    DocumentKeywordIndex.due_at == job.due_at,
                )
                .values(updated_at=SafeDateTime.now())
            )
        count += 1
    return count


def _payload(job: DocumentKeywordIndex) -> dict | None:
    base = {
        "board_uid": job.project_id.to_short_code(),
        "card_uid": job.card_id.to_short_code(),
        "attachment_uid": job.attachment_id.to_short_code(),
        "active": False,
        "projection_hash": "",
    }
    if job.operation == "remove":
        return base
    service = DomainService()
    try:
        with DbSession.atomic() as db:
            card = db.exec(SqlBuilder.select.table(Card).where(Card.id == job.card_id).with_for_update()).first()
            current = db.exec(
                SqlBuilder.select.table(DocumentKeywordIndex).where(DocumentKeywordIndex.id == job.id)
            ).first()
            if current is None or current.version != job.version or current.claim_token != job.claim_token:
                return None
            project = db.exec(SqlBuilder.select.table(Project).where(Project.id == job.project_id)).first()
            attachment = db.exec(
                SqlBuilder.select.table(CardAttachment).where(CardAttachment.id == job.attachment_id)
            ).first()
            column = (
                db.exec(
                    SqlBuilder.select.table(ProjectColumn).where(ProjectColumn.id == card.project_column_id)
                ).first()
                if card
                else None
            )
            if (
                card is None
                or card.deleted_at
                or card.project_id != job.project_id
                or project is None
                or project.deleted_at
                or attachment is None
                or attachment.deleted_at
                or attachment.card_id != job.card_id
                or column is None
                or column.deleted_at
                or column.project_id != job.project_id
            ):
                return base
            document = service.docling_metadata.get_document_by_attachment_uid(
                CardMetadata, card, job.attachment_id.to_short_code()
            )
            if not document:
                return base
            from ...domain.services.factory.DoclingMetadataService import DoclingMetadataService

            document = DoclingMetadataService.published_document(document)
            if (
                document.get("generation") != job.source_generation
                or document.get("status") != "indexed"
                or not document.get("content_hash")
            ):
                return None
            text = attachment.document_text
            if not text or len(text) > 131072:
                raise ValueError("Keyword projection requires explicit processing")
            return {**base, "active": True, "projection_hash": sha256(text.encode()).hexdigest(), "text": text}
    finally:
        service.close()


def _send(job: DocumentKeywordIndex, payload: dict) -> None:
    config = (
        configuration(operation=job.operation, connection_id=job.connection_id)
        if job.connection_id
        else {
            **job.destination,
            "legacy": True,
            "connection_id": None,
        }
    )
    if config is None:
        raise ValueError("Original keyword connection is required")
    destination = {key: config[key] for key in ("endpoint", "index")}
    if destination != job.destination or fingerprint(destination) != job.destination_fingerprint:
        raise ValueError("Original keyword connection is required")
    options = runtime_options(config, operation=job.operation)
    from elasticsearch import ConflictError, Elasticsearch

    with Elasticsearch(**options) as client:
        target = client.indices.resolve_index(name=destination["index"], expand_wildcards="none")
        if (
            target.get("aliases")
            or target.get("data_streams")
            or [entry.get("name") for entry in target.get("indices", [])] != [destination["index"]]
        ):
            raise ValueError("A pre-provisioned concrete keyword index is required")
        try:
            client.index(
                index=destination["index"],
                id=job.attachment_id.to_short_code(),
                document=payload,
                version=job.version,
                version_type="external",
                refresh="wait_for",
            )
        except ConflictError:
            result = client.get(
                index=destination["index"],
                id=job.attachment_id.to_short_code(),
                source_includes=["active", "board_uid", "card_uid", "attachment_uid", "projection_hash"],
            )
            expected = {key: value for key, value in payload.items() if key != "text"}
            if result.get("_version") != job.version or result.get("_source") != expected:
                raise ValueError("Keyword version conflict requires reconciliation") from None


def process_keyword(job_id: int) -> bool:
    now = SafeDateTime.now()
    token = uuid4().hex
    with DbSession.atomic() as db:
        if not db.exec(
            update(DocumentKeywordIndex)
            .where(DocumentKeywordIndex.id == job_id, DocumentKeywordIndex.due_at <= now)
            .values(
                due_at=now + timedelta(seconds=LEASE_SECONDS),
                claim_token=token,
                attempt_count=DocumentKeywordIndex.attempt_count + 1,
            )
        ):
            return False
        job = db.exec(SqlBuilder.select.table(DocumentKeywordIndex).where(DocumentKeywordIndex.id == job_id)).first()
    fence = (
        DocumentKeywordIndex.id == job_id,
        DocumentKeywordIndex.version == job.version,
        DocumentKeywordIndex.claim_token == token,
    )
    try:
        attempt_configuration = (
            configuration(operation=job.operation, connection_id=job.connection_id) if job.connection_id else None
        )
        payload = _payload(job)
        if payload is not None and job.operation == "index" and payload["active"] is False:
            # A new version fences an earlier in-flight index write, including retries.
            with DbSession.atomic() as db:
                db.exec(
                    update(DocumentKeywordIndex)
                    .where(*fence)
                    .values(version=job.version + 1, operation="remove", due_at=SafeDateTime.now(), claim_token=None)
                )
            return False
        if payload is not None:
            _send(job, payload)
    except Exception:
        with DbSession.atomic() as db:
            db.exec(
                update(DocumentKeywordIndex)
                .where(*fence)
                .values(
                    due_at=SafeDateTime.now() + timedelta(seconds=min(3600, 2 ** min(job.attempt_count, 12))),
                    claim_token=None,
                    last_error="keyword_index_failed_check_original_connection",
                )
            )
        return False
    with DbSession.atomic() as db:
        if job.connection_id is not None:
            try:
                current_configuration = configuration(
                    operation=job.operation, connection_id=job.connection_id, lock=True
                )
            except (ValueError, SecretReferenceUnavailable):
                current_configuration = None
            if current_configuration != attempt_configuration:
                db.exec(
                    update(DocumentKeywordIndex)
                    .where(*fence)
                    .values(
                        due_at=SafeDateTime.now() + timedelta(seconds=1),
                        claim_token=None,
                        last_error="keyword_index_failed_check_original_connection",
                    )
                )
                return False
        finished = bool(
            db.exec(
                update(DocumentKeywordIndex)
                .where(*fence)
                .values(
                    completed_version=job.version if payload is not None else job.completed_version,
                    due_at=None,
                    claim_token=None,
                    last_error=None if payload is not None else "source_superseded",
                )
            )
        )
    return finished and payload is not None


@Broker.wrap_async_task_decorator({"name": TASK_NAME, "acks_late": True, "reject_on_worker_lost": True})
async def document_keyword_index_task(job_id: int):
    process_keyword(job_id)


if __name__ == "__main__":
    recover_keyword()
