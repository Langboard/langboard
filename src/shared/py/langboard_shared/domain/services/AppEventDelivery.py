"""Internal durable claim primitive. A delivery claim is never a runtime start."""

from datetime import timedelta, timezone
from json import loads
from secrets import token_urlsafe
from ...core.db import DbSession, SqlBuilder
from ...core.types import SafeDateTime
from ..models import AppExecutionOutbox, AppExecutionRequest
from .AppEventDestination import prepare_app_event_signature
from .AppExecutionGrant import evaluate_connection_execution_grant
from .AppGovernance import AppGovernanceConflict, AppGovernanceDenied, _current, lock_policy_mutation


MAX_DELIVERY_ATTEMPTS = 4
DELIVERY_LEASE_SECONDS = 300


def _expired(value, now):
    if value is None:
        return True
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value <= now


def claim_app_event(event_id, expected_destination_revision):
    """Recheck fixed execution authority and target before acquiring a bounded lease."""
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        event = db.exec(
            SqlBuilder.select.table(AppExecutionOutbox)
            .where(
                AppExecutionOutbox.id == event_id,
            )
            .with_for_update(skip_locked=True)
        ).first()
        if event is None or event.state not in ("pending", "delivering"):
            return None
        now = SafeDateTime.now()
        if event.state == "delivering" and not _expired(event.lease_until, now):
            return None
        event.state = "pending"
        if event.claim_token:
            event.delivery_history = [
                *event.delivery_history,
                {
                    "attempt": event.attempt_count,
                    "outcome": "lease_expired",
                    "at": now.isoformat(),
                },
            ]
        event.claim_token = None
        event.lease_until = None
        if event.attempt_count >= MAX_DELIVERY_ATTEMPTS:
            event.state = "failed"
            event.last_error = "delivery_attempts_exhausted"
            db.update(event)
            return None
        receipt = db.exec(
            SqlBuilder.select.table(AppExecutionRequest).where(
                AppExecutionRequest.id == event.request_id,
            )
        ).first()
        try:
            _current_authority(event, receipt)
            # Nested transaction shares the locked event and pins the destination.
            db.update(event)
            signed = prepare_app_event_signature(event.id, expected_destination_revision)
        except (AppGovernanceDenied, AppGovernanceConflict):
            event.state = "blocked"
            event.last_error = "current_authority_or_destination_changed"
            db.update(event)
            return None
        event.state = "delivering"
        event.payload = loads(signed["body"])
        event.claim_token = token_urlsafe(24)
        event.lease_until = now + timedelta(seconds=DELIVERY_LEASE_SECONDS)
        event.attempt_count += 1
        event.delivery_history = [
            *event.delivery_history,
            {
                "attempt": event.attempt_count,
                "outcome": "claimed",
                "at": now.isoformat(),
            },
        ]
        event.last_error = None
        db.update(event)
        return {
            **signed,
            "event_uid": event.get_uid(),
            "claim_token": event.claim_token,
            "attempt_count": event.attempt_count,
            "lease_until": event.lease_until.isoformat(),
        }


def finish_app_event(event_id, claim_token, *, delivered):
    """Fence late workers; HTTP success records delivery only, never running work."""
    if type(delivered) is not bool:
        raise ValueError("Delivery result must be boolean")
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        event = db.exec(
            SqlBuilder.select.table(AppExecutionOutbox)
            .where(
                AppExecutionOutbox.id == event_id,
            )
            .with_for_update()
        ).first()
        if (
            event is None
            or event.state != "delivering"
            or event.claim_token != claim_token
            or _expired(event.lease_until, SafeDateTime.now())
        ):
            raise AppGovernanceConflict()
        event.state = (
            "delivered" if delivered else ("failed" if event.attempt_count >= MAX_DELIVERY_ATTEMPTS else "pending")
        )
        event.last_error = None if delivered else "delivery_failed"
        event.delivery_history = [
            *event.delivery_history,
            {
                "attempt": event.attempt_count,
                "outcome": "delivered" if delivered else "delivery_failed",
                "at": SafeDateTime.now().isoformat(),
            },
        ]
        event.claim_token = None
        event.lease_until = None
        db.update(event)
        return {
            "event_uid": event.get_uid(),
            "state": event.state,
            "attempt_count": event.attempt_count,
            "started": False,
        }


