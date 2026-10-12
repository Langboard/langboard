"""Bounded current-authority runtime permits; acquisition is not start evidence."""

import re
from datetime import timedelta
from hashlib import sha256
from hmac import compare_digest
from ...core.db import DbSession, SqlBuilder
from ...core.types import SafeDateTime
from ..models import (
    AppConnection,
    AppConnectionCredential,
    AppExecutionAcknowledgment,
    AppExecutionActive,
    AppExecutionLease,
    CardExecutionIntent,
    User,
)
from .AppConnectionAuthentication import _identity_hash, authenticate_connection_credential
from .AppExecutionRequestRead import read_app_execution_request
from .AppGovernance import AppGovernanceConflict, AppGovernanceDenied, _actor, _current, lock_policy_mutation
from .CardExecutionIntent import _configuration, _generation


LEASE_SECONDS = 120


def _hash(token):
    if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{64}", token):
        raise ValueError("Runtime token must be 32 random bytes encoded as lowercase hex")
    return sha256(token.encode()).hexdigest()


def _result(db, row):
    intent = _current(db, CardExecutionIntent, row.request_id, for_update=False)
    ack = _current(db, AppExecutionAcknowledgment, row.acknowledgment_id, for_update=False)
    if intent is None or ack is None or ack.request_id != intent.id:
        raise AppGovernanceDenied()
    return {
        "schema_version": 1,
        "request_uid": intent.get_uid(),
        "acknowledgment_uid": ack.get_uid(),
        "runtime_reference": ack.runtime_reference,
        "generation": intent.execution_generation,
        "lease_uid": row.get_uid(),
        "state": row.state,
        "permit_execution": row.state == "authorized",
        "started": False,
        "expires_at": row.expires_at.isoformat(),
    }


def authorize_app_runtime(token, project_id, card_id, request_id, acknowledgment_id, runtime_token):
    digest = _hash(runtime_token)
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        read_app_execution_request(token, project_id, card_id, request_id, for_update=True)
        principal = authenticate_connection_credential(token)
        _current(db, AppConnectionCredential, principal.credential_id)
        authenticate_connection_credential(token)
        ack = _current(db, AppExecutionAcknowledgment, acknowledgment_id)
        if (
            ack is None
            or ack.request_id != request_id
            or ack.connection_id != principal.connection_id
            or ack.app_key != principal.app_key
        ):
            raise AppGovernanceDenied()
        row = db.exec(
            SqlBuilder.select.table(AppExecutionLease)
            .where(AppExecutionLease.request_id == request_id)
            .with_for_update()
        ).first()
        now = SafeDateTime.now()
        if row:
            if (
                not compare_digest(row.runtime_token_hash, digest)
                or row.credential_id != principal.credential_id
                or row.acknowledgment_id != ack.id
                or row.state != "authorized"
                or row.expires_at <= now
            ):
                raise AppGovernanceConflict()
            return {**_result(db, row), "changed": False}
        row = AppExecutionLease(
            request_id=request_id,
            acknowledgment_id=ack.id,
            credential_id=principal.credential_id,
            runtime_token_hash=digest,
            expires_at=now + timedelta(seconds=LEASE_SECONDS),
            history=[{"state": "authorized", "at": now.isoformat()}],
        )
        db.insert(row)
        return {**_result(db, row), "changed": True}


def _allowed(db, row, intent, project_id):
    try:
        actor = _current(db, User, intent.actor_id)
        if actor is None:
            return False
        actor = _actor(db, actor)
        active = db.exec(
            SqlBuilder.select.table(AppExecutionActive).where(AppExecutionActive.lease_id == row.id)
        ).first()
        card, connection, snapshot = _configuration(
            db,
            actor,
            project_id,
            intent.card_id,
            intent.app_key,
            intent.connection_id,
            workflow_stage="active" if active else "ready",
        )
        generation = _generation(db, card.id)
        credential = _current(db, AppConnectionCredential, row.credential_id)
        now = SafeDateTime.now()
        return bool(
            generation
            and generation.execution_generation == intent.execution_generation
            and snapshot == (active.snapshot if active else intent.snapshot)
            and credential
            and credential.connection_id == connection.id
            and credential.revoked_at is None
            and credential.expires_at > now
            and credential.identity_hash == _identity_hash(db, connection)
        )
    except (AppGovernanceDenied, AppGovernanceConflict):
        return False


def check_app_runtime(lease_id, runtime_token, *, stopped=False, renew=True):
    if type(stopped) is not bool:
        raise ValueError("Stopped must be boolean")
    digest = _hash(runtime_token)
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        initial = _current(db, AppExecutionLease, lease_id, for_update=False)
        if initial is None or not compare_digest(initial.runtime_token_hash, digest):
            raise AppGovernanceDenied()
        intent = _current(db, CardExecutionIntent, initial.request_id, for_update=False)
        connection = _current(db, AppConnection, intent.connection_id, for_update=False) if intent else None
        # Resolve board from native ownership, never from caller-supplied authority.
        from ..models import Card

        card = _current(db, Card, intent.card_id, for_update=False) if intent else None
        allowed = bool(
            not stopped
            and initial.state == "authorized"
            and intent
            and connection
            and card
            and _allowed(db, initial, intent, card.project_id)
        )
        row = _current(db, AppExecutionLease, lease_id)
        now = SafeDateTime.now()
        state = (
            "stopped"
            if stopped
            else "stop_requested"
            if row.state == "authorized" and (not allowed or row.expires_at <= now)
            else row.state
        )
        if state != row.state:
            row.state = state
            row.history = [*row.history, {"state": state, "at": now.isoformat()}]
            db.update(row)
        elif renew and state == "authorized" and allowed:
            row.expires_at = now + timedelta(seconds=LEASE_SECONDS)
            db.update(row)
        return _result(db, row)


def recover_app_runtime(request_id, runtime_token):
    digest = _hash(runtime_token)
    with DbSession.atomic() as db:
        row = db.exec(
            SqlBuilder.select.table(AppExecutionLease).where(AppExecutionLease.request_id == request_id)
        ).first()
        if row is None or not compare_digest(row.runtime_token_hash, digest):
            raise AppGovernanceDenied()
        lease_id = row.id
    return check_app_runtime(lease_id, runtime_token, renew=False)
