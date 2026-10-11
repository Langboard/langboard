"""Current owning app requests Review through existing native card movement."""
from hashlib import sha256
from json import dumps
from ...core.db import DbSession, SqlBuilder
from ...core.types import SnowflakeID
from ..models import (
    AppDefinition,
    AppExecutionLease,
    AppExecutionRequest,
    AppExecutionReview,
    AppExecutionStart,
    Project,
    ProjectColumn,
    User,
    WorkflowStageDefinition,
)
from .AppCardScope import require_app_card_scope
from .AppConnectionAuthentication import authenticate_connection_credential
from .AppExecutionGrant import AppExecutionCredentialDenied
from .AppExecutionRequests import AppExecutionRequestConflict
from .AppGovernance import AppGovernanceDenied, _current
from .DomainService import DomainService
from .ExecutionGeneration import execution_generations


def request_execution_review(token, project_id, card_id, request_id, generation, expected_card_revision,
                             expected_binding_revision, evidence_reference):
    if type(generation) is not int or generation < 1 or type(expected_card_revision) is not int or expected_card_revision < 0:
        raise ValueError("Positive generation and nonnegative card revision are required")
    if not isinstance(expected_binding_revision, str) or not expected_binding_revision:
        raise ValueError("A current binding revision is required")
    if not isinstance(evidence_reference, str) or not evidence_reference.strip() or len(evidence_reference) > 200:
        raise ValueError("A result reference of 1 to 200 characters is required")
    fingerprint = sha256(dumps([generation, expected_card_revision, expected_binding_revision, evidence_reference]).encode()).hexdigest()
    with DbSession.atomic() as db:
        try:
            principal = authenticate_connection_credential(token)
        except AppGovernanceDenied as exc:
            raise AppExecutionCredentialDenied() from exc
        card, owner, selection, binding, resources = require_app_card_scope(
            db, principal, project_id, card_id, capability="execution.run", role_action="card_update"
        )
        request = _current(db, AppExecutionRequest, request_id)
        if (request is None or request.card_id != card_id or request.project_id != project_id
            or request.connection_id != principal.connection_id or request.app_key != principal.app_key
            or request.generation != generation or execution_generations([card_id]).get(int(card_id)) != generation
            or set(request.authority.get("resource_uids", [])) != set(selection.resource_uids)
            or request.authority.get("ownership_revision") != owner.revision
            or request.authority.get("selection_revision") != selection.revision
            or request.authority.get("resource_revisions") != {row.get_uid(): row.access_revision for row in resources}):
            raise AppGovernanceDenied()
        if not binding.stage_transitions_enabled or binding.edit_revision() != expected_binding_revision:
            raise AppExecutionRequestConflict()
        definition = db.exec(SqlBuilder.select.table(AppDefinition).where(AppDefinition.key == principal.app_key)).first()
        if ("resources.read" not in definition.declaration.get("capabilities", [])
            or "resources.read" not in binding.granted_capabilities):
            raise AppGovernanceDenied()
        requirements = definition.declaration.get("workflow_requirements") or {}
        if "review" not in requirements.get("required", []) + requirements.get("optional", []):
            raise AppGovernanceDenied()
        stage = db.exec(SqlBuilder.select.table(WorkflowStageDefinition).where(WorkflowStageDefinition.key == "review").with_for_update()).first()
        target_uid = binding.workflow_mapping.get("review")
        target = _current(db, ProjectColumn, SnowflakeID.from_short_code(target_uid)) if target_uid else None
        if (stage is None or not stage.is_builtin or not stage.is_active or stage.counts_as_completed
            or target is None or target.project_id != project_id or target.deleted_at or target.is_archive
            or target.workflow_stage != "review"):
            raise AppExecutionRequestConflict()
        lease = db.exec(SqlBuilder.select.table(AppExecutionLease).where(AppExecutionLease.request_id == request_id).with_for_update()).first()
        start = db.exec(SqlBuilder.select.table(AppExecutionStart).where(AppExecutionStart.request_id == request_id)).first()
        # A stopped permit is evidence of termination only. Current app/card scope
        # above supplies transition authority independently of that permit.
        if lease is None or lease.state != "stopped" or start is None or start.lease_id != lease.id:
            raise AppGovernanceDenied()
        retained = db.exec(SqlBuilder.select.table(AppExecutionReview).where(AppExecutionReview.request_id == request_id)).first()
        if retained:
            if (retained.fingerprint != fingerprint or retained.receipt["to_column_uid"] != target_uid
                or retained.receipt["column_revision"] != target.updated_at.isoformat()
                or retained.receipt["stage_revision"] != stage.updated_at.isoformat()):
                raise AppExecutionRequestConflict()
            return {**retained.receipt, "changed": False}
        if card.last_change_seq != expected_card_revision:
            raise AppExecutionRequestConflict()
        source_id = card.project_column_id
        moved = source_id != target.id
        service = DomainService()
        actor = _current(db, User, principal.owner_id)
        project = _current(db, Project, project_id)
        if moved and service.card.change_order(actor, project, card, 0, target) is not True:
            raise AppExecutionRequestConflict()
        current = _current(db, type(card), card_id)
        if current.project_column_id != target.id:
            raise AppExecutionRequestConflict()
        receipt = {"schema_version": 1, "request_uid": request.get_uid(), "card_uid": card.get_uid(),
                   "generation": generation, "workflow_stage": "review", "from_column_uid": SnowflakeID(source_id).to_short_code(),
                   "to_column_uid": target_uid, "binding_revision": expected_binding_revision,
                   "column_revision": target.updated_at.isoformat(), "stage_revision": stage.updated_at.isoformat(),
                   "card_revision": current.last_change_seq, "evidence_reference": evidence_reference,
                   "evidence_kind": "app_attestation", "state": "moved" if moved else "already_at_target",
                   "workflow_transitioned": moved, "started": False, "execution_resumed": False}
        db.insert(AppExecutionReview(request_id=request_id, fingerprint=fingerprint, receipt=receipt))
        return {**receipt, "changed": True}
