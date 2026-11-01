"""App declarations select built-in workflow meanings, never introduce stages."""

from langboard_sdk import WorkflowRequirements
from ...core.db import DbSession, SqlBuilder
from ...publishers import AppSettingPublisher
from ..models import ProjectColumn, WorkflowStageDefinition
from .AppBoardConsent import _context
from .AppGovernance import AppGovernanceConflict, AppGovernanceDenied, current_policy, lock_policy_mutation
from .AppWorkflowPolicy import resolve_app_workflow
from .WorkflowGuidance import compose_workflow_guidance


def _requirements(definition):
    declaration = definition.declaration.get("workflow_requirements")
    if declaration is None:
        raise ValueError("The app has no workflow requirements")
    return WorkflowRequirements(tuple(declaration["required"]), tuple(declaration.get("optional", [])))


def _resolve(db, definition, project, explicit, *, for_update=True):
    # Match the native workflow editor: destinations must stay current until
    # consent preparation or the mapping save commits.
    requirements = _requirements(definition)
    column_statement = SqlBuilder.select.table(ProjectColumn).where(ProjectColumn.project_id == project.id)
    stage_statement = SqlBuilder.select.table(WorkflowStageDefinition).where(
        WorkflowStageDefinition.key.in_(requirements.required + requirements.optional)
    )
    columns = db.exec(column_statement.with_for_update() if for_update else column_statement).all()
    stages = db.exec(stage_statement.with_for_update() if for_update else stage_statement).all()
    result = resolve_app_workflow(
        requirements,
        project.id,
        columns,
        stages,
        explicit,
        authorized_column_ids=frozenset(column.id for column in columns),
    )
    columns_by_uid = {column.get_uid(): column for column in columns}
    stages_by_key = {stage.key: stage for stage in stages}
    return {
        "transitions_resolved": result.transitions_enabled,
        "choices": [
            {
                "stage": choice.stage,
                "required": choice.required,
                "stage_active": choice.stage_active,
                "status": choice.status,
                "column_uid": choice.column_uid,
                "candidates": list(choice.candidates),
                "workflow_guidance": compose_workflow_guidance(
                    stages_by_key[choice.stage].description if choice.stage in stages_by_key else "",
                    columns_by_uid[choice.column_uid].description if choice.column_uid in columns_by_uid else "",
                ),
            }
            for choice in result.choices
        ],
    }


def read_workflow_mapping(actor, project_id, app_key):
    with DbSession.atomic() as db:
        definition, project, binding = _context(db, actor, project_id, app_key, for_update=False)
        if binding is None:
            raise AppGovernanceDenied()
        resolved = _resolve(db, definition, project, binding.workflow_mapping, for_update=False)
        enabled = (
            binding.stage_transitions_enabled
            and resolved["transitions_resolved"]
            and definition.is_enabled
            and binding.state == "enabled"
            and current_policy(db, project.organization_id)["effective_mode"] != "disabled"
        )
        return {
            **resolved,
            "revision": binding.edit_revision(),
            "app_revision": definition.edit_revision(),
            "enabled": enabled,
        }


def save_workflow_mapping(actor, project_id, app_key, mapping, enabled, expected_revision, expected_app_revision):
    if (
        type(enabled) is not bool
        or not isinstance(mapping, dict)
        or len(mapping) > 6
        or any(not isinstance(key, str) or not isinstance(value, str) for key, value in mapping.items())
    ):
        raise ValueError("Invalid built-in workflow mapping")
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        definition, project, binding = _context(db, actor, project_id, app_key)
        if (
            binding is None
            or binding.edit_revision() != expected_revision
            or definition.edit_revision() != expected_app_revision
        ):
            raise AppGovernanceConflict()
        result = _resolve(db, definition, project, mapping)
        if enabled:
            if (
                not definition.is_enabled
                or binding.state != "enabled"
                or current_policy(db, project.organization_id)["effective_mode"] == "disabled"
            ):
                raise AppGovernanceDenied()
            if not result["transitions_resolved"]:
                raise ValueError("Required stages must resolve to active board columns")
        # Freeze unique defaults just like the native board workflow editor.
        # Runtime app actions consume this saved mapping, not display choices.
        binding.workflow_mapping = {
            **mapping,
            **{choice["stage"]: choice["column_uid"] for choice in result["choices"] if choice["status"] == "resolved"},
        }
        binding.stage_transitions_enabled = enabled
        db.update(binding)
        db.after_commit(AppSettingPublisher.apps_changed)
        return {
            **result,
            "revision": binding.edit_revision(),
            "app_revision": definition.edit_revision(),
            "enabled": enabled,
        }
