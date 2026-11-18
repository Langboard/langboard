"""Personal notification scope, current send authority and retained receipts.

Native activity producers reuse this authority and outbox. Personal test content
contains only the initiating user's text; room sharing is not authorized here.
"""

from dataclasses import asdict
from datetime import timedelta
from ...core.db import DbSession, SqlBuilder
from ...core.types import SafeDateTime, SnowflakeID
from ..models import AppExecutionOutbox, User
from ..models.AppNotificationSubscription import AppNotificationSubscription, AppNotificationDispatch, AppNotificationSharedProjectScope
from .AppConnectionAuthentication import _connection, _identity_hash, authenticate_connection_credential
from .AppGovernance import AppGovernanceConflict, AppGovernanceDenied, _actor, _current, lock_policy_mutation
from .AppNotificationDestinations import current_notification_destination, _positive, _request, _expires
from .AppEventDestination import current_destination, APP_NOTIFICATION_EVENT
from .NotificationDeliveryReceipt import (
    NotificationDeliveryFence, NotificationDeliveryReceipt, NotificationReceiptConflict, accept_notification_receipt,
)

MAX_RECEIPT_REQUESTS = 32


def _data(row):
    return {"subscription_uid": row.get_uid(), "state": row.state, "generation": row.generation,
            "event_kinds": row.filters.get("event_kinds", ["test"]),
            "project_uids": row.filters.get("project_uids", []),
            "destination_uid": SnowflakeID(row.destination_id).to_short_code(),
            "destination_revision": row.destination_revision, "scope": row.filters.get("scope", "personal")}


def _current_subscription(db, identifier, *, principal=None):
    row = _current(db, AppNotificationSubscription, identifier)
    if row is None or row.state != "enabled":
        raise AppGovernanceDenied()
    if principal is not None and (
        row.connection_id != principal.connection_id or row.user_id != principal.owner_id or row.app_key != principal.app_key
    ):
        raise AppGovernanceDenied()
    destination = current_notification_destination(db, row.destination_id, expected_revision=row.destination_revision)
    connection = _connection(db, row.connection_id)
    if (destination.user_id != row.user_id or destination.connection_id != row.connection_id
            or destination.app_key != row.app_key or _identity_hash(db, connection) != row.connection_identity):
        raise AppGovernanceConflict()
    return row, destination, connection


def _readable_project_ids(actor, project_ids):
    from . import DomainService
    from ...core.security.CollaborationChannel import CollaborationChannel

    with DomainService.use() as service:
        return set(service.card.resolve_work_visibility_contexts(actor, CollaborationChannel.Api, project_ids=project_ids))


def read_shared_notification_scope(token, subscription_id):
    """Read the current effective grant for this exact app connection."""
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        principal = authenticate_connection_credential(token)
        subscription, destination, connection = _current_subscription(db, subscription_id, principal=principal)
        if destination.proof.get("destination", {}).get("kind") != "shared" or subscription.filters.get("scope") != "shared":
            raise AppGovernanceDenied()
        current_destination(db, connection, APP_NOTIFICATION_EVENT)
        project_ids = {int(row.project_id) for row in db.exec(
            SqlBuilder.select.table(AppNotificationSharedProjectScope).where(
                AppNotificationSharedProjectScope.subscription_id == subscription.id)).all()}
        owner = _current(db, User, subscription.user_id)
        if owner is None or owner.deleted_at or not owner.activated_at:
            raise AppGovernanceDenied()
        readable = _readable_project_ids(owner, project_ids) if project_ids else set()
        consented = set(subscription.filters.get("project_uids", []))
        effective = {SnowflakeID(project_id).to_short_code() for project_id in project_ids & readable} & consented
        return {**_data(subscription), "connection_uid": connection.get_uid(), "project_uids": sorted(effective)}


