"""Human card exports using the native signed, durable app-event transport."""

from copy import deepcopy
from hashlib import sha256
from io import BytesIO
from uuid import UUID
from sqlalchemy import func, select, update
from ...core.db.DbEngine import DbEngine
from ...core.db import DbSession, SqlBuilder
from ...core.storage import Storage
from ...core.types import SafeDateTime, SnowflakeID
from ..models import AppConnection, AppDefinition, AppExecutionOutbox, AppResourceBinding, AppTransferRequest, BoardAppBinding, Card, CardAttachment, User
from ..models.ProjectRole import ProjectRoleAction
from .AppConnectionAuthentication import _connection, _identity_hash, authenticate_connection_credential
from .AppEventDestination import APP_TRANSFER_EVENT, current_destination
from .AppGovernance import AppGovernanceConflict, AppGovernanceDenied, _actor, _authorized_board, _current, lock_policy_mutation, require_connection_access, current_policy
from .AppExecutionGrant import AppExecutionCredentialDenied
from .DomainService import DomainService


TRANSFER_CAPABILITY = "transfers.receive"
MAX_ATTACHMENT_BYTES = 16 * 1024 * 1024


def _uuid(value):
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError("Canonical client request UUID required")
    return value


def _one(db, model, *conditions, lock=True):
    query = SqlBuilder.select.table(model).where(*conditions)
    return db.exec(query.with_for_update() if lock else query,
                   execution_options={"populate_existing": True}).first()


def _scope(db, actor, project_id, card_id, app_key, connection_id, resource_uid):
    if actor is None:
        raise AppGovernanceDenied()
    actor = _actor(db, actor, for_update=False)
    project = _authorized_board(db, actor, project_id, action=ProjectRoleAction.CardUpdate)
    card = _current(db, Card, card_id)
    if card is None or card.archived_at or card.source_type:
        raise AppGovernanceDenied()
    if DomainService().card.resolve_readable_card(project_id, card_id, actor) is None:
        raise AppGovernanceDenied()
    connection = _connection(db, connection_id)
    if connection.app_key != app_key:
        raise AppGovernanceDenied()
    require_connection_access(db, actor, project, connection, for_update=False)
    # The credential principal must retain its own current card ACL as well.
    owner = _current(db, User, connection.owner_id, for_update=False)
    require_connection_access(db, owner, project, connection, for_update=False)
    if DomainService().card.resolve_readable_card(project_id, card_id, owner) is None:
        raise AppGovernanceDenied()
    definition = _one(db, AppDefinition, AppDefinition.key == app_key)
    binding = _one(db, BoardAppBinding, BoardAppBinding.project_id == project_id, BoardAppBinding.app_key == app_key)
    required = {TRANSFER_CAPABILITY, "cards.read", "resources.read", "events.receive"}
    if (definition is None or not definition.is_enabled or binding is None or binding.state != "enabled"
        or not required.issubset(definition.declaration.get("capabilities", []))
        or not required.issubset(binding.granted_capabilities)):
        raise AppGovernanceDenied()
    resource = _current(db, AppResourceBinding, SnowflakeID.from_short_code(resource_uid))
    if (resource is None or resource.connection_id != connection.id or resource.board_binding_id != binding.id
        or not resource.is_selected or resource.access_state != "granted"
        or resource.resource_type not in definition.declaration.get("resource_types", [])
        or resource.identity_hash not in ("", _identity_hash(db, connection, definition=definition))):
        raise AppGovernanceDenied()
    destination, _ = current_destination(db, connection, APP_TRANSFER_EVENT)
    return card, {
        "app_revision": definition.edit_revision(), "binding_revision": binding.edit_revision(),
        "connection_identity": _identity_hash(db, connection, definition=definition),
        "resource_revision": resource.access_revision,
        "policy_revision": current_policy(db, project.organization_id)["revision"],
        "destination": {"destination_uid": destination.get_uid(), "revision": destination.revision},
    }


def _attachment(db, card, intent):
    uid = intent.get("attachment_uid")
    if uid is None:
        if intent.get("attachment_revision") is not None or intent.get("content_sha256") is not None:
            raise ValueError("Attachment identity required")
        return None
    attachment = _current(db, CardAttachment, SnowflakeID.from_short_code(uid))
    if (attachment is None or attachment.card_id != card.id or attachment.deleted_at or attachment.replaced_by_id):
        raise AppGovernanceDenied()
    if (attachment.image_revision != intent.get("attachment_revision") or not attachment.content_sha256
        or attachment.content_sha256 != intent.get("content_sha256")):
        raise AppGovernanceConflict()
    return attachment


