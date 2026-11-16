"""Explicit custody consent; deliver a short-lived credential only to its approved service.

The callback body exists in memory for one bounded HTTPS call. The durable
receipt stores identity and outcome only. Unknown callback outcomes revoke the
credential and cannot trigger automatic replay or another issuance.
"""

from asyncio import CancelledError
from json import dumps
from hashlib import sha256
from ...core.db import DbSession, SqlBuilder
from ...core.types import SafeDateTime, SnowflakeID
from ..models import AppConnectionCredential, User
from ..models.AppConnectionCredentialCustody import AppConnectionCredentialCustody, AppConnectionCredentialHandoff
from .AppConnectionAuthentication import _connection, _identity_hash, _manage, issue_connection_credential
from .AppEventDestination import current_destination
from .AppGovernance import AppGovernanceConflict, AppGovernanceDenied, _actor, _current, lock_policy_mutation
from .AppNotificationDestinations import _positive, _request


HANDOFF_EVENT = "io.langboard.app.connection.credential.v1"


class CredentialHandoffUnknown(RuntimeError):
    """The callback outcome is unknown and its credential was revoked."""


def _current_custody(db, identifier, expected_revision):
    row = _current(db, AppConnectionCredentialCustody, identifier)
    if row is None or row.state != "enabled":
        raise AppGovernanceDenied()
    if row.revision != _positive(expected_revision):
        raise AppGovernanceConflict()
    actor = _current(db, User, row.approved_by)
    if actor is None:
        raise AppGovernanceDenied()
    actor = _actor(db, actor)
    connection = _connection(db, row.connection_id)
    _manage(db, actor, connection)
    destination, setting = current_destination(db, connection, HANDOFF_EVENT)
    if (_identity_hash(db, connection) != row.identity_hash or destination.id != row.destination_id
            or destination.revision != row.destination_revision):
        raise AppGovernanceConflict()
    return row, actor, connection, setting


def _status(row):
    return {"custody_uid": row.get_uid(), "connection_uid": SnowflakeID(row.connection_id).to_short_code(),
            "revision": row.revision, "state": row.state,
            "destination_uid": SnowflakeID(row.destination_id).to_short_code(),
            "destination_revision": row.destination_revision}


def approve_credential_custody(actor, connection_id, *, expected_connection_revision, expected_destination_revision,
                               expected_revision=None):
    """Explicit host-user action; never modify the administrator's webhook binding."""
    _positive(expected_destination_revision)
    if expected_revision is not None:
        _positive(expected_revision)
    with DbSession.atomic() as db:
        lock_policy_mutation(db, exclusive=True)
        user = _actor(db, actor)
        connection = _connection(db, connection_id)
        _manage(db, user, connection)
        if connection.edit_revision() != expected_connection_revision:
            raise AppGovernanceConflict()
        destination, _ = current_destination(db, connection, HANDOFF_EVENT)
        if destination.revision != expected_destination_revision:
            raise AppGovernanceConflict()
        row = db.exec(SqlBuilder.select.table(AppConnectionCredentialCustody).where(
            AppConnectionCredentialCustody.connection_id == connection.id).with_for_update()).first()
        if row is None:
            if expected_revision is not None:
                raise AppGovernanceConflict()
            row = AppConnectionCredentialCustody(connection_id=connection.id, approved_by=user.id,
                identity_hash=_identity_hash(db, connection), destination_id=destination.id,
                destination_revision=destination.revision)
            db.insert(row)
        else:
            if row.revision != expected_revision:
                raise AppGovernanceConflict()
            row.approved_by = user.id
            row.identity_hash = _identity_hash(db, connection)
            row.destination_id = destination.id
            row.destination_revision = destination.revision
            row.state = "enabled"
            row.revision += 1
            db.update(row)
        return _status(row)


def revoke_credential_custody(actor, custody_id, *, expected_revision):
    with DbSession.atomic() as db:
        lock_policy_mutation(db, exclusive=True)
        user = _actor(db, actor)
        row = _current(db, AppConnectionCredentialCustody, custody_id)
        if row is None or row.approved_by != user.id:
            raise AppGovernanceDenied()
        if row.revision != _positive(expected_revision):
            raise AppGovernanceConflict()
        if row.state != "revoked":
            row.state = "revoked"
            row.revision += 1
            db.update(row)
            handoffs = db.exec(SqlBuilder.select.table(AppConnectionCredentialHandoff).where(
                AppConnectionCredentialHandoff.custody_id == row.id)).all()
            for handoff in handoffs:
                credential = _current(db, AppConnectionCredential, handoff.credential_id)
                if credential is not None and credential.revoked_at is None:
                    credential.revoked_at = SafeDateTime.now()
                    db.update(credential)
        return _status(row)


