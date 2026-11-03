# ruff: noqa: F811
"""Exercise board resource isolation through the native selection mutation."""

import pytest
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.domain.models import AppResourceBinding, BoardAppBinding, Project
from langboard_shared.domain.services.AppConnectionResources import list_connection_resources
from langboard_shared.domain.services.AppGovernance import AppGovernanceConflict, AppGovernanceDenied
from langboard_shared.domain.services.AppResourceSelection import list_resource_selections, save_resource_selection
from langboard_shared.publishers import AppSettingPublisher
from test_app_connection_resources import scope


@pytest.mark.parametrize("board", ["sqlite-http", "postgresql-test"], indirect=True)
def test_native_selection_withdrawal_preserves_other_board_and_sibling_resource(board, monkeypatch):
    # Publishing settings changes is outside the local persistence assertion.
    monkeypatch.setattr(AppSettingPublisher, "apps_changed", lambda: None)
    actor, project = board[1:3]
    connection, definition, binding, rows, token = scope(board)
    with DbSession.atomic() as db:
        other_project = db.exec(SqlBuilder.select.table(Project).where(Project.id == 11)).first()
        other_project.owner_id = actor.id
        db.update(other_project)
        binding.workflow_mapping = {"active": "first-board-column"}
        db.update(binding)
        other_binding = BoardAppBinding(
            project_id=other_project.id,
            app_key=connection.app_key,
            state="enabled",
            granted_capabilities=["resources.read"],
            workflow_mapping={"review": "second-board-column"},
        )
        db.insert(other_binding)
        other_resource = AppResourceBinding(
            board_binding_id=other_binding.id,
            connection_id=connection.id,
            resource_type=rows[0].resource_type,
            external_resource_id=rows[0].external_resource_id,
            resource_path=[{"name": "second-board-resource"}],
            access_state="granted",
            is_selected=True,
            health="healthy",
        )
        db.insert(other_resource)

    first_before = list_resource_selections(actor, project.id, connection.app_key, connection.id)
    other_before = list_resource_selections(actor, other_project.id, connection.app_key, connection.id)
    other_app_before = list_connection_resources(token, other_project.id)
    other_binding_revision = other_binding.edit_revision()
    original_binding_revision = binding.edit_revision()
    target = rows[0]
    withdrawn = save_resource_selection(
        actor,
        project.id,
        connection.app_key,
        connection.id,
        target.resource_type,
        target.external_resource_id,
        False,
        target.access_revision,
        original_binding_revision,
        definition.edit_revision(),
        first_before["identity_revision"],
    )
    assert withdrawn["resource_uid"] == target.get_uid()
    assert withdrawn["is_selected"] is False
    assert withdrawn["access_state"] == "revoked"
    assert withdrawn["revision"] == target.access_revision + 1
    assert withdrawn["binding_revision"] != original_binding_revision
    first_after = list_resource_selections(actor, project.id, connection.app_key, connection.id)
    assert first_after["items"][1:] == first_before["items"][1:]
    assert list_resource_selections(actor, other_project.id, connection.app_key, connection.id) == other_before
    assert list_connection_resources(token, other_project.id) == other_app_before
    assert {item["resource_uid"] for item in list_connection_resources(token, project.id)["items"]} == {
        row.get_uid() for row in rows[1:]
    }
    with DbSession.atomic() as db:
        current = db.exec(SqlBuilder.select.table(BoardAppBinding).where(BoardAppBinding.id == binding.id)).first()
        other_current = db.exec(
            SqlBuilder.select.table(BoardAppBinding).where(BoardAppBinding.id == other_binding.id)
        ).first()
        assert current.workflow_mapping == {"active": "first-board-column"}
        assert other_current.edit_revision() == other_binding_revision
        other_project.owner_id = 2
        db.update(other_project)
    # Neither another board's current revision nor stale authority can mutate it.
    with pytest.raises(AppGovernanceConflict):
        save_resource_selection(
            actor,
            project.id,
            connection.app_key,
            connection.id,
            rows[1].resource_type,
            rows[1].external_resource_id,
            False,
            rows[1].access_revision,
            other_binding_revision,
            definition.edit_revision(),
            first_before["identity_revision"],
        )
    with pytest.raises(AppGovernanceDenied):
        save_resource_selection(
            actor,
            other_project.id,
            connection.app_key,
            connection.id,
            target.resource_type,
            target.external_resource_id,
            False,
            other_resource.access_revision,
            other_binding_revision,
            definition.edit_revision(),
            other_before["identity_revision"],
        )
    assert list_resource_selections(actor, project.id, connection.app_key, connection.id) == first_after