def _event(db, row):
    event = _one(db, AppExecutionOutbox, AppExecutionOutbox.request_id == row.id,
                 AppExecutionOutbox.event_type == APP_TRANSFER_EVENT)
    if event is None:
        raise AppGovernanceDenied()
    return event


def _view(row, event):
    return {"transfer_uid": row.get_uid(), "client_request_id": row.client_request_id,
            "app_key": row.app_key, "intent": row.intent, "receipt": row.receipt,
            "delivery": {"event_uid": event.get_uid(), "state": event.state, "attempt_count": event.attempt_count,
                         "last_error": event.last_error, "history": event.delivery_history}, "started": False}


def _lock_transfer_request(db, actor_id, client_request_id):
    """Serialize only this sender request, including its initially absent receipt."""
    dialect = DbEngine.get_main_engine().dialect.name
    if dialect == "postgresql":
        key = f"langboard_app_transfer:{int(actor_id)}:{client_request_id}"
        lock_key = int.from_bytes(sha256(key.encode()).digest()[:8], "big", signed=True)
        db.exec(select(func.pg_advisory_xact_lock(lock_key)))
    elif dialect == "sqlite":
        db.exec(update(User).where(User.id == actor_id).values(id=User.id))
    else:
        raise ValueError("Transfer receipts require PostgreSQL or SQLite")


def create_transfer(actor, project_id, card_id, app_key, client_request_id, connection_id, intent, expected_authority):
    _uuid(client_request_id)
    if (not isinstance(intent, dict) or set(intent) != {"card_revision", "resource_uid", "attachment_uid", "attachment_revision", "content_sha256"}
        or type(intent["card_revision"]) is not int or intent["card_revision"] < 0):
        raise ValueError("Invalid transfer identity")
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        _lock_transfer_request(db, actor.id, client_request_id)
        # The scoped native transaction lock serializes first requests.
        actor = _actor(db, actor, for_update=False)
        row = _one(db, AppTransferRequest, AppTransferRequest.actor_id == actor.id,
                   AppTransferRequest.client_request_id == client_request_id, lock=False)
        if row:
            if (row.project_id, row.card_id, row.app_key, row.connection_id, row.intent) != (project_id, card_id, app_key, connection_id, intent):
                raise AppGovernanceConflict()
            if row.authority != expected_authority:
                raise AppGovernanceConflict()
            event = _event(db, row)
            revalidate_transfer_event(db, event)
            return {**_view(row, event), "changed": False}
        card, authority = _scope(db, actor, project_id, card_id, app_key, connection_id, intent["resource_uid"])
        if authority != expected_authority or card.last_change_seq != intent["card_revision"]:
            raise AppGovernanceConflict()
        _attachment(db, card, intent)
        row = AppTransferRequest(actor_id=actor.id, project_id=project_id, card_id=card_id,
                                 app_key=app_key, connection_id=connection_id, client_request_id=client_request_id,
                                 intent=intent, authority=authority,
                                 receipt={"revision": 0, "acknowledgment": None, "provider_outcome": None, "retry_revision": 0, "history": []})
        db.insert(row)
        event = AppExecutionOutbox(request_id=row.id, project_id=project_id, app_key=app_key,
                                   connection_id=connection_id, event_type=APP_TRANSFER_EVENT)
        event.payload = {"schema_version": 1, "event_uid": event.get_uid(), "event_type": APP_TRANSFER_EVENT,
                         "request_uid": row.get_uid(), "board_uid": SnowflakeID(project_id).to_short_code(),
                         "card_uid": card.get_uid(), "app_key": app_key,
                         "connection_uid": SnowflakeID(connection_id).to_short_code(), **intent,
                         "destination": authority["destination"], "started": False}
        db.insert(event)
        from .AppEventDelivery import enqueue_event
        db.after_commit(lambda: enqueue_event(event.id))
        return {**_view(row, event), "changed": True}


