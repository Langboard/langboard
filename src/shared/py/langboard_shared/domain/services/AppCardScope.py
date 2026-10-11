"""Current app ownership, consent and selected-resource boundary for native cards."""

from ...core.db import SqlBuilder
from ..models import (
    AppConnection,
    AppDefinition,
    AppResourceBinding,
    BoardAppBinding,
    Card,
    CardAppOwnership,
    CardAppResourceSelection,
    Project,
    ProjectRole,
    User,
)
from .AppGovernance import AppGovernanceDenied, _current, require_connection_access
from .DomainService import DomainService


def require_app_card_scope(db, principal, project_id, card_id, *, capability, role_action):
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
    if project.owner_id != actor.id and (role is None or not role.is_granted(role_action)):
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
        or capability not in definition.declaration.get("capabilities", [])
        or capability not in binding.granted_capabilities
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
    return card, owner, selection, binding, resources

