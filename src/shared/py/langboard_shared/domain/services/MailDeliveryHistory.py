"""Read-only, bounded projections of the two native mail outboxes.

These rows retain latest state, not immutable attempts. Pending is queued intent;
sent means SMTP acceptance; confirmed_sent is an operator decision. Direct signup,
recovery, invitation and diagnostic sends are not covered by these outboxes.
Historical subjects, SMTP replies, retry counts and inbox receipts are not stored.
No resource links are emitted: mail configuration authority grants no board access.
"""

import base64
import binascii
import hashlib
import json
from datetime import datetime, timezone
from typing import get_args
from sqlalchemy import and_, or_
from ...core.db import DbSession, SqlBuilder
from ...core.resources.locales.EmailTemplateNames import TEmailTemplateName
from ...core.types import SnowflakeID
from ..models import NotificationEmailDelivery, ProjectActivityEmailDelivery
from ..models.NotificationEmailDelivery import NotificationEmailDeliveryStatus


class MailDeliveryHistory:
    SOURCES = {"notification": NotificationEmailDelivery, "project_activity": ProjectActivityEmailDelivery}
    COVERAGE = {
        "sources": list(SOURCES),
        "subject": "not_persisted",
        "retry_history": "not_persisted",
        "inbox_delivery": "unavailable",
        "direct_email_paths": "not_covered",
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
        status: str | None = None,
        created_from: str | None = None,
        created_to: str | None = None,
        cursor: str | None = None,
    ) -> dict:
        if not 1 <= limit <= 100:
            raise ValueError("Invalid limit")
        state = NotificationEmailDeliveryStatus(status) if status is not None else None
        lower, upper = cls._date(created_from), cls._date(created_to)
        if lower is not None and upper is not None and lower >= upper:
            raise ValueError("Invalid date interval")
        filters = [status, cls._iso(lower), cls._iso(upper)]
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
                    or data[0] != 1
                    or data[1] != fingerprint
                    or type(data[3]) is not int
                    or data[3] not in (0, 1)
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
                created, identifier = model.column("created_at"), model.column("id")
                query = SqlBuilder.select.table(model)
                if state is not None:
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
                found.extend((cls._iso(row.created_at), rank, int(row.id), source, row) for row in rows)
        found.sort(key=lambda row: row[:3], reverse=True)
        selected = found[:limit]
        next_cursor = None
        if len(found) > limit:
            at, rank, identifier, _, _ = selected[-1]
            next_cursor = base64.urlsafe_b64encode(json.dumps([1, fingerprint, at, rank, identifier]).encode()).decode()
        return {
            "items": [cls._project(source, row) for _, _, _, source, row in selected],
            "next_cursor": next_cursor,
            "coverage": dict(cls.COVERAGE),
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
        return cls._project(source, row) if row is not None else None

    @classmethod
    def _project(cls, source: str, row) -> dict:
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
