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


@pytest.mark.parametrize("mode", ["unique", "ambiguous", "missing", "no_contract"])
def test_board_consent_prepares_unique_workflow_without_granting_execution(board, mode):
    from langboard_shared.domain.services.AppBoardConsent import prepare_board_consent

    actor, project = board[1:3]
    declaration = {"capabilities": ["signals.read"], "signal_sources": ["external-erp"]}
    if mode != "no_contract":
        declaration["workflow_requirements"] = {"required": ["active"], "optional": ["review", "ready"]}
    with DbSession.atomic() as db:
        board[4].actions = ["read", "update"]
        db.update(board[4])
        db.insert(AppDefinition(key="external-erp", approved_by=actor.id, declaration=declaration))
        if mode == "ambiguous":
            db.insert(ProjectColumn(project_id=project.id, name="Another active", workflow_stage="active"))
        elif mode == "missing":
            column = db.exec(SqlBuilder.select.table(ProjectColumn).where(ProjectColumn.id == board[5][0].id)).first()
            column.workflow_stage = None
            db.update(column)
    prepared = prepare_board_consent(actor, project.id, "external-erp")
    with DbSession.atomic() as db:
        stored = db.exec(SqlBuilder.select.table(BoardAppBinding).where(
            BoardAppBinding.project_id == project.id, BoardAppBinding.app_key == "external-erp",
        )).first()
        expected = {} if mode == "no_contract" else {"review": board[5][1].get_uid()}
        if mode == "unique":
            expected["active"] = board[5][0].get_uid()
        assert stored.workflow_mapping == expected
        assert stored.granted_capabilities == []
        assert stored.stage_transitions_enabled is False
        revision = stored.edit_revision()
        assert prepared["revision"] == revision
        db.insert(ProjectColumn(project_id=project.id, name="Later active", workflow_stage="active"))
    repeated = prepare_board_consent(actor, project.id, "external-erp")
    assert repeated["revision"] == revision, "Repeated setup must not silently replace existing board choices"


@pytest.mark.parametrize("board", ["postgresql-test"], indirect=True)
@pytest.mark.parametrize("destination", ["column", "stage"])
def test_mapping_save_serializes_destination_invalidation(board, binding, monkeypatch, destination):
    import importlib
    from langboard_shared.core.db.DbEngine import DbEngine
    from langboard_shared.domain.models import WorkflowStageDefinition
    from sqlalchemy import update
    from sqlalchemy.exc import OperationalError

    actor, project = board[1:3]
    with DbSession.atomic() as db:
        db.insert(AppDefinition(
            key="github", approved_by=actor.id,
            declaration={"workflow_requirements": {"required": ["active"]}},
        ))
    initial = read_workflow_mapping(actor, project.id, "github")
    module = importlib.import_module("langboard_shared.domain.services.AppWorkflowMapping")
    resolve = module._resolve
    target = board[5][0] if destination == "column" else board[6][0]
    model = ProjectColumn if destination == "column" else WorkflowStageDefinition
    values = {"workflow_stage": "review"} if destination == "column" else {"is_active": False}
    invalidation = update(model).where(model.id == target.id).values(**values)
    blocked = []

    def invalidate_after_resolution(*args, **kwargs):
        result = resolve(*args, **kwargs)
        # A separate transaction attempts the same row mutation as the native
        # column/stage editor after resolution but before the mapping is saved.
        try:
            with DbEngine.get_main_engine().begin() as connection:
                connection.exec_driver_sql("SET LOCAL lock_timeout = '50ms'")
                connection.execute(invalidation)
        except OperationalError as error:
            assert error.orig.sqlstate == "55P03"
            blocked.append(True)
        return result

    with monkeypatch.context() as patch:
        patch.setattr(module, "_resolve", invalidate_after_resolution)
        saved = save_workflow_mapping(
            actor, project.id, "github", {}, True, initial["revision"], initial["app_revision"],
        )
    current = read_workflow_mapping(actor, project.id, "github")
    assert saved["enabled"] and current["enabled"], "Save must not enable an already invalidated destination"
    assert blocked == [True], "Destination mutation must serialize with the mapping save"
    # Once the save commits, the native editor can invalidate the destination;
    # the next read must expose Needs attention without choosing a replacement.
    with DbEngine.get_main_engine().begin() as connection:
        connection.execute(invalidation)
    current = read_workflow_mapping(actor, project.id, "github")
    assert not current["enabled"]
    assert current["choices"][0]["status"] == "invalid"
