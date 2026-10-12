"""Bounded event delivery claims; neither transport nor ACK grants execution."""

from datetime import timedelta
from secrets import token_urlsafe
from ...core.db import DbSession, SqlBuilder
from ...core.types import SafeDateTime
from ..models import AppConnection, AppExecutionOutbox
from .AppEventDestination import current_destination, prepare_app_event_signature
from .AppGovernance import AppGovernanceDenied, _current, lock_policy_mutation


MAX_ATTEMPTS = 5
CLAIM_SECONDS = 60


class AppEventBusy(RuntimeError):
    """Another bounded send owns the current event claim."""


def _receipt(event, kind, **fields):
    """Append bounded attempt evidence; never persist payloads, URLs or secrets."""
    event.delivery_receipts = [
        *event.delivery_receipts,
        {
            "event_uid": event.get_uid(),
            "attempt": event.attempt,
            "kind": kind,
            "at": SafeDateTime.now().isoformat(),
            **fields,
        },
    ]


def claim_event(event_id):
    with DbSession.atomic() as db:
        event = _current(db, AppExecutionOutbox, event_id)
        now = SafeDateTime.now()
        if event is None or event.state in ("delivered", "failed"):
            return None
        if event.state == "delivering" and event.claim_expires_at and event.claim_expires_at > now:
            raise AppEventBusy("Event claim remains live")
        if event.state == "delivering":
            _receipt(event, "outcome", outcome="uncertain", error="claim_expired")
        if event.attempt >= MAX_ATTEMPTS:
            event.state = "failed"
            event.last_error = "attempt_limit"
            db.update(event)
            return None
        event.state = "delivering"
        event.attempt += 1
        event.claim_token = token_urlsafe(24)
        event.claim_expires_at = now + timedelta(seconds=CLAIM_SECONDS)
        _receipt(event, "claimed")
        db.update(event)
        return event.claim_token


def prepare_claim(event_id, token):
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        event = _current(db, AppExecutionOutbox, event_id, for_update=False)
        if event is None:
            raise AppGovernanceDenied()
        connection = _current(db, AppConnection, event.connection_id, for_update=False)
        if connection is None:
            raise AppGovernanceDenied()
        # Destination lookup is deferred to the existing configuration lock order.
        from .AppEventDestination import validate_event_authority

        validate_event_authority(db, event, connection)
        destination, _ = current_destination(db, connection, event.event_type)
        prepared = prepare_app_event_signature(event_id, destination.revision, claim_token=token)
        event = _current(db, AppExecutionOutbox, event_id)
        if not any(
            item["attempt"] == event.attempt and item["kind"] == "destination" for item in event.delivery_receipts
        ):
            _receipt(event, "destination", destination_revision=destination.revision)
            db.update(event)
        return prepared


def finish_event(event_id, token, *, success, error=None, retryable=False):
    with DbSession.atomic() as db:
        event = _current(db, AppExecutionOutbox, event_id)
        if event is None or event.state != "delivering" or event.claim_token != token:
            return False
        event.state = "delivered" if success else "pending" if retryable and event.attempt < MAX_ATTEMPTS else "failed"
        _receipt(event, "outcome", outcome="delivered" if success else "failed", error=None if success else error)
        event.last_error = None if success else error
        event.claim_token = None
        event.claim_expires_at = None
        db.update(event)
        return event.state == "pending"


def enqueue_event(event_id):
    from ...core.broker import Broker

    Broker.celery.send_task("langboard.app_event_delivery", args=[int(event_id)])


def recover_pending_events(after_id=None, *, limit=100):
    """Bound startup/admin recovery to retained pending or expired event claims."""
    from sqlalchemy import or_

    now = SafeDateTime.now()
    with DbSession.atomic() as db:
        query = (
            SqlBuilder.select.table(AppExecutionOutbox)
            .where(
                or_(
                    AppExecutionOutbox.state == "pending",
                    (AppExecutionOutbox.state == "delivering") & (AppExecutionOutbox.claim_expires_at <= now),
                )
            )
            .order_by(AppExecutionOutbox.id)
        )
        if after_id is not None:
            query = query.where(AppExecutionOutbox.id > after_id)
        rows = db.exec(query.limit(limit)).all()
        identifiers = [row.id for row in rows]
    for identifier in identifiers:
        enqueue_event(identifier)
    return identifiers[-1] if len(identifiers) == limit else None


def request_event_redelivery(actor, event_id):
    from .AppGovernance import _actor

    with DbSession.atomic() as db:
        user = _actor(db, actor, for_update=False)
        if not user.is_admin:
            raise AppGovernanceDenied()
        row = _current(db, AppExecutionOutbox, event_id, for_update=False)
        if row is None or row.state not in ("pending", "delivering"):
            raise AppGovernanceDenied()
        db.after_commit(lambda: enqueue_event(event_id))
        return {"event_uid": row.get_uid(), "state": row.state, "delivery_requested": True, "started": False}


def read_event_delivery(actor, event_id):
    """Administrator delivery evidence; payloads and signing material remain private."""
    from .AppGovernance import _actor

    with DbSession.atomic() as db:
        user = _actor(db, actor, for_update=False)
        if not user.is_admin:
            raise AppGovernanceDenied()
        event = _current(db, AppExecutionOutbox, event_id, for_update=False)
        if event is None:
            raise AppGovernanceDenied()
        return {
            "event_uid": event.get_uid(),
            "state": event.state,
            "attempts": event.attempt,
            "delivery_receipts": event.delivery_receipts,
            "started": False,
        }
