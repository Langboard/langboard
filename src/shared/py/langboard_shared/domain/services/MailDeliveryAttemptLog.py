"""Independent durable audit transactions at the existing SMTP boundary.

A start must commit before SMTP. Failure prevents sending. Completion is best
effort: SMTP acceptance remains authoritative even when its update cannot commit.
An unfinished row is unknown and must never be interpreted as permission to resend.
"""

from email.message import EmailMessage
from sqlalchemy import update
from sqlalchemy.orm import Session
from ...core.db import DbSession
from ...core.db.DbEngine import DbEngine
from ...core.types import SafeDateTime
from ..models.MailDeliveryAttempt import MailDeliveryAttempt


class MailAuditUnavailable(RuntimeError):
    """No SMTP invocation occurred because its start could not be persisted."""


class MailDeliveryAttemptLog:
    @staticmethod
    def start(message: EmailMessage) -> MailDeliveryAttempt:
        metadata = getattr(message, "_langboard_attempt_metadata", {})
        try:
            row = MailDeliveryAttempt(
                recipient_masked="***@***" if "@" in str(message.get("To", "")) else "***",
                **metadata,
            )
            # Do not join a caller's atomic transaction: it may roll back after
            # SMTP, and would then erase evidence of an irreversible side effect.
            with Session(DbEngine.get_main_engine(), expire_on_commit=False) as session, session.begin():
                DbSession(session, readonly=False).insert(row)
        except Exception:
            raise MailAuditUnavailable("Mail attempt storage unavailable") from None
        return row

    @staticmethod
    def finish(row: MailDeliveryAttempt, outcome: str, code: str | None) -> None:
        with Session(DbEngine.get_main_engine()) as session, session.begin():
            session.execute(
                update(MailDeliveryAttempt)
                .where(MailDeliveryAttempt.column("id") == row.id)
                .values(
                    outcome=outcome,
                    safe_error_code=code,
                    completed_at=SafeDateTime.now(),
                    updated_at=SafeDateTime.now(),
                )
            )
