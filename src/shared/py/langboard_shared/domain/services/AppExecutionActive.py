"""Explicit mapped Active handoff after a retained start attestation."""

from hmac import compare_digest
from ...core.db import DbSession, SqlBuilder
from ...core.types import SafeDateTime, SnowflakeID
from ..models import (
    AppExecutionActive,
    AppExecutionLease,
    AppExecutionStart,
    Card,
    CardExecutionIntent,
    Project,
    ProjectColumn,
    User,
    WorkflowStageDefinition,
)
from .AppConnectionAuthentication import authenticate_connection_credential
from .AppExecutionLeases import _allowed, _hash, check_app_runtime
from .AppGovernance import AppGovernanceConflict, AppGovernanceDenied, _current, lock_policy_mutation
from .CardExecutionIntent import _configuration
from .DomainService import DomainService


def _receipt(row, *, changed=False):
    return {
        "schema_version": 1,
        "handoff_uid": row.get_uid(),
        "state": "active_recorded",
        "changed": changed,
        "request_uid": SnowflakeID(row.request_id).to_short_code(),
        "card_uid": SnowflakeID(row.card_id).to_short_code(),
        "generation": row.generation,
        "lease_uid": SnowflakeID(row.lease_id).to_short_code(),
        "column_uid": SnowflakeID(row.column_id).to_short_code(),
        "inputs": row.inputs,
        "execution_resumed": False,
    }


def activate_app_runtime(
    token, project_id, card_id, request_id, lease_id, runtime_token, expected_card_revision, expected_binding_revision
):
    if (
        type(expected_card_revision) is not int
        or expected_card_revision < 0
        or not isinstance(expected_binding_revision, str)
    ):
        raise ValueError("Observed card and binding revisions required")
    digest = _hash(runtime_token)
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        principal = authenticate_connection_credential(token)
        intent = _current(db, CardExecutionIntent, request_id, for_update=False)
        if (
            not intent
            or intent.card_id != card_id
            or intent.app_key != principal.app_key
            or intent.connection_id != principal.connection_id
        ):
            raise AppGovernanceDenied()
        lease = _current(db, AppExecutionLease, lease_id, for_update=False)
        if (
            not lease
            or lease.request_id != request_id
            or lease.credential_id != principal.credential_id
            or not compare_digest(lease.runtime_token_hash, digest)
        ):
            raise AppGovernanceDenied()
        if (
            not _allowed(db, lease, intent, project_id)
            or lease.state != "authorized"
            or lease.expires_at <= SafeDateTime.now()
        ):
            raise AppGovernanceDenied()
        lease = _current(db, AppExecutionLease, lease_id)
        inputs = {
            "expected_card_revision": expected_card_revision,
            "expected_binding_revision": expected_binding_revision,
        }
        previous = db.exec(
            SqlBuilder.select.table(AppExecutionActive).where(AppExecutionActive.lease_id == lease_id)
        ).first()
        if previous:
            if previous.inputs != inputs:
                raise AppGovernanceConflict()
            return _receipt(previous)
        start = db.exec(
            SqlBuilder.select.table(AppExecutionStart).where(AppExecutionStart.lease_id == lease_id)
        ).first()
        if start is None:
            raise AppGovernanceDenied()
        actor = _current(db, User, intent.actor_id)
        card, _, snapshot = _configuration(db, actor, project_id, card_id, intent.app_key, intent.connection_id)
        if (
            int(SafeDateTime.fromisoformat(snapshot["card_revision"]).timestamp() * 1000000) != expected_card_revision
            or snapshot["binding_revision"] != expected_binding_revision
        ):
            raise AppGovernanceConflict()
        from ..models import BoardAppBinding

        binding = db.exec(
            SqlBuilder.select.table(BoardAppBinding).where(
                BoardAppBinding.project_id == project_id, BoardAppBinding.app_key == intent.app_key
            )
        ).first()
        column_uid = binding.workflow_mapping.get("active")

        column = _current(db, ProjectColumn, SnowflakeID.from_short_code(column_uid)) if column_uid else None
        stage = db.exec(
            SqlBuilder.select.table(WorkflowStageDefinition).where(WorkflowStageDefinition.key == "active")
        ).first()
        if (
            not column
            or column.project_id != project_id
            or column.deleted_at
            or column.is_archive
            or column.workflow_stage != "active"
            or not stage
            or not stage.is_builtin
            or not stage.is_active
        ):
            raise AppGovernanceDenied()
        project = _current(db, Project, project_id)
        service = DomainService().card
        order = service.repo.card.get_next_order(column)
        if service.change_order(actor, project, card, order, column) is not True:
            raise AppGovernanceDenied()
        _, _, active_snapshot = _configuration(
            db, actor, project_id, card_id, intent.app_key, intent.connection_id, workflow_stage="active"
        )
        row = AppExecutionActive(
            lease_id=lease_id,
            start_id=start.id,
            request_id=request_id,
            project_id=project_id,
            card_id=card_id,
            column_id=column.id,
            generation=intent.execution_generation,
            snapshot=active_snapshot,
            inputs=inputs,
        )
        db.insert(row)
        return _receipt(row, changed=True)


def read_app_active_handoff(token, project_id, card_id, request_id, lease_id, runtime_token):
    principal = authenticate_connection_credential(token)
    with DbSession.atomic() as db:
        permit = check_app_runtime(lease_id, runtime_token, renew=False)
        intent = _current(db, CardExecutionIntent, request_id, for_update=False)
        card = _current(db, Card, card_id, for_update=False)
        if (
            card is None
            or card.project_id != project_id
            or not intent
            or intent.card_id != card_id
            or intent.connection_id != principal.connection_id
            or intent.app_key != principal.app_key
            or permit["request_uid"] != intent.get_uid()
            or not permit["permit_execution"]
        ):
            raise AppGovernanceDenied()
        row = db.exec(
            SqlBuilder.select.table(AppExecutionActive).where(
                AppExecutionActive.lease_id == lease_id, AppExecutionActive.project_id == project_id
            )
        ).first()
        return (
            _receipt(row)
            if row
            else {
                "schema_version": 1,
                "request_uid": intent.get_uid(),
                "card_uid": SnowflakeID(card_id).to_short_code(),
                "lease_uid": SnowflakeID(lease_id).to_short_code(),
                "generation": intent.execution_generation,
                "state": "not_recorded",
                "changed": False,
                "execution_resumed": False,
            }
        )
