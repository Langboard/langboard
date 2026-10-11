"""Idempotent delivery acknowledgment with current authority; no execution grant."""

from ...core.db import DbSession, SqlBuilder
from ..models import AppConnectionCredential, AppExecutionAcknowledgment, AppExecutionOutbox
from .AppConnectionAuthentication import authenticate_connection_credential
from .AppExecutionRequestRead import read_app_execution_request
from .AppGovernance import AppGovernanceConflict, AppGovernanceDenied, _current, lock_policy_mutation


def acknowledge_execution_request(token, project_id, card_id, request_id, event_id, runtime_reference):
    if not isinstance(runtime_reference, str) or not runtime_reference.strip() or len(runtime_reference) > 200:
        raise ValueError("A runtime reference of 1 to 200 characters is required")
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        principal = authenticate_connection_credential(token)
        receipt = read_app_execution_request(token, project_id, card_id, request_id, for_update=True)
        # Serialize with credential revocation, then authenticate again after waiting.
        _current(db, AppConnectionCredential, principal.credential_id)
        authenticate_connection_credential(token)
        event = _current(db, AppExecutionOutbox, event_id)
        if (
            event is None
            or event.request_id != request_id
            or event.project_id != project_id
            or event.app_key != principal.app_key
            or event.connection_id != principal.connection_id
            or event.event_type != "io.langboard.app.execution.requested.v1"
            or event.state not in ("delivering", "delivered")
            or event.payload.get("card_uid") != card_id.to_short_code()
            or event.payload.get("request_uid") != request_id.to_short_code()
            or event.payload.get("event_uid") != event.get_uid()
            or event.payload.get("generation") != receipt["generation"]
        ):
            raise AppGovernanceDenied()
        row = db.exec(
            SqlBuilder.select.table(AppExecutionAcknowledgment).where(
                AppExecutionAcknowledgment.request_id == request_id
            )
        ).first()
        changed = row is None
        if row is None:
            row = AppExecutionAcknowledgment(
                request_id=request_id,
                event_id=event_id,
                app_key=principal.app_key,
                connection_id=principal.connection_id,
                runtime_reference=runtime_reference,
            )
            db.insert(row)
        elif row.event_id != event_id or row.runtime_reference != runtime_reference:
            raise AppGovernanceConflict()
        return {
            "schema_version": 1,
            "acknowledgment_uid": row.get_uid(),
            "request_uid": request_id.to_short_code(),
            "event_uid": event.get_uid(),
            "runtime_reference": row.runtime_reference,
            "state": "received",
            "started": False,
            "changed": changed,
        }
