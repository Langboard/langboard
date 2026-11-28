"""Explicit board grants, checked against current board authority and app policy."""

from ...core.db import DbSession, SqlBuilder
from ...publishers import AppSettingPublisher
from ..models import AppDefinition, BoardAppBinding
from ..models.ProjectRole import ProjectRoleAction
from .AppGovernance import (
    AppGovernanceConflict,
    AppGovernanceDenied,
    _actor,
    _authorized_board,
    current_policy,
    lock_policy_mutation,
)


def _context(
    db, actor, project_id, app_key, *, lock_binding=True, for_update=True, board_action=ProjectRoleAction.Update
):
    actor = _actor(db, actor, for_update=for_update)
    definition_statement = SqlBuilder.select.table(AppDefinition).where(AppDefinition.key == app_key)
    definition = db.exec(definition_statement.with_for_update() if for_update else definition_statement).first()
    if definition is None:
        raise AppGovernanceDenied()
    project = _authorized_board(db, actor, project_id, for_update=for_update, action=board_action)
    statement = SqlBuilder.select.table(BoardAppBinding).where(
        BoardAppBinding.project_id == project.id, BoardAppBinding.app_key == app_key
    )
    binding = db.exec(statement.with_for_update() if for_update and lock_binding else statement).first()
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
            if definition.declaration.get("workflow_requirements") is not None:
                # Reuse the same resolver as explicit workflow saves; importing here
                # avoids the mapping module's dependency on this authorization context.
                from .AppWorkflowMapping import _resolve

                result = _resolve(db, definition, project, {})
                binding.workflow_mapping = {
                    choice["stage"]: choice["column_uid"] for choice in result["choices"] if choice["status"] == "resolved"
                }
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
        if "workflow.transition" not in capabilities:
            binding.stage_transitions_enabled = False
        if "events.receive" not in capabilities:
            if binding.archive_sync_types:
                binding.archive_sync_revision += 1
            binding.archive_sync_types = []
        db.update(binding)
        db.after_commit(AppSettingPublisher.apps_changed)
        return _response(definition, binding)