def _current_authority(event, receipt):
    from .AppEventDestination import APP_CARD_ARCHIVE_EVENT, APP_INTERACTION_RESPONSE_EVENT

    if event.event_type == APP_CARD_ARCHIVE_EVENT:
        from .AppCardArchive import revalidate_archive_event

        with DbSession.atomic() as db:
            lock_policy_mutation(db)
            revalidate_archive_event(db, event)
        return

    if event.event_type == APP_INTERACTION_RESPONSE_EVENT:
        from .AppCardInteractions import AppCardInteractionConflict, revalidate_response_event

        with DbSession.atomic() as db:
            lock_policy_mutation(db)
            try:
                revalidate_response_event(db, event)
            except AppCardInteractionConflict as exc:
                raise AppGovernanceConflict() from exc
        return
    if (
        receipt is None
        or receipt.connection_id != event.connection_id
        or receipt.app_key != event.app_key
        or receipt.project_id != event.project_id
    ):
        raise AppGovernanceDenied()
    if (
        event.payload.get("request_uid") != receipt.get_uid()
        or event.payload.get("card_uid") != receipt.authority.get("card_uid")
        or event.payload.get("generation") != receipt.generation
        or event.payload.get("resource_uids") != receipt.authority.get("resource_uids")
    ):
        raise AppGovernanceDenied()
    current = evaluate_connection_execution_grant(
        event.connection_id,
        receipt.project_id,
        receipt.card_id,
        receipt.generation,
    )
    if current["authority_version"] != receipt.authority.get("authority_version"):
        raise AppGovernanceConflict()


def revalidate_app_event_claim(event_id, claim_token, expected_destination_revision):
    """After DNS resolution, fence the same live claim immediately before HTTP."""
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        event = db.exec(
            SqlBuilder.select.table(AppExecutionOutbox)
            .where(
                AppExecutionOutbox.id == event_id,
            )
            .with_for_update()
        ).first()
        if (
            event is None
            or event.state != "delivering"
            or event.claim_token != claim_token
            or _expired(event.lease_until, SafeDateTime.now())
        ):
            raise AppGovernanceConflict()
        receipt = db.exec(
            SqlBuilder.select.table(AppExecutionRequest).where(
                AppExecutionRequest.id == event.request_id,
            )
        ).first()
        try:
            _current_authority(event, receipt)
            return prepare_app_event_signature(event.id, expected_destination_revision, claim_token=claim_token)
        except (AppGovernanceDenied, AppGovernanceConflict):
            event.state = "blocked"
            event.last_error = "current_authority_or_destination_changed"
            event.delivery_history = [
                *event.delivery_history,
                {
                    "attempt": event.attempt_count,
                    "outcome": "blocked_before_send",
                    "at": SafeDateTime.now().isoformat(),
                },
            ]
            event.claim_token = None
            event.lease_until = None
            db.update(event)
            return None


def enqueue_event(event_id):
    from ...core.broker import Broker

    Broker.celery.send_task("langboard.app_event_delivery", args=[int(event_id)])


def recover_pending_events(after_id=None, *, limit=100):
    """Bound startup/admin recovery to retained pending or expired event claims."""
    from sqlalchemy import or_
    from .AppEventDestination import APP_EVENTS

    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Recovery limit must be between 1 and 100")
    now = SafeDateTime.now()
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        query = (
            SqlBuilder.select.table(AppExecutionOutbox)
            .where(
                AppExecutionOutbox.event_type.in_(APP_EVENTS),
                AppExecutionOutbox.payload["destination"]["revision"].as_integer().is_not(None),
                or_(
                    AppExecutionOutbox.state == "pending",
                    (AppExecutionOutbox.state == "delivering") & (AppExecutionOutbox.lease_until <= now),
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
        lock_policy_mutation(db)
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
        lock_policy_mutation(db)
        user = _actor(db, actor, for_update=False)
        if not user.is_admin:
            raise AppGovernanceDenied()
        event = _current(db, AppExecutionOutbox, event_id, for_update=False)
        if event is None:
            raise AppGovernanceDenied()
        return {
            "event_uid": event.get_uid(),
            "state": event.state,
            "attempts": event.attempt_count,
            "delivery_receipts": event.delivery_history,
            "started": False,
        }