def revalidate_transfer_event(db, event):
    row = _current(db, AppTransferRequest, event.request_id)
    if (row is None or row.project_id != event.project_id or row.connection_id != event.connection_id or row.app_key != event.app_key):
        raise AppGovernanceDenied()
    actor = _current(db, User, row.actor_id, for_update=False)
    card, authority = _scope(db, actor, row.project_id, row.card_id, row.app_key, row.connection_id, row.intent["resource_uid"])
    if authority != row.authority or card.last_change_seq != row.intent["card_revision"]:
        raise AppGovernanceConflict()
    _attachment(db, card, row.intent)
    expected = {"schema_version": 1, "event_uid": event.get_uid(), "event_type": APP_TRANSFER_EVENT,
                "request_uid": row.get_uid(), "board_uid": SnowflakeID(row.project_id).to_short_code(),
                "card_uid": card.get_uid(), "app_key": row.app_key,
                "connection_uid": SnowflakeID(row.connection_id).to_short_code(), **row.intent,
                "destination": row.authority["destination"], "started": False}
    if event.payload != expected:
        raise AppGovernanceConflict()
    return row, card


def read_sender_transfer(actor, project_id, client_request_id):
    _uuid(client_request_id)
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        actor = _actor(db, actor, for_update=False)
        row = _one(db, AppTransferRequest, AppTransferRequest.actor_id == actor.id,
                   AppTransferRequest.client_request_id == client_request_id, AppTransferRequest.project_id == project_id, lock=False)
        if row is None:
            return {"found": False}
        # Status remains readable after app revocation, but native card ACL still applies.
        if DomainService().card.resolve_readable_card(project_id, row.card_id, actor) is None:
            raise AppGovernanceDenied()
        return {"found": True, **_view(row, _event(db, row))}


def retry_transfer(actor, project_id, client_request_id, expected_retry_revision):
    if type(expected_retry_revision) is not int:
        raise ValueError("Retry revision required")
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        actor = _actor(db, actor, for_update=False)
        row = _one(db, AppTransferRequest, AppTransferRequest.actor_id == actor.id,
                   AppTransferRequest.client_request_id == _uuid(client_request_id), AppTransferRequest.project_id == project_id, lock=False)
        if row is None:
            raise AppGovernanceDenied()
        event = _event(db, row)
        row, _ = revalidate_transfer_event(db, event)
        revision = row.receipt["retry_revision"]
        if revision == expected_retry_revision + 1:
            return {**_view(row, event), "changed": False}
        if (revision != expected_retry_revision or event.state != "failed"
            or row.receipt["acknowledgment"] is not None):
            raise AppGovernanceConflict()
        row.receipt = {**row.receipt, "retry_revision": revision + 1,
                       "history": [*row.receipt["history"], {"kind": "retry_requested", "revision": revision + 1, "at": SafeDateTime.now().isoformat()}]}
        event.state = "pending"
        db.update(row)
        db.update(event)
        from .AppEventDelivery import enqueue_event
        db.after_commit(lambda: enqueue_event(event.id))
        return {**_view(row, event), "changed": True}


def transfer_attempt_limit(db, event, default):
    row = _current(db, AppTransferRequest, event.request_id)
    if row is None:
        raise AppGovernanceDenied()
    return default * (1 + row.receipt["retry_revision"])


def _app_scope(db, token, project_id, transfer_id):
    try:
        principal = authenticate_connection_credential(token)
    except AppGovernanceDenied as exc:
        raise AppExecutionCredentialDenied() from exc
    row = _current(db, AppTransferRequest, transfer_id, for_update=False)
    if (row is None or row.project_id != project_id or row.app_key != principal.app_key or row.connection_id != principal.connection_id):
        raise AppGovernanceDenied()
    event = _event(db, row)
    row, card = revalidate_transfer_event(db, event)
    return row, event, card


def read_app_transfer(token, project_id, transfer_id, *, offset=0, limit=16000):
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 16000:
        raise ValueError("Invalid content window")
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        row, event, card = _app_scope(db, token, project_id, transfer_id)
        content = card.description.content
        return {**_view(row, event), "title": card.title, "description": content[offset:offset + limit],
                "next_offset": offset + limit if offset + limit < len(content) else None}


class _BoundedBytes(BytesIO):
    def write(self, data):
        if self.tell() + len(data) > MAX_ATTACHMENT_BYTES:
            raise ValueError("Transfer attachment exceeds byte limit")
        return super().write(data)


