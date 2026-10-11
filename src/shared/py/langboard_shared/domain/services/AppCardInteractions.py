"""App-owned structured requests, independent of inbox signals and runtime control."""

from datetime import datetime, timedelta
from langboard_sdk.interactions import validate_card_interaction, validate_card_interaction_values
from ...core.db import DbSession, SqlBuilder
from ...core.types import SafeDateTime, SnowflakeID
from ..models import (
    AppCardInteraction,
    AppCardInteractionResponse,
    AppConnection,
    AppDefinition,
    AppResourceBinding,
    BoardAppBinding,
    Card,
    CardAppOwnership,
    CardAppResourceSelection,
    CardComment,
    Project,
    ProjectRole,
    User,
)
from .AppConnectionAuthentication import authenticate_connection_credential
from .AppEventDelivery import _expired
from .AppExecutionAcknowledgments import _request
from .AppExecutionGrant import AppExecutionCredentialDenied
from .AppGovernance import AppGovernanceDenied, _current, require_connection_access
from .DomainService import DomainService


class AppCardInteractionConflict(Exception):
    pass


def _scope(db, token, project_id, card_id):
    try:
        principal = authenticate_connection_credential(token)
    except AppGovernanceDenied as exc:
        raise AppExecutionCredentialDenied() from exc
    return _scope_principal(db, principal, project_id, card_id)


def _scope_principal(db, principal, project_id, card_id):
    project = _current(db, Project, project_id)
    card = _current(db, Card, card_id)
    connection = _current(db, AppConnection, principal.connection_id)
    actor = _current(db, User, principal.owner_id)
    if (
        any(row is None for row in (project, card, connection, actor))
        or card.project_id != project.id
        or card.deleted_at
        or card.archived_at
        or card.source_type
    ):
        raise AppGovernanceDenied()
    require_connection_access(db, actor, project, connection, unattended=True)
    role = db.exec(
        SqlBuilder.select.table(ProjectRole).where(
            ProjectRole.project_id == project.id, ProjectRole.user_id == actor.id
        )
    ).first()
    if project.owner_id != actor.id and (role is None or not role.is_granted("card_update")):
        raise AppGovernanceDenied()
    if card.visibility not in ("SHARED", "INTERNAL") or (
        card.visibility == "INTERNAL" and not DomainService().card._resolve_internal_access(actor)
    ):
        raise AppGovernanceDenied()
    owner = db.exec(
        SqlBuilder.select.table(CardAppOwnership).where(CardAppOwnership.card_id == card.id).with_for_update()
    ).first()
    selection = db.exec(
        SqlBuilder.select.table(CardAppResourceSelection)
        .where(CardAppResourceSelection.card_id == card.id, CardAppResourceSelection.app_key == principal.app_key)
        .with_for_update()
    ).first()
    definition = db.exec(
        SqlBuilder.select.table(AppDefinition).where(AppDefinition.key == principal.app_key).with_for_update()
    ).first()
    binding = db.exec(
        SqlBuilder.select.table(BoardAppBinding)
        .where(BoardAppBinding.project_id == project.id, BoardAppBinding.app_key == principal.app_key)
        .with_for_update()
    ).first()
    if (
        owner is None
        or owner.app_key != principal.app_key
        or selection is None
        or selection.connection_id != connection.id
        or not selection.resource_uids
        or definition is None
        or not definition.is_enabled
        or binding is None
        or binding.state != "enabled"
        or "interactions.request" not in definition.declaration.get("capabilities", [])
        or "interactions.request" not in binding.granted_capabilities
    ):
        raise AppGovernanceDenied()
    from ...core.types import SnowflakeID

    resources = db.exec(
        SqlBuilder.select.table(AppResourceBinding)
        .where(
            AppResourceBinding.id.in_([SnowflakeID.from_short_code(uid) for uid in selection.resource_uids]),
            AppResourceBinding.connection_id == connection.id,
            AppResourceBinding.board_binding_id == binding.id,
            AppResourceBinding.is_selected == True,  # noqa: E712
            AppResourceBinding.access_state == "granted",
            AppResourceBinding.resource_type.in_(definition.declaration.get("resource_types", [])),
        )
        .with_for_update()
    ).all()
    if {r.get_uid() for r in resources} != set(selection.resource_uids):
        raise AppGovernanceDenied()
    return principal, card


