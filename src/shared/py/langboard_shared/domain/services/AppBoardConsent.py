"""Explicit board grants, checked against current board authority and app policy."""

from ...core.db import DbSession, SqlBuilder
from ...publishers import AppSettingPublisher
from ..models import AppDefinition, BoardAppBinding, Project, ProjectAssignedUser, ProjectRole
from ..models.ProjectRole import ProjectRoleAction
from .AppGovernance import (
    AppGovernanceConflict,
    AppGovernanceDenied,
    _actor,
    _current,
    current_policy,
    lock_policy_mutation,
)


def _context(db, actor, project_id, app_key, *, lock_binding=True):
    actor = _actor(db, actor)
    definition = db.exec(
        SqlBuilder.select.table(AppDefinition).where(AppDefinition.key == app_key).with_for_update()
    ).first()
    project = _current(db, Project, project_id)
    if definition is None or project is None or project.deleted_at:
        raise AppGovernanceDenied()
    if not actor.is_admin and actor.id != project.owner_id:
        member = db.exec(
            SqlBuilder.select.table(ProjectAssignedUser)
            .where(ProjectAssignedUser.project_id == project.id, ProjectAssignedUser.user_id == actor.id)
            .with_for_update()
        ).first()
        role = db.exec(
            SqlBuilder.select.table(ProjectRole)
            .where(ProjectRole.project_id == project.id, ProjectRole.user_id == actor.id)
            .with_for_update()
        ).first()
        if member is None or role is None or not role.is_granted(ProjectRoleAction.Update):
            raise AppGovernanceDenied()
    statement = SqlBuilder.select.table(BoardAppBinding).where(
        BoardAppBinding.project_id == project.id, BoardAppBinding.app_key == app_key
    )
    binding = db.exec(statement.with_for_update() if lock_binding else statement).first()
    return definition, project, binding


def _response(definition, binding):
    return {
        "binding_uid": binding.get_uid(),
        "revision": binding.edit_revision(),
        "app_revision": definition.edit_revision(),
        "state": binding.state,
        "granted_capabilities": binding.granted_capabilities,
        "stage_transitions_enabled": binding.stage_transitions_enabled,
    }


def prepare_board_consent(actor, project_id, app_key):
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        definition, project, binding = _context(db, actor, project_id, app_key)
        if binding is None:
            if not definition.is_enabled or current_policy(db, project.organization_id)["effective_mode"] == "disabled":
                raise AppGovernanceDenied()
            binding = BoardAppBinding(project_id=project.id, app_key=app_key)
            db.insert(binding)
        return _response(definition, binding)


def save_board_consent(actor, project_id, app_key, capabilities, expected_revision, expected_app_revision):
    if (
        not isinstance(capabilities, list)
        or len(capabilities) > 32
        or any(not isinstance(value, str) for value in capabilities)
        or len(set(capabilities)) != len(capabilities)
    ):
        raise ValueError("Board capabilities must be a unique bounded list")
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        definition, project, binding = _context(db, actor, project_id, app_key)
        if (
            binding is None
            or binding.edit_revision() != expected_revision
            or definition.edit_revision() != expected_app_revision
        ):
            raise AppGovernanceConflict()
        if capabilities:
            if not definition.is_enabled or current_policy(db, project.organization_id)["effective_mode"] == "disabled":
                raise AppGovernanceDenied()
            if not set(capabilities).issubset(definition.declaration["capabilities"]):
                raise ValueError("Board grants must be declared by the approved app")
            if "signals.read" in capabilities and not definition.declaration.get("signal_sources"):
                raise ValueError("Signal access requires declared sources")
        binding.granted_capabilities = sorted(capabilities)
        binding.state = "enabled" if capabilities else "disabled"
        if not capabilities:
            binding.stage_transitions_enabled = False
        if "events.receive" not in capabilities:
            if binding.archive_sync_types:
                binding.archive_sync_revision += 1
            binding.archive_sync_types = []
        db.update(binding)
        db.after_commit(AppSettingPublisher.apps_changed)
        return _response(definition, binding)
