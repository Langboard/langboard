"""Unique workflow destinations must persist before apps consume their mapping."""

import pytest
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.types import SafeDateTime
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


def test_apps_choose_independent_destinations_for_an_ambiguous_stage(board, binding):
    actor, project = board[1:3]
    with DbSession.atomic() as db:
        for key in ("github", "external-erp"):
            db.insert(AppDefinition(
                key=key, approved_by=actor.id,
                declaration={"workflow_requirements": {"required": ["active"]}},
            ))
        db.insert(BoardAppBinding(project_id=project.id, app_key="external-erp", state="enabled"))
        other = ProjectColumn(project_id=project.id, name="ERP destination", workflow_stage="active")
        db.insert(other)
    destinations = {"github": board[5][0].get_uid(), "external-erp": other.get_uid()}
    for key, destination in destinations.items():
        initial = read_workflow_mapping(actor, project.id, key)
        choice = initial["choices"][0]
        assert choice["status"] == "ambiguous" and choice["column_uid"] is None
        assert set(choice["candidates"]) == set(destinations.values())
        saved = save_workflow_mapping(
            actor, project.id, key, {"active": destination}, False,
            initial["revision"], initial["app_revision"],
        )
        assert saved["choices"][0]["column_uid"] == destination
    with DbSession.atomic() as db:
        rows = db.exec(SqlBuilder.select.table(BoardAppBinding).where(BoardAppBinding.project_id == project.id)).all()
        assert {row.app_key: row.workflow_mapping for row in rows} == {
            key: {"active": destination} for key, destination in destinations.items()
        }
        for row in rows:
            assert row.stage_transitions_enabled is False
    for key, destination in destinations.items():
        current = read_workflow_mapping(actor, project.id, key)
        assert current["choices"][0]["status"] == "resolved"
        assert current["choices"][0]["column_uid"] == destination


@pytest.mark.parametrize("failure", ["missing", "ambiguous", "deleted", "stage_changed"])
def test_incomplete_mapping_cannot_enable_transitions_and_repair_requires_explicit_save(board, binding, failure):
    actor, project = board[1:3]
    with DbSession.atomic() as db:
        db.insert(AppDefinition(
            key="github", approved_by=actor.id,
            declaration={"workflow_requirements": {"required": ["active"]}},
        ))
    initial = read_workflow_mapping(actor, project.id, "github")
    original = board[5][0]
    selected = {} if failure in {"missing", "ambiguous"} else {"active": original.get_uid()}
    with DbSession.atomic() as db:
        column = db.exec(SqlBuilder.select.table(ProjectColumn).where(ProjectColumn.id == original.id)).first()
        if failure == "missing":
            column.workflow_stage = None
            db.update(column)
        elif failure == "ambiguous":
            db.insert(ProjectColumn(project_id=project.id, name="Other active", workflow_stage="active"))
        elif failure == "deleted":
            column.deleted_at = SafeDateTime.now()
            db.update(column)
        else:
            column.workflow_stage = "review"
            db.update(column)
    with pytest.raises(ValueError, match="Required stages must resolve"):
        save_workflow_mapping(actor, project.id, "github", selected, True, initial["revision"], initial["app_revision"])
    with DbSession.atomic() as db:
        stored = db.exec(SqlBuilder.select.table(BoardAppBinding).where(BoardAppBinding.id == binding.id)).first()
        assert stored.stage_transitions_enabled is False
        assert stored.workflow_mapping == {}
        replacement = ProjectColumn(project_id=project.id, name="Chosen replacement", workflow_stage="active")
        db.insert(replacement)
    repaired = read_workflow_mapping(actor, project.id, "github")
    assert repaired["enabled"] is False, "Repairing columns must not opt the board into automatic transitions"
    saved = save_workflow_mapping(
        actor, project.id, "github", {"active": replacement.get_uid()}, True,
        repaired["revision"], repaired["app_revision"],
    )
    assert saved["enabled"] is True
    with DbSession.atomic() as db:
        stored = db.exec(SqlBuilder.select.table(BoardAppBinding).where(BoardAppBinding.id == binding.id)).first()
        assert stored.stage_transitions_enabled is True
        assert stored.workflow_mapping == {"active": replacement.get_uid()}