def _receipt(row):
    with DbSession.use(readonly=False) as db:
        answer = db.exec(
            SqlBuilder.select.table(AppCardInteractionResponse).where(
                AppCardInteractionResponse.interaction_id == row.id
            )
        ).first()
    return {
        "schema_version": 1,
        "interaction_uid": row.get_uid(),
        "revision": row.revision,
        "state": "answered"
        if answer
        else "expired"
        if row.state == "pending" and _expired(row.expires_at, SafeDateTime.now())
        else row.state,
        "request": row.request,
        "response": {
            "response_uid": answer.get_uid(),
            "actor_uid": SnowflakeID(answer.actor_id).to_short_code(),
            "values": answer.values,
            "execution_generation": answer.execution_generation,
            "accepted_at": answer.created_at.isoformat(),
        }
        if answer
        else None,
        "execution_resumed": False,
    }


def create_app_card_interaction(token, project_id, card_id, body):
    request = validate_card_interaction(body)
    expiry = datetime.fromisoformat(request["expires_at"])
    with DbSession.atomic() as db:
        principal, card = _scope(db, token, project_id, card_id)
        if "execution" in request:
            from ...core.types import SnowflakeID

            context = request["execution"]
            execution = _request(db, token, project_id, card_id, SnowflakeID.from_short_code(context["request_uid"]))
            if execution.generation != context["generation"]:
                raise AppCardInteractionConflict()
        presentation = request["presentation"]
        if presentation["type"] == "comment_reaction":
            from ...core.types import SnowflakeID

            comment = _current(db, CardComment, SnowflakeID.from_short_code(presentation["comment_uid"]))
            if comment is None or comment.card_id != card.id:
                raise AppGovernanceDenied()
        row = db.exec(
            SqlBuilder.select.table(AppCardInteraction)
            .where(
                AppCardInteraction.connection_id == principal.connection_id,
                AppCardInteraction.card_id == card_id,
                AppCardInteraction.app_request_key == request["app_request_key"],
            )
            .with_for_update()
        ).first()
        if row is not None:
            if row.request != request:
                raise AppCardInteractionConflict()
            return {**_receipt(row), "changed": False}
        now = SafeDateTime.now()
        if expiry <= now or expiry > now + timedelta(days=7):
            raise ValueError("Interaction expiry must be within seven days")
        row = AppCardInteraction(
            project_id=project_id,
            card_id=card_id,
            connection_id=principal.connection_id,
            app_key=principal.app_key,
            app_request_key=request["app_request_key"],
            request=request,
            expires_at=expiry,
        )
        db.insert(row)
        return {**_receipt(row), "changed": True}


def read_app_card_interaction(token, project_id, card_id, interaction_id):
    with DbSession.atomic() as db:
        principal, _ = _scope(db, token, project_id, card_id)
        row = db.exec(
            SqlBuilder.select.table(AppCardInteraction).where(
                AppCardInteraction.id == interaction_id,
                AppCardInteraction.card_id == card_id,
                AppCardInteraction.project_id == project_id,
                AppCardInteraction.connection_id == principal.connection_id,
                AppCardInteraction.app_key == principal.app_key,
            )
        ).first()
        if row is None:
            raise AppGovernanceDenied()
        return _receipt(row)


def cancel_app_card_interaction(token, project_id, card_id, interaction_id, expected_revision):
    """Retain the immutable request and fence cancellation against its revision."""
    if type(expected_revision) is not int or expected_revision < 1:
        raise ValueError("A positive interaction revision is required")
    with DbSession.atomic() as db:
        principal, _ = _scope(db, token, project_id, card_id)
        row = db.exec(
            SqlBuilder.select.table(AppCardInteraction)
            .where(
                AppCardInteraction.id == interaction_id,
                AppCardInteraction.card_id == card_id,
                AppCardInteraction.project_id == project_id,
                AppCardInteraction.connection_id == principal.connection_id,
                AppCardInteraction.app_key == principal.app_key,
            )
            .with_for_update()
        ).first()
        if row is None:
            raise AppGovernanceDenied()
        if _receipt(row)["state"] == "answered":
            raise AppCardInteractionConflict()
        if row.state == "cancelled" and row.revision == expected_revision + 1:
            return {**_receipt(row), "changed": False}
        if row.revision != expected_revision or row.state != "pending":
            raise AppCardInteractionConflict()
        if _expired(row.expires_at, SafeDateTime.now()):
            # Expiry is already authoritative; cancellation cannot change it.
            return {**_receipt(row), "changed": False}
        row.state = "cancelled"
        row.revision += 1
        db.update(row)
        return {**_receipt(row), "changed": True}


