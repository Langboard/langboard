"""Retained app start attestation; never inferred from admission or ACK."""

from hmac import compare_digest
from ...core.db import DbSession, SqlBuilder
from ...core.types import SafeDateTime
from ..models import AppConnectionCredential, AppExecutionLease, AppExecutionStart
from .AppConnectionAuthentication import authenticate_connection_credential
from .AppExecutionLeases import _hash, _result, check_app_runtime
from .AppExecutionRequestRead import read_app_execution_request
from .AppGovernance import AppGovernanceConflict, AppGovernanceDenied, _current, lock_policy_mutation


def report_app_execution_start(token, project_id, card_id, request_id, lease_id, runtime_token, execution_reference):
    if not isinstance(execution_reference, str) or not execution_reference.strip() or len(execution_reference) > 200:
        raise ValueError("Execution reference must contain 1 to 200 characters")
    digest = _hash(runtime_token)
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        request = read_app_execution_request(token, project_id, card_id, request_id, for_update=True)
        principal = authenticate_connection_credential(token)
        _current(db, AppConnectionCredential, principal.credential_id)
        authenticate_connection_credential(token)
        lease = _current(db, AppExecutionLease, lease_id)
        if (
            lease is None
            or lease.request_id != request_id
            or lease.credential_id != principal.credential_id
            or not compare_digest(lease.runtime_token_hash, digest)
            or lease.state != "authorized"
            or lease.expires_at <= SafeDateTime.now()
        ):
            raise AppGovernanceDenied()
        identity = _result(db, lease)
        start = db.exec(
            SqlBuilder.select.table(AppExecutionStart).where(AppExecutionStart.lease_id == lease.id)
        ).first()
        changed = start is None
        if start is None:
            start = AppExecutionStart(
                lease_id=lease.id,
                request_id=request_id,
                acknowledgment_id=lease.acknowledgment_id,
                generation=request["generation"],
                runtime_reference=identity["runtime_reference"],
                execution_reference=execution_reference,
            )
            db.insert(start)
        elif start.execution_reference != execution_reference:
            raise AppGovernanceConflict()
        return {
            **{
                key: identity[key]
                for key in (
                    "schema_version",
                    "request_uid",
                    "acknowledgment_uid",
                    "runtime_reference",
                    "generation",
                    "lease_uid",
                )
            },
            "start_uid": start.get_uid(),
            "state": "start_reported",
            "evidence_kind": "app_attestation",
            "execution_reference": start.execution_reference,
            "reported_at": start.created_at.isoformat(),
            "changed": changed,
        }


def read_app_runtime_report(lease_id, runtime_token):
    with DbSession.atomic() as db:
        permit = check_app_runtime(lease_id, runtime_token, renew=False)
        start = db.exec(
            SqlBuilder.select.table(AppExecutionStart).where(AppExecutionStart.lease_id == lease_id)
        ).first()
        return {
            **permit,
            "evidence_kind": "app_attestation" if start else "none",
            "start_report": {
                "start_uid": start.get_uid(),
                "execution_reference": start.execution_reference,
                "reported_at": start.created_at.isoformat(),
            }
            if start
            else None,
            "active_report": bool(start and permit["permit_execution"]),
        }
