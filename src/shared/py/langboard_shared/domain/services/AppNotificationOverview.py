"""Bounded personal notification reads; metadata never grants delivery authority."""

from ...core.db import DbSession, SqlBuilder
from ...core.types import SafeDateTime, SnowflakeID
from ..models import AppExecutionOutbox
from ..models.AppNotificationSubscription import AppNotificationDispatch, AppNotificationSubscription
from ..models.AppNotificationDestination import AppNotificationDestination, AppNotificationDestinationChallenge
from .AppGovernance import AppGovernanceConflict, AppGovernanceDenied, _actor, lock_policy_mutation
from .AppConnectionAuthentication import _identity_hash
from .AppNotificationDestinations import _challenge_data, _destination_data, _expires, _managed_connection
from .AppNotificationDelivery import _data, revalidate_notification_event


def read_notification_onboarding(actor, connection_id):
    """Read one reviewed fixed entry; an authentication origin is never a fallback."""
    from urllib.parse import urlsplit
    from langboard_sdk.definition import validate_app_definition
    from .AppConnectionAuthentication import _connection, _manage
    from .AppNotificationDestinations import _definition
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        user = _actor(db, actor)
        connection = _connection(db, connection_id)
        _manage(db, user, connection)
        definition = _definition(db, connection)
        declaration = validate_app_definition(definition.declaration)
        onboarding = declaration.get("notification_onboarding")
        return {"connection_uid": connection.get_uid(), "app_key": connection.app_key,
                "app_revision": definition.edit_revision(),
                "connection_revision": _identity_hash(db, connection, definition=definition),
                "available": onboarding is not None,
                "entry": {"url": onboarding["url"], "origin": "https://" + urlsplit(onboarding["url"]).netloc,
                          "protocol_version": 1} if onboarding else None}


def _limit(value):
    if type(value) is not int or not 1 <= value <= 50:
        raise ValueError("Invalid notification page limit")
    return value


def read_connection_notification_settings(actor, connection_id, *, section="destinations", after=None, limit=25, request_id=None):
    """Enumerate only this user's rows, including retained revoked metadata."""
    _limit(limit)
    if request_id is not None:
        from .AppNotificationDestinations import _request
        _request(request_id)
        if section not in {"challenges", "subscriptions"}:
            raise ValueError("Request lookup is available for retained operations only")
    models = {"destinations": AppNotificationDestination, "challenges": AppNotificationDestinationChallenge,
              "subscriptions": AppNotificationSubscription}
    model = models.get(section)
    if model is None:
        raise ValueError("Invalid notification settings section")
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        user = _actor(db, actor)
        connection = _managed_connection(db, user, connection_id)
        query = SqlBuilder.select.table(model).where(model.user_id == user.id, model.connection_id == connection.id)
        if request_id is not None:
            query = query.where(model.request_id == request_id)
        if after is not None:
            query = query.where(model.id < after)
        rows = db.exec(query.order_by(model.id.desc()).limit(limit + 1)).all()
        items = []
        for row in rows[:limit]:
            if section == "challenges":
                item = _challenge_data(row)
                item["expired"] = _expires(row.expires_at) <= SafeDateTime.now()
            else:
                item = _destination_data(row) if section == "destinations" else _data(row)
                if section == "destinations":
                    item["display_name"] = row.proof.get("destination", {}).get("display_name", "")
            items.append(item)
        return {"connection_uid": connection.get_uid(), "connection_revision": _identity_hash(db, connection),
                "section": section, "items": items, "delivery_authority": False,
                "next_cursor": rows[limit - 1].get_uid() if len(rows) > limit else None}


def _status(dispatch, event):
    receipt = dispatch.receipt.get("status")
    if receipt in {"confirmed", "rejected", "unknown", "adapter_accepted"}:
        return receipt
    if event.state == "blocked":
        return "blocked"
    if _expires(dispatch.expires_at) <= SafeDateTime.now():
        return "expired"
    return {"pending": "pending", "delivering": "delivering", "delivered": "transport_delivered",
            "failed": "failed"}.get(event.state, "unavailable")


def list_personal_notification_outbox(actor, *, after=None, limit=25, subscription_id=None, request_id=None):
    """Read neutral owner receipts, optionally reconciling one original creation tuple."""
    _limit(limit)
    if (subscription_id is None) != (request_id is None):
        raise ValueError("Subscription and request identity must be supplied together")
    if subscription_id is not None:
        from .AppNotificationDestinations import _request

        _request(request_id)
        if after is not None:
            raise ValueError("Request reconciliation cannot use a pagination cursor")
        if (
            isinstance(subscription_id, bool)
            or not isinstance(subscription_id, int)
            or not 1 <= subscription_id <= SnowflakeID.MAX_VALUE
        ):
            raise ValueError("Invalid notification subscription identity")
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        user = _actor(db, actor)
        query = SqlBuilder.select.tables(AppNotificationDispatch, AppExecutionOutbox).join(
            AppNotificationSubscription, AppNotificationSubscription.id == AppNotificationDispatch.subscription_id).join(
            AppExecutionOutbox, AppExecutionOutbox.id == AppNotificationDispatch.event_id).where(
            AppNotificationSubscription.user_id == user.id)
        if subscription_id is not None:
            query = query.where(
                AppNotificationDispatch.subscription_id == subscription_id,
                AppNotificationDispatch.request_id == request_id,
            )
        if after is not None:
            query = query.where(AppNotificationDispatch.id < after)
        rows = db.exec(query.order_by(AppNotificationDispatch.id.desc()).limit(limit + 1)).all()
        # List retained neutral metadata in one bounded join. Sensitive source
        # content is resolved only for the selected detail through native ACL.
        result = {
            "items": [_outbox_data(dispatch, event) for dispatch, event in rows[:limit]],
            "next_cursor": rows[limit - 1][0].get_uid() if len(rows) > limit else None,
        }
        if subscription_id is not None:
            result["lookup"] = {
                "subscription_uid": SnowflakeID(subscription_id).to_short_code(),
                "request_id": request_id,
            }
        return result


def _outbox_data(dispatch, event):
    return {"dispatch_uid": dispatch.get_uid(), "event_uid": event.get_uid(),
            "subscription_uid": SnowflakeID(dispatch.subscription_id).to_short_code(),
            "created_at": _expires(dispatch.created_at).isoformat(),
            "expires_at": _expires(dispatch.expires_at).isoformat(), "status": _status(dispatch, event),
            "transport_state": event.state, "attempt_count": event.attempt_count,
            "receipt_revision": dispatch.receipt.get("revision"), "delivery_authority": False}


def read_personal_notification_outbox(actor, dispatch_id):
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        user = _actor(db, actor)
        row = db.exec(SqlBuilder.select.tables(AppNotificationDispatch, AppExecutionOutbox).join(
            AppNotificationSubscription, AppNotificationSubscription.id == AppNotificationDispatch.subscription_id).join(
            AppExecutionOutbox, AppExecutionOutbox.id == AppNotificationDispatch.event_id).where(
            AppNotificationSubscription.user_id == user.id, AppNotificationDispatch.id == dispatch_id)).first()
        if row is None:
            raise AppGovernanceDenied()
        dispatch, event = row
        current = True
        try:
            revalidate_notification_event(db, event, receipt_only=True)
        except (AppGovernanceConflict, AppGovernanceDenied):
            current = False
        return {**_outbox_data(dispatch, event), "content_available": current,
                "notification": event.payload.get("notification") if current else None}