def respond_to_app_card_interaction(
    service,
    actor,
    project_id,
    card_id,
    interaction_id,
    expected_revision,
    idempotency_key,
    values,
    execution_generation=None,
    channel=None,
):
    """Accept one current human answer; delivery and workflow outcomes are separate."""
    import re
    from ...core.security.CollaborationChannel import CollaborationChannel
    from ...core.types import SnowflakeID
    from ..models import AppExecutionRequest, ProjectAssignedUser
    from .AppConnectionAuthentication import AuthenticatedAppConnection, _connection
    from .AppExecutionGrant import evaluate_connection_execution_grant

    if type(expected_revision) is not int or expected_revision < 1:
        raise ValueError("A positive interaction revision is required")
    if not isinstance(idempotency_key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", idempotency_key):
        raise ValueError("Invalid response idempotency key")
    if execution_generation is not None and (type(execution_generation) is not int or execution_generation < 1):
        raise ValueError("Invalid execution generation")
    if not isinstance(actor, User):
        raise AppGovernanceDenied()
    with DbSession.atomic() as db:
        # Match app mutations: project/card locks precede user and request locks.
        project = _current(db, Project, project_id)
        card = _current(db, Card, card_id)
        current = _current(db, User, actor.id)
        if (
            current is None
            or current.deleted_at
            or not current.activated_at
            or project is None
            or project.deleted_at
            or card is None
            or card.project_id != project_id
            or card.deleted_at
            or card.archived_at
        ):
            raise AppGovernanceDenied()
        if (
            service.card.resolve_readable_card(project_id, card_id, current, channel or CollaborationChannel.Api)
            is None
        ):
            raise AppGovernanceDenied()
        if project.owner_id != current.id:
            member = db.exec(
                SqlBuilder.select.table(ProjectAssignedUser).where(
                    ProjectAssignedUser.project_id == project.id, ProjectAssignedUser.user_id == current.id
                )
            ).first()
            role = db.exec(
                SqlBuilder.select.table(ProjectRole).where(
                    ProjectRole.project_id == project.id, ProjectRole.user_id == current.id
                )
            ).first()
            if member is None or role is None or not role.is_granted("card_update"):
                raise AppGovernanceDenied()
        row = db.exec(
            SqlBuilder.select.table(AppCardInteraction)
            .where(
                AppCardInteraction.id == interaction_id,
                AppCardInteraction.project_id == project_id,
                AppCardInteraction.card_id == card_id,
            )
            .with_for_update()
        ).first()
        if row is None:
            raise AppGovernanceDenied()
        connection = _connection(db, row.connection_id)
        principal = AuthenticatedAppConnection(
            connection.app_key, connection.id, 0, connection.owner_id, connection.organization_id
        )
        _scope_principal(db, principal, project_id, card_id)
        if row.app_key != principal.app_key:
            raise AppGovernanceDenied()
        if row.request["presentation"]["type"] == "comment_reaction":
            # Reaction requests must be resolved by a new native reaction event.
            raise AppGovernanceDenied()
        context = row.request.get("execution")
        if context:
            if execution_generation != context["generation"]:
                raise AppCardInteractionConflict()
            execution = _current(db, AppExecutionRequest, SnowflakeID.from_short_code(context["request_uid"]))
            if (
                execution is None
                or execution.card_id != card_id
                or execution.project_id != project_id
                or execution.app_key != row.app_key
                or execution.connection_id != row.connection_id
                or execution.generation != execution_generation
            ):
                raise AppGovernanceDenied()
            authority = evaluate_connection_execution_grant(
                row.connection_id, project_id, card_id, execution_generation
            )
            if authority["authority_version"] != execution.authority.get("authority_version"):
                raise AppCardInteractionConflict()
        elif execution_generation is not None:
            raise AppCardInteractionConflict()
        validated = validate_card_interaction_values(row.request, values)
        answer = db.exec(
            SqlBuilder.select.table(AppCardInteractionResponse).where(
                AppCardInteractionResponse.interaction_id == row.id
            )
        ).first()
        if answer:
            if (
                answer.actor_id != current.id
                or answer.expected_revision != expected_revision
                or answer.idempotency_key != idempotency_key
                or answer.values != validated
                or answer.execution_generation != execution_generation
            ):
                raise AppCardInteractionConflict()
            changed = False
        else:
            if (
                row.revision != expected_revision
                or row.state != "pending"
                or _expired(row.expires_at, SafeDateTime.now())
            ):
                raise AppCardInteractionConflict()
            answer = AppCardInteractionResponse(
                interaction_id=row.id,
                actor_id=current.id,
                expected_revision=expected_revision,
                idempotency_key=idempotency_key,
                values=validated,
                execution_generation=execution_generation,
            )
            db.insert(answer)
            row.revision += 1
            db.update(row)
            changed = True
        return {
            "schema_version": 1,
            "interaction_uid": row.get_uid(),
            "response_uid": answer.get_uid(),
            "actor_uid": current.get_uid(),
            "revision": row.revision,
            "state": "answered",
            "values": answer.values,
            "changed": changed,
            "execution_resumed": False,
            "workflow_transitioned": False,
            "delivery_state": "not_queued",
        }