def create_personal_subscription(actor, destination_id, *, expected_destination_revision, request_id, scope="personal", project_uids=None):
    _request(request_id); _positive(expected_destination_revision)
    if scope not in {"personal", "shared"}:
        raise ValueError("Invalid notification scope")
    project_uids = project_uids or []
    if scope == "personal" and project_uids:
        raise ValueError("Configure personal board scope explicitly after creation")
    if scope == "shared":
        if not isinstance(project_uids, list) or not project_uids or len(project_uids) > 50:
            raise ValueError("Shared notifications require selected readable boards")
        try:
            project_ids = {SnowflakeID.from_short_code(value) for value in project_uids
                           if isinstance(value, str) and len(value) == 11}
        except (TypeError, ValueError):
            raise ValueError("Invalid notification project scope") from None
        if len(project_ids) != len(project_uids):
            raise ValueError("Invalid notification project scope")
    with DbSession.atomic() as db:
        lock_policy_mutation(db, exclusive=True)
        user = _actor(db, actor)
        destination = current_notification_destination(db, destination_id, expected_revision=expected_destination_revision)
        if destination.user_id != user.id or destination.proof.get("destination", {}).get("kind") != scope:
            raise AppGovernanceDenied()
        if scope == "shared":
            ids = {int(value) for value in project_ids}
            if not ids <= _readable_project_ids(user, ids):
                raise AppGovernanceDenied()
        connection = _connection(db, destination.connection_id)
        current_destination(db, connection, APP_NOTIFICATION_EVENT)
        row = db.exec(SqlBuilder.select.table(AppNotificationSubscription).where(
            AppNotificationSubscription.user_id == user.id,
            AppNotificationSubscription.request_id == request_id).with_for_update()).first()
        if row is not None:
            if (row.destination_id != destination.id or row.destination_revision != expected_destination_revision
                    or row.connection_identity != _identity_hash(db, connection) or row.state != "enabled"
                    or row.filters.get("scope", "personal") != scope
                    or (scope == "shared" and row.filters.get("project_uids", []) != sorted(project_uids))):
                raise AppGovernanceConflict()
            return _data(row)
        row = AppNotificationSubscription(user_id=user.id, request_id=request_id, app_key=connection.app_key,
            connection_id=connection.id, connection_identity=_identity_hash(db, connection),
            destination_id=destination.id, destination_revision=destination.revision)
        if scope == "shared":
            row.filters = {"scope": "shared", "event_kinds": ["test"], "project_uids": sorted(project_uids)}
        db.insert(row)
        if scope == "shared":
            for project_id in project_ids:
                db.insert(AppNotificationSharedProjectScope(subscription_id=row.id, project_id=project_id))
        return _data(row)



def configure_personal_subscription(actor, subscription_id, *, expected_generation, event_kinds, project_uids):
    """Explicit opt-in to native content kinds; an old test grant never expands itself."""
    _positive(expected_generation)
    supported = {"test", "card_activity", "card_deadline", "missed_summary"}
    if (not isinstance(event_kinds, list) or not event_kinds or len(event_kinds) > len(supported)
            or any(not isinstance(value, str) or value not in supported for value in event_kinds)
            or len(set(event_kinds)) != len(event_kinds)
            or not isinstance(project_uids, list) or len(project_uids) > 50):
        raise ValueError("Invalid notification subscription scope")
    try:
        project_ids = {SnowflakeID.from_short_code(value) for value in project_uids
                       if isinstance(value, str) and len(value) == 11}
    except (TypeError, ValueError):
        raise ValueError("Invalid notification project scope") from None
    if "missed_summary" in event_kinds and not {"card_activity", "card_deadline"}.intersection(event_kinds):
        raise ValueError("Missed summaries require a native notification kind")
    if len(project_ids) != len(project_uids) or ("card_deadline" in event_kinds and not project_ids):
        raise ValueError("Deadline notifications require selected readable boards")
    with DbSession.atomic() as db:
        lock_policy_mutation(db, exclusive=True)
        user = _actor(db, actor)
        row, destination, _ = _current_subscription(db, subscription_id)
        if row.user_id != user.id:
            raise AppGovernanceDenied()
        scope = destination.proof["destination"]["kind"]
        if scope == "shared" and ("card_activity" in event_kinds or not project_ids):
            raise ValueError("Shared notifications require selected boards and exclude activity")
        if row.generation != expected_generation:
            raise AppGovernanceConflict()
        if project_ids:
            ids = {int(value) for value in project_ids}
            if not ids <= _readable_project_ids(user, ids):
                raise AppGovernanceDenied()
        kinds = sorted(event_kinds)
        projects = sorted(SnowflakeID(identifier).to_short_code() for identifier in project_ids)
        if row.filters.get("event_kinds", ["test"]) != kinds or row.filters.get("project_uids", []) != projects:
            row.filters = {"event_kinds": kinds, "project_uids": projects,
                           "activated_at": SafeDateTime.now().isoformat()}
            if scope == "shared":
                row.filters["scope"] = "shared"
                for entry in db.exec(SqlBuilder.select.table(AppNotificationSharedProjectScope).where(
                        AppNotificationSharedProjectScope.subscription_id == row.id)).all():
                    db.delete(entry)
                for project_id in project_ids:
                    db.insert(AppNotificationSharedProjectScope(subscription_id=row.id, project_id=project_id))
            row.generation += 1
            db.update(row)
        return _data(row)


