"""Internal storage only; the application must authorize and collect current sources.

No returned record is a public projection. In particular, completed execution
does not prove current source validity or current reader permission.
"""

import json
from datetime import timedelta, timezone
from hashlib import sha256
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from ....ai.comment_compaction import CommentCompactionEvidence, CompactionDraft
from ....core.db import DbSession, SqlBuilder
from ....core.domain import BaseRepository
from ....core.types import SafeDateTime
from ....domain.models import Card, CardCommentCompaction, CardMetadata, InternalBot, User
from ....domain.models.InternalBot import InternalBotType
from ....domain.services.WorkflowCompactionHistory import finish_compaction_history, start_compaction_history


POLICY_VERSION = 1
FAILURE_CODES = {"source_changed", "permission_changed", "provider_changed", "provider_error", "invalid_result", "cancelled", "generation_failed", "provider_unconfigured"}


def provider_revision(binding: InternalBot) -> str:
    # Settings writes change updated_at. Do not hash or persist credential material.
    return sha256(f"{binding.get_uid()}:{_utc(binding.updated_at).isoformat()}".encode()).hexdigest()


def request_key(card: Card, user: User, binding: InternalBot, snapshot: CommentCompactionEvidence) -> str:
    return sha256(json.dumps([POLICY_VERSION, int(card.id), int(user.id), snapshot.source_revision(),
                             provider_revision(binding)]).encode()).hexdigest()


def automatic_request_key(card: Card, binding: InternalBot, snapshot: CommentCompactionEvidence) -> str:
    return sha256(json.dumps([POLICY_VERSION, int(card.id), ["profile", int(binding.id)], snapshot.source_revision(),
                             provider_revision(binding)]).encode()).hexdigest()


