"""App-owned structured requests, independent of inbox signals and runtime control."""

from datetime import datetime, timedelta
from langboard_sdk.interactions import validate_card_interaction
from ...core.db import DbSession, SqlBuilder
from ...core.types import SafeDateTime
from ..models import (
    AppCardInteraction,
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
    return {
        "schema_version": 1,
        "interaction_uid": row.get_uid(),
        "revision": row.revision,
        "state": "expired" if row.state == "pending" and _expired(row.expires_at, SafeDateTime.now()) else row.state,
        "request": row.request,
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
