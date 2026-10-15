# ruff: noqa: F811
"""Explicit card selection uses current authority and preserves unlink evidence."""

import importlib
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.domain.models import (
    Card,
    CardAppOwnership,
    CardAppResourceSelection,
    CardAppResourceSelectionAudit,
)
from langboard_shared.domain.services.AppGovernance import AppGovernanceDenied
from langboard_shared.domain.services.CardAppGovernance import CardAppOwnershipConflict
from langboard_shared.domain.services.CardAppResources import set_card_app_resources
from langboard_shared.domain.services.factory.WorkflowStageService_app_test import board  # noqa: F401
from test_app_connection_resources import scope


def prepare(board):
    connection, definition, binding, rows, _ = scope(board)
    engine = DbEngine.get_main_engine()
    Card.__table__.create(engine)
    CardAppOwnership.__table__.create(engine)
    module = importlib.import_module("langboard.migrations.versions.20261011023000-b48e359672ca")
    original = module.op
    try:
        with engine.begin() as db:
            module.op = Operations(MigrationContext.configure(db))
            module.upgrade()
    finally:
        module.op = original
    with DbSession.atomic() as db:
        card = Card(project_id=board[2].id, project_column_id=board[5][0].id, title="Resource work", visibility="SHARED")
        db.insert(card)
    return connection, definition, binding, rows, card


@pytest.mark.parametrize("board", ["sqlite://"], indirect=True)
def test_replace_noop_cas_revoked_unlink_and_permanent_history(board):
    connection, _, binding, rows, card = prepare(board)
    args = board[1], board[2].id, card.id, connection.id
    uids = [rows[1].get_uid(), rows[0].get_uid()]
    first = set_card_app_resources(*args, uids, None)
    assert first == {"revision": 1, "changed": True, "resource_uids": sorted(uids)}
    assert not set_card_app_resources(*args, list(reversed(uids)), 1)["changed"]
    with pytest.raises(CardAppOwnershipConflict):
        set_card_app_resources(*args, [], None)
    assert set_card_app_resources(*args, [rows[2].get_uid()], 1)["revision"] == 2
    with DbSession.atomic() as db:
        binding.granted_capabilities = []
        db.update(binding)
    with pytest.raises(AppGovernanceDenied):
        set_card_app_resources(*args, [rows[2].get_uid()], 2)
    assert set_card_app_resources(*args, [], 2)["revision"] == 3
    with DbSession.atomic() as db:
        audit = db.exec(SqlBuilder.select.table(CardAppResourceSelectionAudit).order_by(CardAppResourceSelectionAudit.revision)).all()
        assert [a.resource_uids for a in audit] == [sorted(uids), [rows[2].get_uid()], []]
        assert all(a.actor_id == board[1].id for a in audit)
        selection = db.exec(SqlBuilder.select.table(CardAppResourceSelection)).first()
        db.delete(selection)
        db.delete(card)
    module = importlib.import_module("langboard.migrations.versions.20261011023000-b48e359672ca")
    original = module.op
    try:
        with DbEngine.get_main_engine().begin() as db:
            module.op = Operations(MigrationContext.configure(db))
            with pytest.raises(RuntimeError, match="selection history"):
                module.downgrade()
    finally:
        module.op = original


@pytest.mark.parametrize("board", ["sqlite://"], indirect=True)
@pytest.mark.parametrize("gate", ["revoked", "unselected", "foreign", "owner", "private", "disabled", "consent", "type", "disconnected"])
def test_current_selection_gates_leave_no_partial_rows(board, gate):
    connection, definition, binding, rows, card = prepare(board)
    with DbSession.atomic() as db:
        if gate == "owner":
            db.insert(CardAppOwnership(card_id=card.id, app_key="second-app"))
        elif gate == "private":
            card.visibility = "PRIVATE"
            card.owner_user_id = 2
            card.created_by_user_id = 2
            db.update(card)
        elif gate == "disabled":
            definition.is_enabled = False
            db.update(definition)
        elif gate == "consent":
            binding.granted_capabilities = []
            db.update(binding)
        elif gate == "disconnected":
            connection.state = "disconnected"
            db.update(connection)
        else:
            if gate == "revoked":
                rows[1].access_state = "revoked"
            elif gate == "unselected":
                rows[1].is_selected = False
            elif gate == "foreign":
                rows[1].connection_id = connection.id + 1
            elif gate == "type":
                rows[1].resource_type = "undeclared"
            db.update(rows[1])
    with pytest.raises(AppGovernanceDenied):
        set_card_app_resources(board[1], board[2].id, card.id, connection.id, [r.get_uid() for r in rows], None)
    with DbSession.atomic() as db:
        assert not db.exec(SqlBuilder.select.table(CardAppResourceSelection)).all()
        assert not db.exec(SqlBuilder.select.table(CardAppResourceSelectionAudit)).all()


