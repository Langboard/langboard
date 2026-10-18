"""Unique workflow destinations must persist before apps consume their mapping."""

import pytest
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.domain.models import AppDefinition, BoardAppBinding, ProjectColumn
from langboard_shared.domain.services.AppWorkflowMapping import read_workflow_mapping, save_workflow_mapping


@pytest.mark.parametrize("enabled", [False, True])
def test_unique_defaults_persist_and_do_not_follow_later_columns(board, binding, enabled):
    actor, project = board[1:3]
    with DbSession.atomic() as db:
        definition = AppDefinition(
            key="github", approved_by=actor.id,
            declaration={"workflow_requirements": {"required": ["active"], "optional": ["review", "ready"]}},
        )
        db.insert(definition)
    initial = read_workflow_mapping(actor, project.id, "github")
    assert initial["transitions_resolved"]
    supplied = {}
    saved = save_workflow_mapping(
        actor, project.id, "github", supplied, enabled, initial["revision"], initial["app_revision"],
    )
    assert saved["enabled"] is enabled
    expected = {column.workflow_stage: column.get_uid() for column in board[5] if column.workflow_stage in {"active", "review"}}
    with DbSession.atomic() as db:
        stored = db.exec(SqlBuilder.select.table(BoardAppBinding).where(BoardAppBinding.id == binding.id)).first()
        assert stored.workflow_mapping == expected
        assert supplied == {}, "Saving automatic choices must not mutate the caller's request"
        db.insert(ProjectColumn(project_id=project.id, name="Another destination", workflow_stage="active"))
    current = read_workflow_mapping(actor, project.id, "github")
    active = next(choice for choice in current["choices"] if choice["stage"] == "active")
    assert active["status"] == "resolved" and active["column_uid"] == expected["active"]
    with DbSession.atomic() as db:
        column = db.exec(SqlBuilder.select.table(ProjectColumn).where(ProjectColumn.id == board[5][0].id)).first()
        column.workflow_stage = "review"
        db.update(column)
    current = read_workflow_mapping(actor, project.id, "github")
    active = next(choice for choice in current["choices"] if choice["stage"] == "active")
    assert active["status"] == "invalid" and active["column_uid"] is None
    assert current["enabled"] is False
