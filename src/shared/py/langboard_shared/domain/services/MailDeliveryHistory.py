"""Read-only, bounded views of SMTP attempts and native outbox latest state.

Legacy outboxes retain latest state; new SMTP rows retain individual attempts.
Pending is queued intent;
sent means SMTP acceptance; confirmed_sent is an operator decision. Direct signup,
recovery, invitation and explicit test sends are recorded after migration.
Attempt subjects use fixed native labels. Legacy subjects, SMTP replies, retry
intent and inbox receipts are not stored. Recorded attempt associations are counted.
No resource links are emitted: mail configuration authority grants no board access.
"""

import base64
import binascii
import hashlib
import json
from datetime import datetime, timezone
from typing import get_args
from sqlalchemy import and_, func, or_, select, tuple_
from ...core.db import DbSession, SqlBuilder
from ...core.resources.locales.EmailTemplateNames import TEmailTemplateName
from ...core.types import SnowflakeID
from ..models import NotificationEmailDelivery, ProjectActivityEmailDelivery
from ..models.MailDeliveryAttempt import MailDeliveryAttempt
from ..models.NotificationEmailDelivery import NotificationEmailDeliveryStatus


class MailDeliveryHistory:
    SOURCES = {
        "notification": NotificationEmailDelivery,
        "project_activity": ProjectActivityEmailDelivery,
        "smtp_attempt": MailDeliveryAttempt,
    }
    COVERAGE = {
        "sources": list(SOURCES),
        "subject": "native_labels_for_attempts_legacy_not_persisted",
        "retry_history": "original_intent_unknown_recorded_associations_only",
        "inbox_delivery": "unavailable",
        "direct_email_paths": "recorded_after_migration",
        "legacy_backfill": "unavailable",
        "views": "separate_attempts_and_legacy_no_combined_total",
    }

    @staticmethod
    def _date(value: str | None) -> datetime | None:
        if value is None:
            return None
        if not isinstance(value, str) or len(value) > 40:
            raise ValueError("Invalid date")
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("Timezone required")
        try:
            return parsed.astimezone(timezone.utc)
        except OverflowError as exc:
            raise ValueError("Invalid date") from exc

    @staticmethod
    def _iso(value: datetime | None) -> str | None:
        return (
            value.replace(tzinfo=timezone.utc).isoformat()
            if value is not None and value.tzinfo is None
            else (value.astimezone(timezone.utc).isoformat() if value is not None else None)
        )

    @classmethod
    def page(
        cls,
        *,
        limit: int = 25,
        view: str = "legacy",
        status: str | None = None,
        created_from: str | None = None,
        created_to: str | None = None,
        cursor: str | None = None,
    ) -> dict:
        if view not in ("attempts", "legacy"):
            raise ValueError("Invalid history view")
        if not 1 <= limit <= 100:
            raise ValueError("Invalid limit")
        state = NotificationEmailDeliveryStatus(status) if status is not None else None
        lower, upper = cls._date(created_from), cls._date(created_to)
        if lower is not None and upper is not None and lower >= upper:
            raise ValueError("Invalid date interval")
        filters = [view, status, cls._iso(lower), cls._iso(upper)]
        fingerprint = hashlib.sha256(json.dumps(filters).encode()).hexdigest()
        boundary = None
        if cursor is not None:
            try:
                if len(cursor) > 512:
                    raise ValueError("Invalid cursor")
                data = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
                if (
                    not isinstance(data, list)
                    or len(data) != 5
                    or type(data[0]) is not int
                    or data[0] != 2
                    or data[1] != fingerprint
                    or type(data[3]) is not int
                    or data[3] not in ((2,) if view == "attempts" else (0, 1))
                    or type(data[4]) is not int
                    or not 0 < data[4] <= SnowflakeID.MAX_VALUE
                ):
                    raise ValueError("Invalid cursor")
                boundary = (cls._date(data[2]), data[3], data[4])
                if boundary[0] is None:
                    raise ValueError("Invalid cursor")
            except (ValueError, TypeError, binascii.Error, UnicodeError) as exc:
                raise ValueError("Invalid cursor") from exc
        found = []
        # Use the authoritative engine. No writes, sends, retries or review actions.
        with DbSession.use(readonly=False, sensitive=True) as db:
            for rank, (source, model) in enumerate(cls.SOURCES.items()):
                if (source == "smtp_attempt") != (view == "attempts"):
                    continue
                created, identifier = (
                    model.column("started_at" if source == "smtp_attempt" else "created_at"),
                    model.column("id"),
                )
                query = SqlBuilder.select.table(model)
                if state is not None:
                    if source == "smtp_attempt":
                        outcome = {"sent": "accepted", "failed": "rejected", "uncertain": "unknown"}.get(status)
                        query = query.where(model.column("outcome") == outcome) if outcome else query.where(False)
                    else:
                        query = query.where(model.column("status") == state)
                if lower is not None:
                    query = query.where(created >= lower)
                if upper is not None:
                    query = query.where(created < upper)
                if boundary is not None:
                    at, last_rank, last_id = boundary
                    tied = identifier < last_id if rank == last_rank else rank < last_rank
                    query = query.where(or_(created < at, and_(created == at, tied)))
                rows = db.exec(query.order_by(created.desc(), identifier.desc()).limit(limit + 1)).all()
                found.extend(
                    (
                        cls._iso(row.started_at if source == "smtp_attempt" else row.created_at),
                        rank,
                        int(row.id),
                        source,
                        row,
                    )
                    for row in rows
                )
            found.sort(key=lambda row: row[:3], reverse=True)
            selected = found[:limit]
            items = cls._recorded_counts(db, [cls._project(source, row) for _, _, _, source, row in selected])
        next_cursor = None
        if len(found) > limit:
            at, rank, identifier, _, _ = selected[-1]
            next_cursor = base64.urlsafe_b64encode(json.dumps([2, fingerprint, at, rank, identifier]).encode()).decode()
        return {
            "items": items,
            "next_cursor": next_cursor,
            "coverage": {**cls.COVERAGE, "view": view},
        }

    @classmethod
    def detail(cls, source: str, uid: str) -> dict | None:
        model = cls.SOURCES.get(source)
        if model is None or len(uid) != 11 or not uid.isascii() or not uid.isalnum():
            return None
        identifier = SnowflakeID.from_short_code(uid)
        if identifier <= 0 or identifier.to_short_code() != uid:
            return None
        with DbSession.use(readonly=False, sensitive=True) as db:
            row = db.exec(SqlBuilder.select.table(model).where(model.column("id") == identifier).limit(1)).first()
            return cls._recorded_counts(db, [cls._project(source, row)])[0] if row is not None else None

    @staticmethod
    def _recorded_counts(db, items: list[dict]) -> list[dict]:
        # Bound aggregate keys to the selected page; direct sends have no shared identity.
        keys = {
            tuple(key.split(":", 1))
            for item in items
            if (key := item.get("outbox_key") if item["source"] == "smtp_attempt" else item["key"])
        }
        counts = {}
        if keys:
            model = MailDeliveryAttempt
            source, uid = model.column("source"), model.column("source_uid")
            query = (
                select(source, uid, func.count(model.column("id")))
                .where(tuple_(source, uid).in_(keys))
                .group_by(source, uid)
                .limit(len(keys))
            )
            counts = {(source, uid): count for source, uid, count in db.exec(query).all()}
        for item in items:
            key = item.get("outbox_key") if item["source"] == "smtp_attempt" else item["key"]
            item["recorded_attempt_count"] = counts.get(tuple(key.split(":", 1)), 0) if key else None
            # Multiple recorded invocations prove neither original retry intent nor old history.
        return items

    @classmethod
    def _project(cls, source: str, row) -> dict:
        if source == "smtp_attempt":
            template = row.template_name if row.template_name in get_args(TEmailTemplateName) else None
            # Sender metadata contains fixed native labels only; reject arbitrary stored content.
            subjects = {f"Langboard: {name.replace(chr(95), chr(32))}" for name in get_args(TEmailTemplateName)}
            subjects.add("Langboard SMTP test message")
            code = row.safe_error_code
            if code not in {
                "smtp_rejected",
                "authentication_failed",
                "connection_failed",
                "tls_failed",
                "timeout",
                "transport_error",
            }:
                code = "error_detail_unavailable" if code else None
            outbox_key = None
            if row.source in ("notification", "project_activity") and row.source_uid:
                uid = row.source_uid
                if len(uid) == 11 and uid.isascii() and uid.isalnum():
                    identifier = SnowflakeID.from_short_code(uid)
                    if identifier > 0 and identifier.to_short_code() == uid:
                        outbox_key = f"{row.source}:{uid}"
            return {
                "key": f"smtp_attempt:{row.get_uid()}",
                "source": "smtp_attempt",
                "uid": row.get_uid(),
                "record_kind": "smtp_attempt",
                "created_at": cls._iso(row.created_at),
                "started_at": cls._iso(row.started_at),
                "completed_at": cls._iso(row.completed_at),
                "claimed_at": None,
                "sent_at": None,
                "status": {"accepted": "sent", "rejected": "failed"}.get(row.outcome, "uncertain"),
                "recipient_masked": "***@***" if row.recipient_masked == "***@***" else "***",
                "trigger": row.trigger
                if row.trigger in (*get_args(TEmailTemplateName), "direct", "template", "smtp_test")
                else "direct",
                "template": template,
                "template_provenance": "persisted",
                "subject": row.subject if row.subject in subjects else None,
                "safe_error_code": code,
                "attempt_evidence": {"accepted": "smtp_accepted", "rejected": "smtp_rejected"}.get(
                    row.outcome, "unknown"
                ),
                "inbox_delivery": "unverified",
                "retry_history": None,
                "linked_ids": None,
                "outbox_key": outbox_key,
            }
        status = row.status.value
        evidence = "unknown"
        if status == "pending":
            evidence = "not_attempted"
        elif status == "sent":
            evidence = "smtp_accepted"
        elif status == "confirmed_sent":
            evidence = "operator_confirmed"
        elif row.claimed_at is not None:
            evidence = "claimed"
        reason = row.failure_reason
        code = None
        if reason:
            code = (
                "worker_outcome_unknown"
                if reason == "Delivery worker exited before recording an SMTP outcome"
                else "error_detail_unavailable"
            )
        template = row.template_name if source == "notification" else "project_activity_updated"
        if template not in get_args(TEmailTemplateName):
            template = None
        uid = row.get_uid()
        return {
            "key": f"{source}:{uid}",
            "record_kind": "outbox_latest",
            "source": source,
            "uid": uid,
            "created_at": cls._iso(row.created_at),
            "claimed_at": cls._iso(row.claimed_at),
            "sent_at": cls._iso(row.sent_at),
            "status": status,
            "recipient_masked": "***@***" if "@" in row.recipient_email else "***",
            "trigger": row.notification_type.value if source == "notification" else "project_activity",
            "template": template,
            "template_provenance": "persisted" if source == "notification" else "native_sender",
            "subject": None,
            "safe_error_code": code,
            "attempt_evidence": evidence,
            "inbox_delivery": "unverified",
            "retry_history": None,
            "linked_ids": None,
        }
