"""Two authenticated sides and explicit host-user approval; no implicit delegation."""

import re
from datetime import UTC, timedelta
from hashlib import sha256
from hmac import compare_digest
from json import dumps
from secrets import token_urlsafe
from urllib.parse import urlsplit
from ...core.db import DbSession, SqlBuilder
from ...core.types import SafeDateTime, SnowflakeID
from ..models import AppConnectionCredential, AppDefinition, User
from ..models.AppNotificationDestination import AppNotificationDestination, AppNotificationDestinationChallenge
from .AppConnectionAuthentication import (
    _connection, _identity_hash, _manage, authenticate_connection_credential,
)
from .AppGovernance import AppGovernanceConflict, AppGovernanceDenied, _actor, _current, lock_policy_mutation


CHALLENGE_LIFETIME_SECONDS = 600


def _text(value, maximum):
    if (not isinstance(value, str) or not value or value != value.strip() or len(value) > maximum
            or any(ord(character) < 32 or ord(character) == 127 for character in value)):
        raise ValueError("Invalid notification destination field")
    return value


def _request(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", value):
        raise ValueError("Invalid notification request identity")
    return value


def _positive(value):
    if type(value) is not int or value < 1:
        raise ValueError("Invalid notification revision")
    return value


def _expires(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _definition(db, connection):
    definition = db.exec(
        SqlBuilder.select.table(AppDefinition).where(AppDefinition.key == connection.app_key),
        execution_options={"populate_existing": True},
    ).first()
    if definition is None or not definition.is_enabled or "events.receive" not in definition.declaration.get("capabilities", []):
        raise AppGovernanceDenied()
    return definition


def _managed_connection(db, actor, connection_id):
    connection = _connection(db, connection_id)
    _manage(db, actor, connection)
    _definition(db, connection)
    return connection


def _challenge(db, actor, identifier):
    user = _actor(db, actor)
    row = _current(db, AppNotificationDestinationChallenge, identifier)
    if row is None or row.user_id != user.id:
        raise AppGovernanceDenied()
    return row


def _current_challenge_connection(db, row):
    connection = _connection(db, row.connection_id)
    if connection.app_key != row.app_key or _identity_hash(db, connection) != row.connection_identity:
        raise AppGovernanceConflict()
    _definition(db, connection)
    return connection


def _readable_project_ids(actor, project_ids):
    from . import DomainService
    from ...core.security.CollaborationChannel import CollaborationChannel

    with DomainService.use() as service:
        return set(service.card.resolve_work_visibility_contexts(actor, CollaborationChannel.Api, project_ids=project_ids))


def _require_challenge_project(user, row):
    if row.project_id is not None and row.project_id not in _readable_project_ids(user, {int(row.project_id)}):
        raise AppGovernanceDenied()


def _challenge_data(row):
    # The initiating user may review both identities. No token or token hash is
    # returned from read/replay routes, and no other user can enumerate this row.
    return {
        "challenge_uid": row.get_uid(), "connection_uid": SnowflakeID(row.connection_id).to_short_code(),
        "app_key": row.app_key, "kind": row.kind, "state": row.state, "revision": row.revision,
        "expires_at": _expires(row.expires_at).isoformat(),
        "proof_uid": SnowflakeID(row.proof_id).to_short_code() if row.proof_id else None,
        "proof": row.proof or None,
        **({"project_uid": SnowflakeID(row.project_id).to_short_code()} if row.project_id is not None else {}),
        "destination_uid": SnowflakeID(row.destination_id).to_short_code() if row.destination_id else None,
    }


def _destination_data(row):
    return {
        "destination_uid": row.get_uid(), "connection_uid": SnowflakeID(row.connection_id).to_short_code(),
        "app_key": row.app_key, "kind": row.proof["destination"]["kind"], "revision": row.revision, "state": row.state,
    }


def create_destination_challenge(actor, connection_id, *, request_id, expected_connection_revision, kind="personal", project_uid=None):
    _request(request_id)
    if kind not in {"personal", "shared"}:
        raise ValueError("Invalid notification destination kind")
    if not isinstance(expected_connection_revision, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_connection_revision):
        raise ValueError("Invalid connection revision")
    project_id = None
    if project_uid is not None:
        if kind != "shared" or not isinstance(project_uid, str) or not re.fullmatch(r"[A-Za-z0-9]{11}", project_uid):
            raise ValueError("Invalid shared challenge board")
        project_id = SnowflakeID.from_short_code(project_uid)
    with DbSession.atomic() as db:
        # Consent writes are rare. Reuse the app policy mutation lock so app,
        # identity and policy revocation cannot race an approval transaction.
        lock_policy_mutation(db, exclusive=True)
        connection = _managed_connection(db, actor, connection_id)
        if _identity_hash(db, connection) != expected_connection_revision:
            raise AppGovernanceConflict()
        existing = db.exec(SqlBuilder.select.table(AppNotificationDestinationChallenge).where(
            AppNotificationDestinationChallenge.user_id == actor.id,
            AppNotificationDestinationChallenge.connection_id == connection.id,
            AppNotificationDestinationChallenge.request_id == request_id,
        ).with_for_update()).first()
        if existing:
            if existing.kind != kind or existing.project_id != project_id:
                raise AppGovernanceConflict()
            # A lost issuance response is reconciled by identity; never mint a
            # second secret or rotate the original challenge on a retry.
            _require_challenge_project(_actor(db, actor), existing)
            return _challenge_data(existing)
        token = token_urlsafe(32)
        row = AppNotificationDestinationChallenge(
            user_id=actor.id, connection_id=connection.id, app_key=connection.app_key,
            request_id=request_id, kind=kind, project_id=project_id, connection_identity=_identity_hash(db, connection),
            token_hash=sha256(token.encode()).hexdigest(),
            expires_at=SafeDateTime.now() + timedelta(seconds=CHALLENGE_LIFETIME_SECONDS),
        )
        _require_challenge_project(_actor(db, actor), row)
        db.insert(row)
        return {**_challenge_data(row), "challenge_token": token}


def read_destination_challenge(actor, challenge_id):
    with DbSession.atomic() as db:
        return _challenge_data(_challenge(db, actor, challenge_id))


def _proof(definition, external_identity, destination, *, kind="personal"):
    if not isinstance(external_identity, dict) or set(external_identity) != {"issuer", "subject"}:
        raise ValueError("Invalid external identity proof")
    if not isinstance(destination, dict) or set(destination) != {"kind", "external_id", "display_name"} or destination["kind"] != kind or kind not in {"personal", "shared"}:
        raise ValueError("Invalid personal notification destination")
    issuer = _text(external_identity["issuer"], 500)
    try:
        parsed = urlsplit(issuer)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Invalid issuer")
        origin = f"https://{parsed.hostname}" + (f":{parsed.port}" if parsed.port and parsed.port != 443 else "")
    except ValueError as exc:
        raise ValueError("Invalid external identity issuer") from exc
    if origin not in definition.declaration.get("trust", {}).get("authentication_origins", []):
        raise AppGovernanceDenied()
    return {
        "external_identity": {"issuer": issuer, "subject": _text(external_identity["subject"], 255)},
        "destination": {
            "kind": kind, "external_id": _text(destination["external_id"], 200),
            "display_name": _text(destination["display_name"], 80),
        },
    }


def _app_challenge(db, principal, challenge_id, challenge_token):
    row = _current(db, AppNotificationDestinationChallenge, challenge_id)
    if (row is None or row.connection_id != principal.connection_id or row.app_key != principal.app_key
            or not isinstance(challenge_token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", challenge_token)
            or not compare_digest(row.token_hash, sha256(challenge_token.encode()).hexdigest())):
        raise AppGovernanceDenied()
    connection = _current_challenge_connection(db, row)
    user = _current(db, User, row.user_id)
    if user is None or user.deleted_at or not user.activated_at:
        raise AppGovernanceDenied()
    _manage(db, user, connection)
    _require_challenge_project(user, row)
    return row, connection


def read_shared_challenge_scope(token, challenge_id, *, challenge_token):
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        principal = authenticate_connection_credential(token)
        row, connection = _app_challenge(db, principal, challenge_id, challenge_token)
        if (row.kind != "shared" or row.project_id is None or row.state not in {"pending", "awaiting_user_approval"}
                or _expires(row.expires_at) <= SafeDateTime.now()):
            raise AppGovernanceDenied()
        return {
            "challenge_uid": row.get_uid(), "connection_uid": connection.get_uid(),
            "project_uid": SnowflakeID(row.project_id).to_short_code(),
            "expires_at": _expires(row.expires_at).isoformat(),
        }


def submit_destination_proof(token, challenge_id, *, challenge_token, request_id, external_identity, destination):
    _request(request_id)
    with DbSession.atomic() as db:
        lock_policy_mutation(db, exclusive=True)
        principal = authenticate_connection_credential(token)
        row, connection = _app_challenge(db, principal, challenge_id, challenge_token)
        proof = _proof(_definition(db, connection), external_identity, destination, kind=row.kind)
        if row.state != "pending":
            if row.state == "awaiting_user_approval" and row.proof_request_id == request_id and row.proof == proof:
                return {"proof_uid": SnowflakeID(row.proof_id).to_short_code(), "challenge_uid": row.get_uid(), "revision": row.revision, "state": row.state}
            raise AppGovernanceConflict()
        if _expires(row.expires_at) <= SafeDateTime.now():
            raise AppGovernanceConflict()
        row.proof_id = SnowflakeID()
        row.proof_credential_id = principal.credential_id
        row.proof_request_id = request_id
        row.proof = proof
        row.state = "awaiting_user_approval"
        row.revision += 1
        db.update(row)
        return {"proof_uid": SnowflakeID(row.proof_id).to_short_code(), "challenge_uid": row.get_uid(), "revision": row.revision, "state": row.state}


def approve_destination_challenge(actor, challenge_id, *, expected_revision, proof_id):
    _positive(expected_revision)
    with DbSession.atomic() as db:
        lock_policy_mutation(db, exclusive=True)
        row = _challenge(db, actor, challenge_id)
        if row.revision != expected_revision or row.proof_id != proof_id or row.state != "awaiting_user_approval":
            raise AppGovernanceConflict()
        if _expires(row.expires_at) <= SafeDateTime.now():
            raise AppGovernanceConflict()
        connection = _current_challenge_connection(db, row)
        _manage(db, actor, connection)
        _require_challenge_project(_actor(db, actor), row)
        credential = _current(db, AppConnectionCredential, row.proof_credential_id)
        if (credential is None or credential.connection_id != connection.id or credential.revoked_at
                or credential.identity_hash != row.connection_identity or _expires(credential.expires_at) <= SafeDateTime.now()):
            raise AppGovernanceDenied()
        _proof(_definition(db, connection), row.proof.get("external_identity"), row.proof.get("destination"), kind=row.kind)
        fingerprint_parts = ([row.app_key, row.proof["external_identity"]["issuer"], row.proof["destination"]["external_id"]]
                             if row.kind == "shared" else
                             [row.app_key, int(connection.id), row.proof["external_identity"], row.proof["destination"]["external_id"]])
        fingerprint = sha256(dumps(
            fingerprint_parts,
            sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest()
        destination = db.exec(SqlBuilder.select.table(AppNotificationDestination).where(
            AppNotificationDestination.target_fingerprint == fingerprint,
        ).with_for_update()).first()
        if destination is not None:
            # Reactivation is a new explicit approval by the same owner, never a
            # destination reassignment or silent subscription reactivation.
            if destination.user_id != row.user_id or destination.state != "revoked":
                raise AppGovernanceConflict()
            destination.state = "verified"
            destination.revision += 1
            destination.challenge_id = row.id
            destination.proof = row.proof
            destination.connection_id = row.connection_id
            destination.connection_identity = row.connection_identity
            db.update(destination)
        else:
            destination = AppNotificationDestination(
                user_id=row.user_id, connection_id=row.connection_id, app_key=row.app_key,
                target_fingerprint=fingerprint, connection_identity=row.connection_identity,
                challenge_id=row.id, proof=row.proof,
            )
            db.insert(destination)
        row.state = "verified"
        row.revision += 1
        row.destination_id = destination.id
        db.update(row)
        return _destination_data(destination)


def revoke_notification_destination(actor, destination_id, *, expected_revision):
    _positive(expected_revision)
    with DbSession.atomic() as db:
        lock_policy_mutation(db, exclusive=True)
        user = _actor(db, actor)
        row = _current(db, AppNotificationDestination, destination_id)
        if row is None or row.user_id != user.id:
            raise AppGovernanceDenied()
        if row.revision != expected_revision:
            raise AppGovernanceConflict()
        if row.state != "revoked":
            row.state = "revoked"
            row.revision += 1
            db.update(row)
        return _destination_data(row)


def cancel_destination_challenge(actor, challenge_id, *, expected_revision):
    _positive(expected_revision)
    with DbSession.atomic() as db:
        lock_policy_mutation(db, exclusive=True)
        row = _challenge(db, actor, challenge_id)
        if row.revision != expected_revision or row.state == "verified":
            raise AppGovernanceConflict()
        if row.state != "cancelled":
            row.state = "cancelled"
            row.revision += 1
            db.update(row)
        return _challenge_data(row)


def current_notification_destination(db, destination_id, *, expected_revision=None):
    """Revalidate a private personal target; never authorize user or board APIs."""
    from ..models import User
    row = _current(db, AppNotificationDestination, destination_id)
    if row is None or row.state != "verified":
        raise AppGovernanceDenied()
    if expected_revision is not None and row.revision != _positive(expected_revision):
        raise AppGovernanceConflict()
    connection = _connection(db, row.connection_id)
    if connection.app_key != row.app_key or _identity_hash(db, connection) != row.connection_identity:
        raise AppGovernanceConflict()
    user = _current(db, User, row.user_id)
    if user is None or user.deleted_at or not user.activated_at:
        raise AppGovernanceDenied()
    _manage(db, user, connection)
    definition = _definition(db, connection)
    challenge = _current(db, AppNotificationDestinationChallenge, row.challenge_id)
    if (challenge is None or challenge.state != "verified" or challenge.destination_id != row.id
            or challenge.user_id != row.user_id or challenge.connection_id != row.connection_id
            or challenge.app_key != row.app_key or challenge.connection_identity != row.connection_identity
            or challenge.proof != row.proof):
        raise AppGovernanceDenied()
    _proof(definition, row.proof.get("external_identity"), row.proof.get("destination"), kind=challenge.kind)
    return row


def read_notification_destination(actor, destination_id):
    with DbSession.atomic() as db:
        user = _actor(db, actor)
        row = _current(db, AppNotificationDestination, destination_id)
        if row is None or row.user_id != user.id:
            raise AppGovernanceDenied()
        # Revoked targets remain inspectable to their owner for reconciliation.
        # This status response contains no external routing ID or identity claim.
        return _destination_data(row)