def _receipt(row):
    return {"handoff_uid": row.get_uid(), "state": row.state,
            "credential_uid": SnowflakeID(row.credential_id).to_short_code(),
            "custody_revision": row.custody_revision}


async def handoff_connection_credential(actor, custody_id, *, expected_revision, request_id):
    from ...core.security import KeyVault
    from ...tasks.webhooks.utils import ensure_public_webhook_url
    from ...tasks.webhooks.WebhookTask import sign_webhook_bytes, post_resolved_webhook_bytes, EXECUTION_WEBHOOK_TIMEOUT

    _request(request_id)
    # Resolve an approved target before minting any credential. No database
    # transaction spans DNS, vault lookup or external network I/O.
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        custody, user, _, setting = _current_custody(db, custody_id, expected_revision)
        if user.id != actor.id:
            raise AppGovernanceDenied()
        previous = db.exec(SqlBuilder.select.table(AppConnectionCredentialHandoff).where(
            AppConnectionCredentialHandoff.custody_id == custody.id,
            AppConnectionCredentialHandoff.request_id == request_id)).first()
        if previous is not None:
            return _receipt(previous)
        target_url, secret_id = setting.url, setting.secret_id
    target = await ensure_public_webhook_url(target_url)
    secret = KeyVault.get_key(secret_id)
    if not secret:
        raise AppGovernanceDenied()
    with DbSession.atomic() as db:
        lock_policy_mutation(db, exclusive=True)
        custody, user, connection, setting = _current_custody(db, custody_id, expected_revision)
        if user.id != actor.id or setting.url != target_url or setting.secret_id != secret_id:
            raise AppGovernanceConflict()
        previous = db.exec(SqlBuilder.select.table(AppConnectionCredentialHandoff).where(
            AppConnectionCredentialHandoff.custody_id == custody.id,
            AppConnectionCredentialHandoff.request_id == request_id)).first()
        if previous is not None:
            return _receipt(previous)
        issued = issue_connection_credential(user, connection.id, expires_in_seconds=3600)
        handoff = AppConnectionCredentialHandoff(custody_id=custody.id, request_id=request_id,
            custody_revision=custody.revision, credential_id=SnowflakeID.from_short_code(issued["credential_uid"]))
        db.insert(handoff)
        credential = _current(db, AppConnectionCredential, handoff.credential_id)
        credential.custody_id = custody.id
        db.update(credential)
        body = dumps({"schema_version": 1, "event_id": handoff.get_uid(), "event": HANDOFF_EVENT,
            "app_key": connection.app_key, "connection_uid": connection.get_uid(),
            "custody_uid": custody.get_uid(), "custody_revision": custody.revision,
            "credential": issued}, sort_keys=True, separators=(",", ":")).encode()
        body, headers = sign_webhook_bytes(body, handoff.get_uid(), secret)
    try:
        await post_resolved_webhook_bytes(target, body, headers, timeout=EXECUTION_WEBHOOK_TIMEOUT)
    except BaseException as error:
        # Includes cancellation. An ambiguous external response never leaves an
        # untracked usable bearer or retries the secret-bearing callback.
        with DbSession.atomic() as db:
            lock_policy_mutation(db, exclusive=True)
            credential = _current(db, AppConnectionCredential, handoff.credential_id)
            credential.revoked_at = SafeDateTime.now()
            db.update(credential)
            saved = _current(db, AppConnectionCredentialHandoff, handoff.id)
            saved.state = "unknown_revoked"
            db.update(saved)
        if isinstance(error, (CancelledError, KeyboardInterrupt, SystemExit)):
            raise
        raise CredentialHandoffUnknown("Credential callback outcome unknown; issued credential revoked") from None
    with DbSession.atomic() as db:
        lock_policy_mutation(db, exclusive=True)
        saved = _current(db, AppConnectionCredentialHandoff, handoff.id)
        try:
            _current_custody(db, custody_id, expected_revision)
        except (AppGovernanceDenied, AppGovernanceConflict):
            credential = _current(db, AppConnectionCredential, handoff.credential_id)
            credential.revoked_at = SafeDateTime.now()
            db.update(credential)
            saved.state = "revoked"
        else:
            saved.state = "delivered"
        db.update(saved)
        return _receipt(saved)


