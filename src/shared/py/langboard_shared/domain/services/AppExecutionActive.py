"""Optional native Active handoff after a current lease's start attestation."""

from hmac import compare_digest
from ...core.db import DbSession, SqlBuilder
from ...core.types import SafeDateTime, SnowflakeID
from ..models import AppExecutionActive, AppExecutionLease, AppExecutionStart, Project, ProjectColumn, User, WorkflowStageDefinition
from .AppEventDelivery import _expired
from .AppExecutionGrant import _evaluate_principal
from .AppExecutionLeases import _hash, _check_app_runtime
from .AppExecutionRequests import current_execution_request_scope
from .AppGovernance import AppGovernanceConflict, AppGovernanceDenied, _current, lock_policy_mutation
from .DomainService import DomainService


def _receipt(row, *, changed=False):
    return {"schema_version": 1, "handoff_uid": row.get_uid(), "state": "active_recorded",
            "changed": changed, "request_uid": SnowflakeID(row.request_id).to_short_code(),
            "card_uid": SnowflakeID(row.card_id).to_short_code(), "generation": row.generation,
            "lease_uid": SnowflakeID(row.lease_id).to_short_code(),
            "column_uid": SnowflakeID(row.column_id).to_short_code(), "inputs": row.inputs,
            "execution_resumed": False}


def _scope(db, token, project_id, card_id, request_id, lease_id, runtime_token):
    principal, card, binding, request, definition = current_execution_request_scope(
        db, token, project_id, card_id, request_id, capability="execution.run", role_action="card_update"
    )
    lease = _current(db, AppExecutionLease, lease_id)
    if (lease is None or lease.request_id != request.id or lease.credential_id != principal.credential_id
        or not compare_digest(lease.runtime_token_hash, _hash(runtime_token))
        or lease.state != "authorized" or _expired(lease.expires_at, SafeDateTime.now())):
        raise AppGovernanceDenied()
    if not _check_app_runtime(lease.id, runtime_token, renew=False)["permit_execution"]:
        raise AppGovernanceDenied()
    return principal, card, binding, request, definition, lease


def activate_app_runtime(token, project_id, card_id, request_id, lease_id, runtime_token,
                         expected_card_revision, expected_binding_revision):
    if (type(expected_card_revision) is not int or expected_card_revision < 0
        or not isinstance(expected_binding_revision, str) or not expected_binding_revision):
        raise ValueError("Observed card and binding revisions required")
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        principal, card, binding, request, definition, lease = _scope(
            db, token, project_id, card_id, request_id, lease_id, runtime_token
        )
        inputs = {"expected_card_revision": expected_card_revision,
                  "expected_binding_revision": expected_binding_revision}
        previous = db.exec(SqlBuilder.select.table(AppExecutionActive).where(AppExecutionActive.lease_id == lease.id)).first()
        if previous:
            if previous.inputs != inputs:
                raise AppGovernanceConflict()
            return _receipt(previous)
        if card.last_change_seq != expected_card_revision or binding.edit_revision() != expected_binding_revision:
            raise AppGovernanceConflict()
        requirements = definition.declaration.get("workflow_requirements") or {}
        if (not binding.stage_transitions_enabled
            or "active" not in requirements.get("required", []) + requirements.get("optional", [])):
            raise AppGovernanceDenied()
        start = db.exec(SqlBuilder.select.table(AppExecutionStart).where(AppExecutionStart.lease_id == lease.id)).first()
        if start is None or start.request_id != request.id or start.generation != request.generation:
            raise AppGovernanceDenied()
        column_uid = binding.workflow_mapping.get("active")
        column = _current(db, ProjectColumn, SnowflakeID.from_short_code(column_uid)) if column_uid else None
        stage = db.exec(SqlBuilder.select.table(WorkflowStageDefinition).where(WorkflowStageDefinition.key == "active")).first()
        if (column is None or column.project_id != project_id or column.deleted_at or column.is_archive
            or column.workflow_stage != "active" or stage is None or not stage.is_builtin
            or not stage.is_active or stage.counts_as_completed):
            raise AppGovernanceDenied()
        project = _current(db, Project, project_id)
        actor = _current(db, User, principal.owner_id)
        service = DomainService().card
        order = service.repo.card.get_next_order(column, {"project_id": project.id})
        if service.change_order(actor, project, card, order, column) is not True:
            raise AppGovernanceConflict()
        snapshot = _evaluate_principal(db, principal, project_id, card_id, request.generation, workflow_stage="active")
        row = AppExecutionActive(lease_id=lease.id, start_id=start.id, request_id=request.id,
            project_id=project_id, card_id=card_id, column_id=column.id, generation=request.generation,
            snapshot=snapshot, inputs=inputs)
        db.insert(row)
        return _receipt(row, changed=True)


def read_app_active_handoff(token, project_id, card_id, request_id, lease_id, runtime_token):
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        _, _, _, request, _, lease = _scope(db, token, project_id, card_id, request_id, lease_id, runtime_token)
        row = db.exec(SqlBuilder.select.table(AppExecutionActive).where(AppExecutionActive.lease_id == lease.id)).first()
        return _receipt(row) if row else {"schema_version": 1, "request_uid": request.get_uid(),
            "card_uid": SnowflakeID(card_id).to_short_code(), "lease_uid": lease.get_uid(),
            "generation": request.generation, "state": "not_recorded", "changed": False,
            "execution_resumed": False}
