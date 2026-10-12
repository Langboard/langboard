"""Connection-scoped SDK request receipt reads; never runtime authorization."""

import json
from hashlib import sha256
from ...core.db import DbSession, SqlBuilder
from ...core.types import SafeDateTime
from ..models import AppConnectionCredential, AppExecutionActive, AppExecutionLease, CardExecutionIntent, User
from .AppConnectionAuthentication import _identity_hash, authenticate_connection_credential
from .AppGovernance import AppGovernanceDenied, _actor, _current
from .CardExecutionIntent import _configuration, _generation


def current_execution_configuration(db, intent, project_id, *, for_update=False):
    """Current immutable intent or its separately retained live Active handoff."""
    actor = _current(db, User, intent.actor_id, for_update=for_update)
    if actor is None:
        raise AppGovernanceDenied()
    actor = _actor(db, actor, for_update=for_update)
    active = db.exec(
        SqlBuilder.select.table(AppExecutionActive).where(AppExecutionActive.request_id == intent.id)
    ).first()
    card, connection, snapshot = _configuration(
        db,
        actor,
        project_id,
        intent.card_id,
        intent.app_key,
        intent.connection_id,
        for_update=for_update,
        workflow_stage="active" if active else "ready",
    )
    generation = _generation(db, intent.card_id, for_update=for_update)
    if (
        generation is None
        or generation.execution_generation != intent.execution_generation
        or snapshot != (active.snapshot if active else intent.snapshot)
    ):
        raise AppGovernanceDenied()
    if active:
        lease = _current(db, AppExecutionLease, active.lease_id, for_update=for_update)
        credential = (
            _current(db, AppConnectionCredential, lease.credential_id, for_update=for_update) if lease else None
        )
        now = SafeDateTime.now()
        if (
            lease is None
            or lease.request_id != intent.id
            or lease.state != "authorized"
            or lease.expires_at <= now
            or credential is None
            or credential.connection_id != connection.id
            or credential.revoked_at is not None
            or credential.expires_at <= now
            or credential.identity_hash != _identity_hash(db, connection)
        ):
            raise AppGovernanceDenied()
    return card, connection, snapshot


def read_app_execution_request(token, project_id, card_id, request_id, *, for_update=False):
    with DbSession.atomic() as db:
        principal = authenticate_connection_credential(token)
        intent = _current(db, CardExecutionIntent, request_id, for_update=False)
        if (
            intent is None
            or intent.card_id != card_id
            or intent.app_key != principal.app_key
            or intent.connection_id != principal.connection_id
        ):
            raise AppGovernanceDenied()
        card, connection, snapshot = current_execution_configuration(db, intent, project_id, for_update=for_update)
        if for_update:
            intent = _current(db, CardExecutionIntent, request_id)
        authority = {
            "schema_version": 1,
            "card_uid": card.get_uid(),
            "app_key": intent.app_key,
            "connection_uid": connection.get_uid(),
            "generation": intent.execution_generation,
            "resource_uids": sorted(snapshot["resource_revisions"]),
            "resource_revisions": snapshot["resource_revisions"],
            "card_revision": int(SafeDateTime.fromisoformat(snapshot["card_revision"]).timestamp() * 1000000),
            "ownership_revision": snapshot["ownership_revision"],
            "selection_revision": snapshot["selection_revision"],
            "binding_revision": snapshot["binding_revision"],
            "authority_version": sha256(
                json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        }
        return {
            "schema_version": 1,
            "request_uid": intent.get_uid(),
            "card_uid": card.get_uid(),
            "generation": intent.execution_generation,
            "state": "requested",
            "started": False,
            "authority": authority,
        }
