"""Inbound consent cannot replace provider verification; both native databases execute."""

from types import SimpleNamespace
import pytest
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.types import SnowflakeID
from langboard_shared.domain.models import AppConnection, AppDefinition, AppResourceBinding, BoardAppBinding
from langboard_shared.domain.services.AppConnectionManagement import create_inbound_connection, select_inbound_resource
from langboard_shared.domain.services.AppGovernance import AppGovernanceDenied


@pytest.fixture
def consent_scope(board):
    service, actor, project, _, role, *_ = board
    with DbSession.atomic() as db:
        role.actions = ["read", "update"]
        db.update(role)
        definition = AppDefinition(key="example-boundary", approved_by=actor.id, declaration={
            "schema_version": 1, "key": "example-boundary", "version": "1.0.0", "name": "Boundary",
            "description": "Inbound consent boundary fixture", "capabilities": ["resources.read"],
            "resource_types": ["application"],
        })
        db.insert(definition)
        binding = BoardAppBinding(project_id=project.id, app_key=definition.key,
                                  state="enabled", granted_capabilities=["resources.read"])
        db.insert(binding)
    receipt = create_inbound_connection(actor, definition.key, definition.edit_revision())
    return SimpleNamespace(service=service, actor=actor, project=project, definition=definition, binding=binding,
                           identifier=SnowflakeID.from_short_code(receipt["connection_uid"]))


def consent_args(scope):
    return (scope.service, scope.actor, scope.project.get_uid(), scope.definition.key, scope.definition.edit_revision(),
            scope.binding.get_uid(), scope.binding.edit_revision(), scope.identifier, "application", "app-17-env-4")


@pytest.mark.parametrize("field,value", [
    ("credential_reference", "secret://ref/00000000001"),
    ("instance_url", "https://provider.example.invalid"),
    ("external_account_id", "provider-account"),
])
def test_inbound_resource_consent_cannot_grant_provider_connection(consent_scope, field, value):
    s = consent_scope
    with DbSession.atomic() as db:
        connection = db.exec(SqlBuilder.select.table(AppConnection).where(AppConnection.id == s.identifier)).first()
        setattr(connection, field, value)
        db.update(connection)
    with pytest.raises(AppGovernanceDenied):
        select_inbound_resource(*consent_args(s))
    with DbSession.atomic() as db:
        assert db.exec(SqlBuilder.select.table(AppResourceBinding).where(
            AppResourceBinding.connection_id == s.identifier)).all() == []


def test_changed_provider_connection_keeps_existing_inbound_resource_withdrawable(consent_scope):
    s = consent_scope
    selected = select_inbound_resource(*consent_args(s))
    with DbSession.atomic() as db:
        connection = db.exec(SqlBuilder.select.table(AppConnection).where(AppConnection.id == s.identifier)).first()
        connection.credential_reference = "secret://ref/00000000001"
        db.update(connection)
    withdrawn = select_inbound_resource(*consent_args(s), selected=False,
                                       expected_access_revision=selected["access_revision"])
    assert withdrawn["resource_uid"] == selected["resource_uid"]
    assert withdrawn["access_state"] == "revoked" and not withdrawn["selected"]
    assert withdrawn["access_revision"] == selected["access_revision"] + 1