@pytest.mark.parametrize("board", ["sqlite://"], indirect=True)
@pytest.mark.parametrize("gate", ["role", "personal_owner", "shared", "deleted", "foreign_card"])
def test_current_admin_and_connection_authority(board, gate):
    from langboard_shared.core.types import SafeDateTime
    from langboard_shared.domain.models import ProjectAssignedUser

    connection, _, _, rows, card = prepare(board)
    with DbSession.atomic() as db:
        if gate == "role":
            board[2].owner_id = 2
            board[4].actions = ["read"]
            db.update(board[2])
            db.update(board[4])
        elif gate == "personal_owner":
            connection.owner_id = 2
            db.update(connection)
        elif gate == "shared":
            db.insert(ProjectAssignedUser(project_id=board[2].id, user_id=2))
        else:
            if gate == "deleted":
                card.deleted_at = SafeDateTime.now()
            else:
                card.project_id = 11
            db.update(card)
    with pytest.raises(AppGovernanceDenied):
        set_card_app_resources(board[1], board[2].id, card.id, connection.id, [rows[0].get_uid()], None)
    with DbSession.atomic() as db:
        assert not db.exec(SqlBuilder.select.table(CardAppResourceSelectionAudit)).all()


@pytest.mark.parametrize("board", ["sqlite://"], indirect=True)
def test_invalid_bounds_and_audit_failure_are_atomic(board, monkeypatch):
    connection, _, _, rows, card = prepare(board)
    args = board[1], board[2].id, card.id, connection.id
    uid = rows[0].get_uid()
    from types import SimpleNamespace

    with pytest.raises(AppGovernanceDenied):
        set_card_app_resources(SimpleNamespace(id=board[1].id), *args[1:], [uid], None)
    for values in ([uid, uid], ["invalid"], [uid] * 21, "all", [None]):
        with pytest.raises(ValueError):
            set_card_app_resources(*args, values, None)
    for revision in (False, 0, -1, "1"):
        with pytest.raises(ValueError):
            set_card_app_resources(*args, [uid], revision)
    original = DbSession.insert

    def insert(db, row):
        if isinstance(row, CardAppResourceSelectionAudit):
            raise RuntimeError("audit unavailable")
        return original(db, row)

    monkeypatch.setattr(DbSession, "insert", insert)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        set_card_app_resources(*args, [uid], None)
    with DbSession.atomic() as db:
        assert not db.exec(SqlBuilder.select.table(CardAppResourceSelection)).all()


@pytest.mark.parametrize("board", ["sqlite://"], indirect=True)
def test_resource_settings_route_uses_canonical_service_and_current_fences(board):
    from langboard.routes.settings.CardAppResourcesApi import CardAppResourcesForm, read_resources, save_resources
    from fastapi import HTTPException
    from json import loads

    connection, definition, binding, rows, card = prepare(board)
    route = dict(project_uid=board[2].get_uid(), card_uid=card.get_uid(),
                 app_key=connection.app_key, connection_uid=connection.get_uid(), user=board[1])
    snapshot = loads(read_resources(**route).body)
    body = dict(resource_uids=[rows[0].get_uid()], expected_revision=None,
                expected_identity_revision=snapshot['identity_revision'],
                expected_app_revision=snapshot['app_revision'],
                expected_binding_revision=snapshot['binding_revision'])
    with pytest.raises(HTTPException) as denied:
        read_resources(**{**route, 'app_key': 'wrong-app'})
    assert denied.value.status_code == 403
    for key in ('expected_identity_revision', 'expected_app_revision', 'expected_binding_revision'):
        with pytest.raises(HTTPException) as conflict:
            save_resources(**route, form=CardAppResourcesForm(**{**body, key: '0' * 64}))
        assert conflict.value.status_code == 409
    with pytest.raises(HTTPException) as conflict:
        save_resources(**route, form=CardAppResourcesForm(**{**body, 'expected_ownership_revision': 1}))
    assert conflict.value.status_code == 409
    with DbSession.atomic() as db:
        assert not db.exec(SqlBuilder.select.table(CardAppResourceSelection)).all()
        assert not db.exec(SqlBuilder.select.table(CardAppResourceSelectionAudit)).all()
    receipt = loads(save_resources(**route, form=CardAppResourcesForm(**body)).body)
    assert receipt == {'revision': 1, 'changed': True, 'resource_uids': [rows[0].get_uid()]}
    assert loads(read_resources(**route).body)['revision'] == 1
    with DbSession.atomic() as db:
        binding.granted_capabilities = []
        db.update(binding)
    unlink = dict(resource_uids=[], expected_revision=1)
    assert loads(save_resources(**route, form=CardAppResourcesForm(**unlink)).body)['revision'] == 2
