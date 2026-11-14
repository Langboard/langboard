"""Sanitized current app-attested identity for an already readable native card."""

from datetime import datetime
from sqlalchemy import select, text
from ...core.db import DbSession
from ...core.types import SafeDateTime
from ..models import AppConnectionCredential, AppExecutionLease, AppExecutionRequest, AppExecutionStart
from .AppExecutionGrant import evaluate_current_lease_grant
from .AppExecutionLeases import runtime_timestamp
from .AppGovernance import AppGovernanceDenied


def card_agent_execution(project_id, card_id):
    """Call only after resolving the current readable card; no lifecycle inference.

    Request(card, generation), lease(request), and start(lease) are unique native
    associations. The bounded ambiguity guard also fails closed for damaged data.
    Retained evidence with withdrawn authority is never presented as current.
    """
    with DbSession.use(readonly=False) as db:
        rows = db.exec(
            select(AppExecutionStart, AppExecutionRequest, AppExecutionLease, AppConnectionCredential)
            .join(AppExecutionLease, AppExecutionLease.id == AppExecutionStart.lease_id)
            .join(AppExecutionRequest, AppExecutionRequest.id == AppExecutionLease.request_id)
            .join(AppConnectionCredential, AppConnectionCredential.id == AppExecutionLease.credential_id)
            .where(
                AppExecutionRequest.project_id == project_id,
                AppExecutionRequest.card_id == card_id,
                AppExecutionRequest.generation
                == text("(SELECT execution_generation FROM card_execution_generation WHERE card_id=:card_id)"),
                AppExecutionStart.request_id == AppExecutionRequest.id,
                AppExecutionStart.generation == AppExecutionRequest.generation,
                AppExecutionStart.acknowledgment_id == AppExecutionLease.acknowledgment_id,
            )
            .limit(2),
            params={"card_id": int(card_id)},
        ).all()
        if len(rows) != 1:
            return None
        start, request, lease, credential = rows[0]
        try:
            current = evaluate_current_lease_grant(db, request, lease, for_update=False)
        except AppGovernanceDenied:
            return None
        if current != request.authority:
            return None
        return {
            "schema_version": 1,
            "start_uid": start.get_uid(),
            "request_uid": request.get_uid(),
            "app_key": request.app_key,
            "generation": request.generation,
            "reported_at": runtime_timestamp(start.created_at),
            "evaluated_at": runtime_timestamp(SafeDateTime.now()),
            "authorization_expires_at": min(
                (runtime_timestamp(value) for value in (lease.expires_at, credential.expires_at)),
                key=datetime.fromisoformat,
            ),
            "evidence_kind": "app_attestation",
            "current_authorization": "authorized",
            "process_liveness": "unknown",
        }