def read_transfer_attachment(token, project_id, transfer_id):
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        row, _, card = _app_scope(db, token, project_id, transfer_id)
        attachment = _attachment(db, card, row.intent)
        if attachment is None:
            raise AppGovernanceDenied()
        file = deepcopy(attachment.file)
        identity = (file.storage_type, file.storage_name, file.filename)
        revision, digest = attachment.image_revision, row.intent["content_sha256"]
    # No database lease or policy lock spans storage/network I/O.
    target = _BoundedBytes()
    if not Storage.download_file(file, target):
        raise AppGovernanceDenied()
    body = target.getvalue()
    if sha256(body).hexdigest() != digest:
        raise AppGovernanceConflict()
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        row, _, card = _app_scope(db, token, project_id, transfer_id)
        current = _attachment(db, card, row.intent)
        if (current is None or current.image_revision != revision or current.content_sha256 != digest
            or (current.file.storage_type, current.file.storage_name, current.file.filename) != identity):
            raise AppGovernanceConflict()
        return body, digest


def report_transfer(token, project_id, transfer_id, expected_revision, acknowledgment, provider_outcome):
    if (type(expected_revision) is not int or expected_revision < 0 or acknowledgment != "received"
        or provider_outcome not in (None, "unknown", "failed", "delivered", "already_delivered")):
        raise ValueError("Invalid transfer receipt")
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        row, event, _ = _app_scope(db, token, project_id, transfer_id)
        previous = row.receipt
        same = previous["acknowledgment"] == acknowledgment and previous["provider_outcome"] == provider_outcome
        if previous["revision"] == expected_revision + 1 and same:
            return {**_view(row, event), "changed": False}
        if (previous["revision"] != expected_revision or previous["provider_outcome"] in ("delivered", "already_delivered")
            or event.attempt_count < 1):
            raise AppGovernanceConflict()
        row.receipt = {**previous, "revision": expected_revision + 1, "acknowledgment": acknowledgment,
                       "provider_outcome": provider_outcome, "outcome_source": "authenticated_app_report",
                       "history": [*previous["history"], {"kind": "app_report", "acknowledgment": acknowledgment,
                           "provider_outcome": provider_outcome, "revision": expected_revision + 1, "at": SafeDateTime.now().isoformat()}]}
        db.update(row)
        return {**_view(row, event), "changed": True}


def review_transfer(actor, project_id, card_id, app_key, attachment_id=None, *, after_id=None):
    """Bounded current destinations and source identity for the human review dialog."""
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        actor = _actor(db, actor, for_update=False)
        card = _current(db, Card, card_id, for_update=False)
        if card is None or DomainService().card.resolve_readable_card(project_id, card_id, actor) is None:
            raise AppGovernanceDenied()
        attachment = _current(db, CardAttachment, attachment_id, for_update=False) if attachment_id is not None else None
        if attachment_id is not None and (attachment is None or attachment.card_id != card.id or attachment.deleted_at or attachment.replaced_by_id or not attachment.content_sha256):
            raise AppGovernanceDenied()
        intent = {"card_revision": card.last_change_seq, "resource_uid": None,
                  "attachment_uid": attachment.get_uid() if attachment else None,
                  "attachment_revision": attachment.image_revision if attachment else None,
                  "content_sha256": attachment.content_sha256 if attachment else None}
        binding = _one(db, BoardAppBinding, BoardAppBinding.project_id == project_id, BoardAppBinding.app_key == app_key)
        if binding is None:
            raise AppGovernanceDenied()
        query = SqlBuilder.select.table(AppResourceBinding).where(AppResourceBinding.board_binding_id == binding.id,
                    AppResourceBinding.is_selected == True, AppResourceBinding.access_state == "granted")  # noqa: E712
        if after_id is not None:
            query = query.where(AppResourceBinding.id > after_id)
        rows = db.exec(query.order_by(AppResourceBinding.id).limit(51)).all()
        targets = []
        for resource in rows[:50]:
            try:
                _, authority = _scope(db, actor, project_id, card_id, app_key, resource.connection_id, resource.get_uid())
            except (AppGovernanceDenied, AppGovernanceConflict):
                continue
            targets.append({"connection_uid": SnowflakeID(resource.connection_id).to_short_code(),
                            "resource_uid": resource.get_uid(), "resource_type": resource.resource_type, "authority": authority,
                            "external_resource_id": resource.external_resource_id})
        _attachment(db, card, intent)
        return {"intent": intent, "title": card.title, "attachment_name": attachment.filename if attachment else None,
                "targets": targets, "next_cursor": rows[49].get_uid() if len(rows) > 50 else None}