def revoke_personal_subscription(actor, subscription_id, *, expected_generation):
    _positive(expected_generation)
    with DbSession.atomic() as db:
        lock_policy_mutation(db, exclusive=True)
        user = _actor(db, actor)
        row = _current(db, AppNotificationSubscription, subscription_id)
        if row is None or row.user_id != user.id:
            raise AppGovernanceDenied()
        if row.generation != expected_generation:
            raise AppGovernanceConflict()
        if row.state != "revoked":
            row.state = "revoked"; row.generation += 1; db.update(row)
        return _data(row)


def _message(title, body):
    for value, maximum in ((title, 120), (body, 2000)):
        if (not isinstance(value, str) or not value.strip() or len(value) > maximum
                or any(ord(ch) < 32 and ch not in '\n\t' for ch in value) or '\x7f' in value):
            raise ValueError("Invalid test notification content")
    return {"kind": "test", "title": title, "body": body}


def create_test_notification(actor, subscription_id, *, expected_generation, request_id, title, body):
    _request(request_id); _positive(expected_generation)
    content = _message(title, body)
    with DbSession.atomic() as db:
        lock_policy_mutation(db, exclusive=True)
        user = _actor(db, actor)
        subscription, destination, connection = _current_subscription(db, subscription_id)
        if subscription.user_id != user.id or "test" not in subscription.filters.get("event_kinds", ["test"]):
            raise AppGovernanceDenied()
        if subscription.generation != expected_generation:
            raise AppGovernanceConflict()
        target, _ = current_destination(db, connection, APP_NOTIFICATION_EVENT)
        row = db.exec(SqlBuilder.select.table(AppNotificationDispatch).where(
            AppNotificationDispatch.subscription_id == subscription.id,
            AppNotificationDispatch.request_id == request_id).with_for_update()).first()
        if row is not None:
            if row.snapshot.get("notification") != content:
                raise AppGovernanceConflict()
            event = _current(db, AppExecutionOutbox, row.event_id)
            revalidate_notification_event(db, event)
            return {"event_uid": event.get_uid(), "event_revision": 1, "created": False}
        row = AppNotificationDispatch(subscription_id=subscription.id, request_id=request_id,
                                      expires_at=SafeDateTime.now() + timedelta(minutes=10))
        db.insert(row)
        # Personal test notifications have no board or execution request. The
        # outbox event type selects this exact authority path before execution.
        event = AppExecutionOutbox(request_id=row.id, project_id=0, app_key=connection.app_key,
                                  connection_id=connection.id, event_type=APP_NOTIFICATION_EVENT)
        db.insert(event)
        event.payload = {"schema_version": 1, "event_uid": event.get_uid(), "event_type": APP_NOTIFICATION_EVENT,
            "app_key": connection.app_key, "connection_uid": connection.get_uid(),
            "notification": content, "notification_fence": {
                "event_revision": 1, "subscription_uid": subscription.get_uid(),
                "subscription_generation": subscription.generation,
                "destination_uid": destination.get_uid(), "destination_revision": destination.revision},
            "external_destination": {"kind": destination.proof["destination"]["kind"], "external_id": destination.proof["destination"]["external_id"]},
            "expires_at": _expires(row.expires_at).isoformat(),
            "destination": {"destination_uid": target.get_uid(), "revision": target.revision}}
        row.event_id = event.id; row.snapshot = event.payload
        db.update(row); db.update(event)
        from .AppEventDelivery import enqueue_event
        db.after_commit(lambda: enqueue_event(event.id))
        return {"event_uid": event.get_uid(), "event_revision": 1, "created": True}


