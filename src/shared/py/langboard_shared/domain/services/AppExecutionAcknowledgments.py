"""Current app-authenticated request readback and idempotent received receipt."""

from ...core.db import DbSession, SqlBuilder
from ...core.types import SafeDateTime, SnowflakeID
from ..models import AppExecutionAcknowledgment, AppExecutionOutbox, AppExecutionRequest
from .AppEventDelivery import _expired
from .AppExecutionGrant import evaluate_accepted_execution_grant
from .AppConnectionAuthentication import authenticate_connection_credential
from .AppExecutionRequests import AppExecutionRequestConflict
from .AppGovernance import AppGovernanceDenied, lock_policy_mutation


def _request(db, token, project_id, card_id, request_id):
    # Match request location before retrieving resource metadata; current app
    # credential/ownership/consent remains mandatory even for old receipts.
    row = db.exec(
        SqlBuilder.select.table(AppExecutionRequest).where(
            AppExecutionRequest.id == request_id,
            AppExecutionRequest.project_id == project_id,
            AppExecutionRequest.card_id == card_id,
        )
    ).first()
    if row is None:
        raise AppGovernanceDenied()
    principal = authenticate_connection_credential(token)
    if principal.app_key != row.app_key or principal.connection_id != row.connection_id:
        raise AppGovernanceDenied()
    current = evaluate_accepted_execution_grant(db, row)
    if current["app_key"] != row.app_key or current["connection_uid"] != SnowflakeID(row.connection_id).to_short_code():
        raise AppGovernanceDenied()
    if current["authority_version"] != row.authority.get("authority_version"):
        raise AppExecutionRequestConflict()
    return row


def read_app_execution_request(token, project_id, card_id, request_id):
    with DbSession.atomic() as db:
        row = _request(db, token, project_id, card_id, request_id)
        from ..models import AppExecutionActive
        handoff = db.exec(SqlBuilder.select.table(AppExecutionActive).where(
            AppExecutionActive.request_id == row.id
        )).first()
        return {
            "schema_version": 1,
            "request_uid": row.get_uid(),
            "card_uid": SnowflakeID(card_id).to_short_code(),
            "generation": row.generation,
            "state": "requested",
            "started": False,
            "authority": handoff.snapshot if handoff else row.authority,
        }


def acknowledge_app_execution(token, project_id, card_id, request_id, event_id, runtime_reference):
    if not isinstance(runtime_reference, str) or not runtime_reference.strip() or len(runtime_reference) > 200:
        raise ValueError("Runtime reference must contain 1 to 200 characters")
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        # Match the delivery worker's event -> project/card lock order.
        event = db.exec(
            SqlBuilder.select.table(AppExecutionOutbox)
            .where(
                AppExecutionOutbox.id == event_id,
                AppExecutionOutbox.request_id == request_id,
            )
            .with_for_update()
        ).first()
        request = _request(db, token, project_id, card_id, request_id)
        if (
            event is None
            or event.connection_id != request.connection_id
            or event.app_key != request.app_key
            or event.project_id != project_id
            or event.event_type != "io.langboard.app.execution.requested.v1"
            or event.payload.get("event_uid") != event.get_uid()
            or event.payload.get("request_uid") != request.get_uid()
            or event.payload.get("card_uid") != SnowflakeID(card_id).to_short_code()
            or event.payload.get("generation") != request.generation
            or event.state not in ("delivering", "delivered")
            or (event.state == "delivering" and _expired(event.lease_until, SafeDateTime.now()))
        ):
            raise AppGovernanceDenied()
        row = db.exec(
            SqlBuilder.select.table(AppExecutionAcknowledgment)
            .where(
                AppExecutionAcknowledgment.request_id == request.id,
            )
            .with_for_update()
        ).first()
        changed = row is None
        if row is None:
            row = AppExecutionAcknowledgment(
                request_id=request.id,
                event_id=event.id,
                connection_id=request.connection_id,
                app_key=request.app_key,
                runtime_reference=runtime_reference,
            )
            db.insert(row)
        elif row.event_id != event.id or row.runtime_reference != runtime_reference:
            raise AppExecutionRequestConflict()
        return {
            "schema_version": 1,
            "acknowledgment_uid": row.get_uid(),
            "request_uid": request.get_uid(),
            "event_uid": event.get_uid(),
            "runtime_reference": row.runtime_reference,
            "state": "received",
            "started": False,
            "changed": changed,
        }


def read_execution_acknowledgment(token, project_id, card_id, request_id):
    """Read a received receipt under the same current request authority."""
    with DbSession.atomic() as db:
        request = _request(db, token, project_id, card_id, request_id)
        row = db.exec(SqlBuilder.select.table(AppExecutionAcknowledgment).where(
            AppExecutionAcknowledgment.request_id == request.id,
            AppExecutionAcknowledgment.app_key == request.app_key,
            AppExecutionAcknowledgment.connection_id == request.connection_id,
        )).first()
        if row is None:
            return {"schema_version": 1, "request_uid": request.get_uid(),
                    "state": "not_received", "started": False, "changed": False}
        return {"schema_version": 1, "acknowledgment_uid": row.get_uid(),
                "request_uid": request.get_uid(), "event_uid": SnowflakeID(row.event_id).to_short_code(),
                "runtime_reference": row.runtime_reference, "state": "received",
                "started": False, "changed": False}
