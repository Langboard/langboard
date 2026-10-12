"""Accept once per generation, rechecking authority even for duplicate requests."""

from ...core.db import DbSession, SqlBuilder
from ...core.types import SnowflakeID
from ..models import AppExecutionOutbox, AppExecutionRequest
from .AppExecutionGrant import evaluate_current_execution_grant
from .AppGovernance import AppGovernanceDenied


class AppExecutionRequestConflict(Exception):
    pass


def request_app_execution(token, project_id, card_id, generation, *, expected_authority_version):
    with DbSession.atomic() as db:
        # Current authority locks project/card before idempotency lookup. Reading an
        # old receipt must never bypass current consent or resource revocation.
        authority = evaluate_current_execution_grant(token, project_id, card_id, generation)
        if authority["authority_version"] != expected_authority_version:
            raise AppExecutionRequestConflict()
        return accept_authorized_execution_request(db, authority, project_id, card_id, generation)


def accept_authorized_execution_request(db, authority, project_id, card_id, generation):
    """Internal persistence shared by current app authority and explicit human resume."""
    row = db.exec(
        SqlBuilder.select.table(AppExecutionRequest).where(
            AppExecutionRequest.card_id == card_id,
            AppExecutionRequest.generation == generation,
        )
    ).first()
    if row:
        if row.app_key != authority["app_key"] or row.connection_id != SnowflakeID.from_short_code(
            authority["connection_uid"]
        ):
            raise AppExecutionRequestConflict()
        if row.authority.get("authority_version") != authority["authority_version"]:
            raise AppExecutionRequestConflict()
        if row.project_id != project_id:
            raise AppGovernanceDenied()
        changed = False
    else:
        row = AppExecutionRequest(
            project_id=project_id,
            card_id=card_id,
            app_key=authority["app_key"],
            connection_id=SnowflakeID.from_short_code(authority["connection_uid"]),
            generation=generation,
            authority=authority,
        )
        db.insert(row)
        event = AppExecutionOutbox(
            request_id=row.id,
            project_id=row.project_id,
            app_key=row.app_key,
            connection_id=row.connection_id,
            event_type="io.langboard.app.execution.requested.v1",
        )
        db.insert(event)
        event.payload = {
            "schema_version": 1,
            "event_uid": event.get_uid(),
            "event_type": event.event_type,
            "app_key": row.app_key,
            "connection_uid": SnowflakeID(row.connection_id).to_short_code(),
            "board_uid": SnowflakeID(row.project_id).to_short_code(),
            "card_uid": SnowflakeID(row.card_id).to_short_code(),
            "request_uid": row.get_uid(),
            "generation": row.generation,
            "resource_uids": row.authority["resource_uids"],
            "state": "requested",
            "started": False,
        }
        db.update(event)
        _enqueue_execution_event(db, event)
        changed = True
    return {
        "schema_version": 1,
        "request_uid": row.get_uid(),
        "card_uid": SnowflakeID(card_id).to_short_code(),
        "generation": row.generation,
        "state": "requested",
        "started": False,
        "changed": changed,
        "authority": row.authority,
    }


def _enqueue_execution_event(db, event):
    """Freeze an already approved destination; recovery retains broker failures."""
    from ..models import AppConnection, AppDefinition, BoardAppBinding
    from .AppEventDestination import current_destination
    from .AppGovernance import AppGovernanceConflict

    definition = db.exec(SqlBuilder.select.table(AppDefinition).where(AppDefinition.key == event.app_key)).first()
    binding = db.exec(SqlBuilder.select.table(BoardAppBinding).where(
        BoardAppBinding.project_id == event.project_id, BoardAppBinding.app_key == event.app_key
    )).first()
    if (definition is None or "events.receive" not in definition.declaration.get("capabilities", [])
        or binding is None or "events.receive" not in binding.granted_capabilities):
        return
    connection = db.exec(SqlBuilder.select.table(AppConnection).where(AppConnection.id == event.connection_id)).first()
    if connection is None:
        return
    try:
        destination, _ = current_destination(db, connection, event.event_type)
    except (AppGovernanceDenied, AppGovernanceConflict):
        # Preserve acceptance independently of unavailable external delivery.
        # No destination is guessed or retargeted after acceptance.
        return
    event.payload = {**event.payload, "destination": {
        "destination_uid": destination.get_uid(), "revision": destination.revision,
    }}
    db.update(event)

    def enqueue():
        from .AppEventDelivery import enqueue_event

        enqueue_event(event.id)

    db.after_commit(enqueue)


def current_execution_request_scope(db, token, project_id, card_id, request_id, *, capability, role_action):
    from .AppCardScope import require_app_card_scope
    from .AppConnectionAuthentication import authenticate_connection_credential
    from .AppExecutionGrant import AppExecutionCredentialDenied
    from .AppGovernance import _current
    from ..models import AppDefinition

    try:
        principal = authenticate_connection_credential(token)
    except AppGovernanceDenied as exc:
        raise AppExecutionCredentialDenied() from exc
    card, owner, selection, binding, resources = require_app_card_scope(
        db, principal, project_id, card_id, capability=capability, role_action=role_action
    )
    request = _current(db, AppExecutionRequest, request_id)
    definition = db.exec(SqlBuilder.select.table(AppDefinition).where(AppDefinition.key == principal.app_key)).first()
    if (request is None or request.card_id != card_id or request.project_id != project_id
        or request.connection_id != principal.connection_id or request.app_key != principal.app_key
        or set(request.authority.get("resource_uids", [])) != set(selection.resource_uids)
        or request.authority.get("ownership_revision") != owner.revision
        or request.authority.get("selection_revision") != selection.revision
        or request.authority.get("resource_revisions") != {row.get_uid(): row.access_revision for row in resources}
        or "resources.read" not in definition.declaration.get("capabilities", [])
        or "resources.read" not in binding.granted_capabilities):
        raise AppGovernanceDenied()
    return principal, card, binding, request, definition
