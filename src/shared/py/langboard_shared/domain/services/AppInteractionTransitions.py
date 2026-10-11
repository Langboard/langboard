"""Explicit human application of an accepted choice through native card movement."""

from ...core.db import DbSession, SqlBuilder
from ...core.security.CollaborationChannel import CollaborationChannel
from ...core.types import SnowflakeID
from ..models import (
    AppCardInteraction,
    AppCardInteractionResponse,
    AppCardInteractionTransition,
    Card,
    Project,
    ProjectAssignedUser,
    ProjectRole,
    User,
)
from .AppCardInteractions import (
    AppCardInteractionConflict,
    _scope_principal,
    _validate_workflow_snapshot,
    respond_to_app_card_interaction,
)
from .AppConnectionAuthentication import AuthenticatedAppConnection, _connection
from .AppGovernance import AppGovernanceDenied, _current


def apply_interaction_transition(
    service, actor, project_id, card_id, interaction_id, response_id, expected_card_revision, channel=None
):
    if type(expected_card_revision) is not int or expected_card_revision < 0:
        raise ValueError("A nonnegative card revision is required")
    if not isinstance(actor, User):
        raise AppGovernanceDenied()
    with DbSession.atomic() as db:
        project = _current(db, Project, project_id)
        card = _current(db, Card, card_id)
        current = _current(db, User, actor.id)
        if (
            project is None
            or project.deleted_at
            or card is None
            or card.project_id != project_id
            or card.deleted_at
            or card.archived_at
            or current is None
            or current.deleted_at
            or not current.activated_at
            or service.card.resolve_readable_card(project_id, card_id, current, channel or CollaborationChannel.Api)
            is None
        ):
            raise AppGovernanceDenied()
        if project.owner_id != current.id:
            member = db.exec(
                SqlBuilder.select.table(ProjectAssignedUser).where(
                    ProjectAssignedUser.project_id == project_id, ProjectAssignedUser.user_id == current.id
                )
            ).first()
            role = db.exec(
                SqlBuilder.select.table(ProjectRole).where(
                    ProjectRole.project_id == project_id, ProjectRole.user_id == current.id
                )
            ).first()
            if member is None or role is None or not role.is_granted("card_update"):
                raise AppGovernanceDenied()
        row = _current(db, AppCardInteraction, interaction_id)
        answer = _current(db, AppCardInteractionResponse, response_id)
        if (
            row is None
            or row.project_id != project_id
            or row.card_id != card_id
            or answer is None
            or answer.interaction_id != interaction_id
        ):
            raise AppGovernanceDenied()
        connection = _connection(db, row.connection_id)
        principal = AuthenticatedAppConnection(
            connection.app_key, connection.id, 0, connection.owner_id, connection.organization_id
        )
        _scope_principal(db, principal, project_id, card_id)
        if row.app_key != principal.app_key:
            raise AppGovernanceDenied()
        retained = db.exec(
            SqlBuilder.select.table(AppCardInteractionTransition).where(
                AppCardInteractionTransition.response_id == answer.id
            )
        ).first()
        if retained:
            if retained.actor_id != current.id or retained.expected_card_revision != expected_card_revision:
                raise AppCardInteractionConflict()
            return {**retained.receipt, "changed": False}
        if card.last_change_seq != expected_card_revision:
            raise AppCardInteractionConflict()
        responder = _current(db, User, answer.actor_id)
        respond_to_app_card_interaction(
            service,
            responder,
            project_id,
            card_id,
            row.id,
            answer.expected_revision,
            answer.idempotency_key,
            answer.values,
            answer.execution_generation,
            channel,
            _retained_response=True,
        )
        plan = _validate_workflow_snapshot(db, row, answer.values)
        if plan["state"] != "planned":
            raise AppCardInteractionConflict()
        source_id = card.project_column_id
        target_id = SnowflakeID.from_short_code(plan["column_uid"])
        moved = source_id != target_id
        if moved and service.card.change_order(current, project, card, 0, target_id) is not True:
            raise AppCardInteractionConflict()
        result = _current(db, Card, card_id)
        if result.project_column_id != target_id:
            raise AppCardInteractionConflict()
        receipt = {
            "schema_version": 1,
            "interaction_uid": row.get_uid(),
            "response_uid": answer.get_uid(),
            "actor_uid": current.get_uid(),
            "from_column_uid": SnowflakeID(source_id).to_short_code(),
            "to_column_uid": plan["column_uid"],
            "workflow_stage": plan["workflow_stage"],
            "binding_revision": plan["binding_revision"],
            "column_revision": plan["column_revision"],
            "card_revision": result.last_change_seq,
            "workflow_transitioned": moved,
            "execution_resumed": False,
            "state": "moved" if moved else "already_at_target",
        }
        transition = AppCardInteractionTransition(
            response_id=answer.id, actor_id=current.id, expected_card_revision=expected_card_revision, receipt=receipt
        )
        db.insert(transition)
        return {**receipt, "changed": True}