def validate_custodied_credential(db, credential):
    """Prepared, crashed or revoked callback credentials cannot authenticate."""
    handoff = db.exec(SqlBuilder.select.table(AppConnectionCredentialHandoff).where(
        AppConnectionCredentialHandoff.credential_id == credential.id)).first()
    if handoff is None:
        raise AppGovernanceDenied()
    if handoff.custody_id != credential.custody_id:
        raise AppGovernanceDenied()
    if handoff.state != "delivered":
        raise AppGovernanceDenied()
    _current_custody(db, handoff.custody_id, handoff.custody_revision)


def read_custodied_credential_status(token):
    """Reconcile an exact staged bearer without granting it API authority.

    This dedicated route accepts possession of the token solely to inspect its
    own metadata, including a revoked outcome. It never authenticates a user or
    app principal, returns another credential, or makes prepared tokens usable.
    """
    from .AppNotificationDestinations import _expires
    if not isinstance(token, str) or len(token) != 48 or not token.startswith("lbac_"):
        raise AppGovernanceDenied()
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        credential = db.exec(SqlBuilder.select.table(AppConnectionCredential).where(
            AppConnectionCredential.token_hash == sha256(token.encode()).hexdigest())).first()
        if credential is None or credential.custody_id is None:
            raise AppGovernanceDenied()
        handoff = db.exec(SqlBuilder.select.table(AppConnectionCredentialHandoff).where(
            AppConnectionCredentialHandoff.credential_id == credential.id)).first()
        if handoff is None or handoff.custody_id != credential.custody_id:
            raise AppGovernanceDenied()
        usable = False
        if (handoff.state == "delivered" and credential.revoked_at is None
                and _expires(credential.expires_at) > SafeDateTime.now()):
            try:
                _current_custody(db, handoff.custody_id, handoff.custody_revision)
            except (AppGovernanceDenied, AppGovernanceConflict):
                pass
            else:
                usable = True
        return {**_receipt(handoff), "usable": usable, "expires_at": _expires(credential.expires_at).isoformat()}


async def renew_custodied_credential(token, *, request_id):
    """Renew through the same approved server callback, never a user route.

    Refresh is allowed only in the last 15 minutes of the existing one-hour
    credential. A successful callback revokes the old credential. Failed or
    unknown handoffs leave the old bearer at its original, finite expiry.
    """
    from .AppConnectionAuthentication import authenticate_connection_credential
    from .AppNotificationDestinations import _expires
    _request(request_id)
    with DbSession.atomic() as db:
        lock_policy_mutation(db, exclusive=True)
        principal = authenticate_connection_credential(token)
        credential = _current(db, AppConnectionCredential, principal.credential_id)
        handoff = db.exec(SqlBuilder.select.table(AppConnectionCredentialHandoff).where(
            AppConnectionCredentialHandoff.credential_id == credential.id)).first()
        if handoff is None or handoff.state != "delivered":
            raise AppGovernanceDenied()
        if request_id == handoff.request_id:
            raise AppGovernanceConflict()
        if (_expires(credential.expires_at) - SafeDateTime.now()).total_seconds() > 900:
            raise AppGovernanceConflict()
        custody, actor, _, _ = _current_custody(db, handoff.custody_id, handoff.custody_revision)
        if handoff.renewal_request_id is not None and handoff.renewal_request_id != request_id:
            raise AppGovernanceConflict()
        handoff.renewal_request_id = request_id
        db.update(handoff)
        identifier, revision = custody.id, custody.revision
    result = await handoff_connection_credential(actor, identifier, expected_revision=revision, request_id=request_id)
    if result["state"] == "delivered":
        with DbSession.atomic() as db:
            lock_policy_mutation(db, exclusive=True)
            current = _current(db, AppConnectionCredential, principal.credential_id)
            if current.revoked_at is None:
                current.revoked_at = SafeDateTime.now()
                db.update(current)
    return result