def _utc(value):
    # SQLite returns timezone-naive UTC; PostgreSQL returns timezone-aware values.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class CardCommentCompactionRepository(BaseRepository[CardCommentCompaction]):
    @staticmethod
    def model_cls():
        return CardCommentCompaction

    @staticmethod
    def name() -> str:
        return "card_comment_compaction"

    def reserve(self, card: Card, user: User, binding: InternalBot, snapshot: CommentCompactionEvidence) -> CardCommentCompaction:
        if not isinstance(user, User):
            raise ValueError("Manual compaction requires a user")
        return self._reserve(card, user.id, binding, snapshot, request_key(card, user, binding, snapshot))

    def reserve_automatic(self, card: Card, binding: InternalBot, snapshot: CommentCompactionEvidence) -> CardCommentCompaction:
        """Application must verify current board grant and workflow before this call."""
        if binding.bot_type != InternalBotType.CommentCompaction:
            raise ValueError("Automatic compaction requires a dedicated compactor")
        return self._reserve(card, None, binding, snapshot, automatic_request_key(card, binding, snapshot))

    def _reserve(self, card, user_id, binding, snapshot, key):
        source = snapshot.source_revision()
        provider = provider_revision(binding)
        with DbSession.atomic() as db:
            # Serialize reservations on PostgreSQL; the unique constraint also
            # arbitrates SQLite and callers which did not acquire this lock.
            db.exec(SqlBuilder.select.table(Card).where(Card.id == card.id).with_for_update()).first()
            query = SqlBuilder.select.table(CardCommentCompaction).where(CardCommentCompaction.request_key == key)
            existing = db.exec(query).first()
            if existing:
                return existing
            record = CardCommentCompaction(request_key=key, card_id=card.id, user_id=user_id,
                internal_bot_id=binding.id, source_revision=source, provider_revision=provider,
                policy_version=POLICY_VERSION, source_manifest=snapshot.evidence_manifest())
            try:
                with db.savepoint():
                    db.insert(record)
            except IntegrityError:
                existing = db.exec(query).first()
                if existing is None:
                    raise
                return existing
            return record

    def claim(self, record: CardCommentCompaction, *, retry: bool = False, now: SafeDateTime | None = None,
              lease_seconds: int = 120) -> CardCommentCompaction | None:
        if not 1 <= lease_seconds <= 3600:
            raise ValueError("Invalid compaction lease")
        now = now or SafeDateTime.now()
        with DbSession.atomic() as db:
            current = self._locked(db, record)
            if current is None or current.status == "completed":
                return None
            expired = current.lease_expires_at is None or _utc(current.lease_expires_at) <= _utc(now)
            if current.status != "pending" and not (retry and expired and current.status in {"running", "failed", "cancelled"}):
                return None
            values = {"status": "running", "attempt": current.attempt + 1,
                      "lease_expires_at": now + timedelta(seconds=lease_seconds),
                      "error_code": None, "updated_at": now}
            changed = db.exec(update(CardCommentCompaction).execution_options(synchronize_session=False).where(
                CardCommentCompaction.id == current.id, CardCommentCompaction.status == current.status,
                CardCommentCompaction.attempt == current.attempt,
            ).values(**values))
            if changed != 1:
                return None
            for field, value in values.items():
                setattr(current, field, value)
            start_compaction_history(db, current, now)
            return current

    def renew(self, record: CardCommentCompaction, attempt: int, *, lease_seconds: int = 120,
              now: SafeDateTime | None = None) -> bool:
        """Extend only the unexpired running attempt before another bounded call."""
        if not 1 <= lease_seconds <= 3600:
            raise ValueError("Invalid compaction lease")
        now = now or SafeDateTime.now()
        with DbSession.atomic() as db:
            current = self._locked(db, record)
            if not self._owns_attempt(current, attempt, now):
                return False
            return db.exec(update(CardCommentCompaction).execution_options(synchronize_session=False).where(
                self._attempt_condition(current, attempt, now),
            ).values(lease_expires_at=now + timedelta(seconds=lease_seconds), updated_at=now)) == 1

    def complete(self, record: CardCommentCompaction, attempt: int, snapshot: CommentCompactionEvidence,
                 binding: InternalBot, draft: CompactionDraft, *, now: SafeDateTime | None = None,
                 automatic_authority: dict | None = None) -> bool:
        """Called only inside the application's current-authority transaction.

        Snapshot and binding must be re-read on the primary by that application;
        matching caller-supplied values cannot establish either authority.
        """
        validated = snapshot.validate_draft_json(draft.model_dump_json())
        with DbSession.atomic() as db:
            current = self._locked(db, record)
            if not self._owns_attempt(current, attempt, now or SafeDateTime.now()):
                return False
            if (current.source_revision != snapshot.source_revision()
                    or current.provider_revision != provider_revision(binding)
                    or current.internal_bot_id != binding.id):
                return False
            changed = db.exec(update(CardCommentCompaction).execution_options(synchronize_session=False).where(
                self._attempt_condition(current, attempt, now or SafeDateTime.now()),
            ).values(draft=validated.model_dump(mode="json"), status="completed", lease_expires_at=None,
                     updated_at=SafeDateTime.now())) == 1
            if changed:
                # The fenced completion owns this immutable, server-derived metadata row.
                # Public reads must validate the source and current reader through the compaction service.
                metadata = CardMetadata(card_id=current.card_id,
                    key=f"internal.comment_compaction.{current.get_uid()}.search",
                    value=json.dumps({"source": "comment_compaction", "derived": True,
                        "compaction_uid": current.get_uid(), "source_revision": current.source_revision,
                        "policy_version": current.policy_version,
                        **({"automatic_authority": automatic_authority} if current.user_id is None and automatic_authority is not None else {}),
                        "keywords": [item.model_dump(mode="json") for item in validated.keywords],
                        "entities": [item.model_dump(mode="json") for item in validated.entities],
                        "index": {"status": "pending"}}, ensure_ascii=False))
                db.insert(metadata)
                from ....tasks.docling.CompactionMetadataIndexTask import enqueue_index

                db.after_commit(lambda: enqueue_index(metadata.id))
                finish_compaction_history(db, current, attempt, "succeeded", now=now)
            return changed

    def fail(self, record: CardCommentCompaction, attempt: int, code: str) -> bool:
        if code not in FAILURE_CODES:
            raise ValueError("Invalid compaction error code")
        with DbSession.atomic() as db:
            current = self._locked(db, record)
            if not self._owns_attempt(current, attempt, SafeDateTime.now()):
                return False
            changed = db.exec(update(CardCommentCompaction).execution_options(synchronize_session=False).where(
                self._attempt_condition(current, attempt, SafeDateTime.now()),
            ).values(status="cancelled" if code == "cancelled" else "failed", error_code=code,
                     lease_expires_at=None, updated_at=SafeDateTime.now())) == 1
            if changed:
                finish_compaction_history(db, current, attempt, "cancelled" if code == "cancelled" else "failed", code)
            return changed

    def cancel(self, record: CardCommentCompaction) -> bool:
        """Withdraw pending/running work; a late provider response cannot publish."""
        with DbSession.atomic() as db:
            current = self._locked(db, record)
            if current is None or current.status not in {"pending", "running"}:
                return False
            changed = db.exec(update(CardCommentCompaction).execution_options(synchronize_session=False).where(
                CardCommentCompaction.id == current.id, CardCommentCompaction.attempt == current.attempt,
                CardCommentCompaction.status.in_(["pending", "running"]),
            ).values(status="cancelled", error_code="cancelled", lease_expires_at=None,
                     updated_at=SafeDateTime.now())) == 1
            if changed:
                finish_compaction_history(db, current, current.attempt, "cancelled", "cancelled")
            return changed

    @staticmethod
    def _locked(db, record):
        return db.exec(SqlBuilder.select.table(CardCommentCompaction).where(
            CardCommentCompaction.id == record.id, CardCommentCompaction.card_id == record.card_id,
            CardCommentCompaction.user_id == record.user_id,
            CardCommentCompaction.internal_bot_id == record.internal_bot_id,
        ).with_for_update().execution_options(populate_existing=True)).first()

    @staticmethod
    def _owns_attempt(current, attempt, now) -> bool:
        return bool(current and current.status == "running" and current.attempt == attempt
                    and current.lease_expires_at and _utc(current.lease_expires_at) > _utc(now))

    @staticmethod
    def _attempt_condition(current, attempt, now):
        return ((CardCommentCompaction.id == current.id) & (CardCommentCompaction.status == "running")
                & (CardCommentCompaction.attempt == attempt) & (CardCommentCompaction.lease_expires_at > now))