def revalidate_notification_event(db, event, *, receipt_only=False):
    if event is None or event.event_type != APP_NOTIFICATION_EVENT:
        raise AppGovernanceDenied()
    dispatch = _current(db, AppNotificationDispatch, event.request_id)
    if dispatch is None or dispatch.event_id != event.id:
        raise AppGovernanceDenied()
    subscription, destination, connection = _current_subscription(db, dispatch.subscription_id)
    target, _ = current_destination(db, connection, APP_NOTIFICATION_EVENT)
    snapshot = dispatch.snapshot.get("payload", dispatch.snapshot)
    expected_project_id = 0
    if snapshot.get("notification", {}).get("kind") == "missed_summary":
        from .AppNotificationRecovery import revalidate_missed_summary
        expected_project_id = revalidate_missed_summary(db, event, dispatch)
    elif "source_fence" in dispatch.snapshot:
        from .AppNotificationActivity import revalidate_native_source
        expected_project_id = revalidate_native_source(db, dispatch, subscription, receipt_only=receipt_only)
    fence = snapshot.get("notification_fence", {})
    expected_fence = {"event_revision": 1, "subscription_uid": subscription.get_uid(),
                      "subscription_generation": subscription.generation,
                      "destination_uid": destination.get_uid(), "destination_revision": destination.revision}
    if (event.app_key != subscription.app_key or event.connection_id != subscription.connection_id
            or event.project_id != expected_project_id or event.payload != snapshot
            or fence != expected_fence
            or snapshot.get("event_uid") != event.get_uid()
            or snapshot.get("connection_uid") != connection.get_uid()
            or snapshot.get("expires_at") != _expires(dispatch.expires_at).isoformat()
            or snapshot.get("external_destination") != {
                "kind": destination.proof["destination"]["kind"], "external_id": destination.proof["destination"]["external_id"]}
            or snapshot.get("destination") != {"destination_uid": target.get_uid(), "revision": target.revision}
            or (not receipt_only and (event.state == "blocked" or _expires(dispatch.expires_at) <= SafeDateTime.now()))):
        raise AppGovernanceConflict()
    return dispatch, subscription


def _authorized_event(db, token, event_id, event_revision, subscription_generation, destination_revision, *, receipt_only=False):
    principal = authenticate_connection_credential(token)
    event = _current(db, AppExecutionOutbox, event_id)
    if event is None or event.app_key != principal.app_key or event.connection_id != principal.connection_id:
        raise AppGovernanceDenied()
    dispatch, subscription = revalidate_notification_event(db, event, receipt_only=receipt_only)
    expected = NotificationDeliveryFence(event.get_uid(), 1, subscription.generation, subscription.destination_revision)
    if NotificationDeliveryFence(event.get_uid(), event_revision, subscription_generation, destination_revision) != expected:
        raise AppGovernanceConflict()
    return dispatch, expected


def authorize_notification_delivery(token, event_id, *, event_revision, subscription_generation, destination_revision):
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        dispatch, fence = _authorized_event(db, token, event_id, event_revision, subscription_generation, destination_revision)
        # An unknown provider outcome requires metadata reconciliation, never
        # another send. Terminal receipts likewise cannot authorize provider I/O.
        if dispatch.receipt.get("status") in {"unknown", "confirmed", "rejected"}:
            raise AppGovernanceConflict()
        return {"authorized": True, **asdict(fence), "checked_at": SafeDateTime.now().isoformat()}


def record_notification_receipt(token, event_id, *, request_id, event_revision, subscription_generation,
                                destination_revision, status, expected_revision=None, reconciled=False,
                                provider_message_id=None, reason_code=None):
    _request(request_id)
    with DbSession.atomic() as db:
        lock_policy_mutation(db, exclusive=True)
        # A retained observation is not renewed permission to contact a provider.
        dispatch, fence = _authorized_event(db, token, event_id, event_revision, subscription_generation,
                                           destination_revision, receipt_only=True)
        incoming = NotificationDeliveryReceipt(fence, status, provider_message_id, reason_code)
        intent = {"receipt": asdict(incoming), "expected_revision": expected_revision, "reconciled": reconciled}
        previous = dispatch.receipt_requests.get(request_id)
        if previous is not None:
            if previous["intent"] != intent:
                raise AppGovernanceConflict()
            return previous["result"]
        # Preserve an earlier admission replay, but never admit new work after expiry.
        if status == "adapter_accepted" and (_expires(dispatch.expires_at) <= SafeDateTime.now()
                or _current(db, AppExecutionOutbox, event_id).state == "blocked"):
            raise AppGovernanceConflict()
        current = dispatch.receipt
        if current:
            current = NotificationDeliveryReceipt(NotificationDeliveryFence(**current["fence"]),
                current["status"], current["provider_message_id"], current["reason_code"], current["revision"])
        try:
            accepted = accept_notification_receipt(current or None, incoming, fence,
                expected_revision=expected_revision, reconciled=reconciled)
        except NotificationReceiptConflict as exc:
            raise AppGovernanceConflict() from exc
        if len(dispatch.receipt_requests) >= MAX_RECEIPT_REQUESTS:
            raise AppGovernanceConflict()
        result = {"event_uid": fence.event_uid, "receipt": asdict(accepted)}
        dispatch.receipt = asdict(accepted)
        dispatch.receipt_requests = {**dispatch.receipt_requests, request_id: {"intent": intent, "result": result}}
        db.update(dispatch)
        return result
