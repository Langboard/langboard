"""Explicit Review handoff after retained start, Active and stopped receipts."""

from ...core.db import DbSession, SqlBuilder
from ...core.types import SafeDateTime, SnowflakeID
from ..models import (
    AppExecutionActive,
    AppExecutionLease,
    AppExecutionReview,
    AppExecutionStart,
    BoardAppBinding,
    CardExecutionIntent,
    Project,
    ProjectColumn,
    User,
    WorkflowStageDefinition,
)
from .AppConnectionAuthentication import authenticate_connection_credential
from .AppGovernance import AppGovernanceConflict, AppGovernanceDenied, _actor, _current, lock_policy_mutation
from .CardExecutionIntent import _configuration, _generation
from .DomainService import DomainService


def _scope(db, token, project_id, card_id, request_id):
    principal = authenticate_connection_credential(token)
    intent = _current(db, CardExecutionIntent, request_id, for_update=False)
    if (
        intent is None
        or intent.card_id != card_id
        or intent.app_key != principal.app_key
        or intent.connection_id != principal.connection_id
    ):
        raise AppGovernanceDenied()
    actor = _current(db, User, intent.actor_id)
    if actor is None:
        raise AppGovernanceDenied()
    actor = _actor(db, actor)
    active = db.exec(
        SqlBuilder.select.table(AppExecutionActive).where(AppExecutionActive.request_id == request_id)
    ).first()
    retained = db.exec(
        SqlBuilder.select.table(AppExecutionReview).where(AppExecutionReview.request_id == request_id)
    ).first()
    if active is None or active.project_id != project_id:
        raise AppGovernanceDenied()
    card, _, snapshot = _configuration(
        db,
        actor,
        project_id,
        card_id,
        intent.app_key,
        intent.connection_id,
        workflow_stage="review" if retained else "active",
    )
    generation = _generation(db, card_id)
    if (
        generation is None
        or generation.execution_generation != intent.execution_generation
        or snapshot != (retained.snapshot if retained else active.snapshot)
    ):
        raise AppGovernanceDenied()
    # Stopped execution is evidence, never permission to resume. Current app,
    # user, consent and resource authority above supplies transition authority.
    lease = _current(db, AppExecutionLease, active.lease_id)
    start = _current(db, AppExecutionStart, active.start_id)
    if (
        lease is None
        or lease.request_id != request_id
        or lease.state != "stopped"
        or start is None
        or start.lease_id != lease.id
        or start.request_id != request_id
    ):
        raise AppGovernanceDenied()
    return actor, intent, card, snapshot, retained


def read_execution_review(token, project_id, card_id, request_id):
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        _, intent, card, snapshot, retained = _scope(db, token, project_id, card_id, request_id)
        return {
            "schema_version": 1,
            "request_uid": intent.get_uid(),
            "card_uid": card.get_uid(),
            "state": "recorded" if retained else "not_recorded",
            "receipt": retained.receipt if retained else None,
            "current": {
                "card_revision": int(SafeDateTime.fromisoformat(snapshot["card_revision"]).timestamp() * 1000000),
                "binding_revision": snapshot["binding_revision"],
                "generation": intent.execution_generation,
            },
            "changed": False,
            "started": False,
            "execution_resumed": False,
        }


def request_execution_review(
    token,
    project_id,
    card_id,
    request_id,
    generation,
    expected_card_revision,
    expected_binding_revision,
    evidence_reference,
):
    if (
        type(generation) is not int
        or generation < 1
        or type(expected_card_revision) is not int
        or expected_card_revision < 0
        or not isinstance(expected_binding_revision, str)
        or not 1 <= len(expected_binding_revision) <= 100
        or not isinstance(evidence_reference, str)
        or not evidence_reference.strip()
        or len(evidence_reference) > 200
    ):
        raise ValueError("Observed generation, card/binding revisions and result reference required")
    inputs = {
        "generation": generation,
        "expected_card_revision": expected_card_revision,
        "expected_binding_revision": expected_binding_revision,
        "evidence_reference": evidence_reference,
    }
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        actor, intent, card, snapshot, retained = _scope(db, token, project_id, card_id, request_id)
        if intent.execution_generation != generation:
            raise AppGovernanceDenied()
        if retained:
            if retained.receipt["inputs"] != inputs:
                raise AppGovernanceConflict()
            return {**retained.receipt, "changed": False}
        if (
            int(SafeDateTime.fromisoformat(snapshot["card_revision"]).timestamp() * 1000000) != expected_card_revision
            or snapshot["binding_revision"] != expected_binding_revision
        ):
            raise AppGovernanceConflict()
        binding = db.exec(
            SqlBuilder.select.table(BoardAppBinding).where(
                BoardAppBinding.project_id == project_id, BoardAppBinding.app_key == intent.app_key
            )
        ).first()
        target_uid = binding.workflow_mapping.get("review")
        target = _current(db, ProjectColumn, SnowflakeID.from_short_code(target_uid)) if target_uid else None
        stage = db.exec(
            SqlBuilder.select.table(WorkflowStageDefinition)
            .where(WorkflowStageDefinition.key == "review")
            .with_for_update()
        ).first()
        if (
            target is None
            or target.project_id != project_id
            or target.deleted_at
            or target.is_archive
            or target.workflow_stage != "review"
            or stage is None
            or not stage.is_builtin
            or not stage.is_active
            or stage.counts_as_completed
        ):
            raise AppGovernanceDenied()
        source_uid = SnowflakeID(card.project_column_id).to_short_code()
        project = _current(db, Project, project_id)
        service = DomainService().card
        if service.change_order(actor, project, card, service.repo.card.get_next_order(target), target) is not True:
            raise AppGovernanceDenied()
        _, _, review_snapshot = _configuration(
            db, actor, project_id, card_id, intent.app_key, intent.connection_id, workflow_stage="review"
        )
        receipt = {
            "schema_version": 1,
            "request_uid": intent.get_uid(),
            "card_uid": card.get_uid(),
            "generation": generation,
            "workflow_stage": "review",
            "state": "moved",
            "from_column_uid": source_uid,
            "to_column_uid": target_uid,
            "inputs": inputs,
            "binding_revision": expected_binding_revision,
            "evidence_reference": evidence_reference,
            "evidence_kind": "app_attestation",
            "workflow_transitioned": True,
            "card_revision": int(SafeDateTime.fromisoformat(review_snapshot["card_revision"]).timestamp() * 1000000),
            "started": False,
            "execution_resumed": False,
        }
        db.insert(AppExecutionReview(request_id=request_id, snapshot=review_snapshot, receipt=receipt))
        return {**receipt, "changed": True}
