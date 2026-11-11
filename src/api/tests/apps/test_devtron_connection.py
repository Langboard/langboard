"""Real host authority and secret references fence provider verification."""

import json
from types import SimpleNamespace
import pytest
from pydantic import SecretStr
from langboard.apps import DevtronConnection as devtron
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.domain.models import AppConnection, AppDefinition
from langboard_shared.domain.services.AppConnectionAuthentication import is_native_inbound_connection
from langboard_shared.domain.services.AppGovernance import AppGovernanceDenied
from langboard_shared.domain.services.AppRegistry import AppRegistryConflict
from langboard_shared.Env import Env


@pytest.fixture
def setup(secrets, monkeypatch):
    secret_service, board, _ = secrets
    service = SimpleNamespace(workflow_stage=board[0], secret_reference=secret_service)
    reference = secret_service.create(board[1], "personal", "me", "devtron/api", SecretStr("fixture-api-token"))
    with DbSession.atomic() as db:
        board[4].actions = ["read", "update"]
        db.update(board[4])
        definition = AppDefinition(
            key=devtron.APP_KEY, approved_by=board[1].id,
            declaration={
                "schema_version": 1, "key": devtron.APP_KEY, "version": "1.0.0", "name": "Devtron",
                "description": "Provider connection fixture", "capabilities": ["resources.read"],
                "resource_types": ["application"],
            },
        )
        db.insert(definition)
    original = Env.get_from_env
    monkeypatch.setattr(
        Env, "get_from_env", lambda key, default=None: "https://devtron.example.invalid"
        if key == "APP_CONNECTION_ALLOWED_BASE_URLS" else original(key, default)
    )
    calls, state = [], {"after": None}

    def read(base, token, operation, arguments):
        assert not DbSession.has_active_transaction()
        assert base == "https://devtron.example.invalid" and token == "fixture-api-token"
        if state.get("scope") is None:
            assert operation == "applications.list" and arguments == {"size": 1}
        else:
            assert operation == "deployments.list" and arguments == {**state["scope"], "limit": 1}
        calls.append(operation)
        if state["after"]:
            state["after"]()
        return {"appContainers": [], "appCount": 0}

    monkeypatch.setattr(devtron, "read_metadata", read)
    return service, board, reference, definition, calls, state


def register(setup, *, app_revision=None, deployment_scope=None):
    service, board, reference, definition, *_ = setup
    return devtron.register_connection(
        service, board[1], board[2].get_uid(), app_revision or definition.edit_revision(),
        "https://devtron.example.invalid", reference["uri"],
        deployment_scope=deployment_scope,
    )


def test_registration_rejects_a_borrowed_transaction_before_provider_io(setup):
    calls = setup[4]
    with DbSession.atomic() as db:
        with pytest.raises(RuntimeError, match="must own its transaction"):
            register(setup)
        assert calls == []
        assert db.exec(SqlBuilder.select.table(AppConnection)).all() == []


def test_verified_account_reuses_receipt_and_preserves_inbound_principal(setup):
    _, board, _, _, calls, _ = setup
    with DbSession.atomic() as db:
        inbound = AppConnection(app_key=devtron.APP_KEY, owner_id=board[1].id, state="connected")
        db.insert(inbound)
    first = register(setup)
    second = register(setup)
    assert first == second and len(calls) == 2
    assert first["connection_uid"] != inbound.get_uid()
    assert "fixture-api-token" not in json.dumps(first) and "secret://" not in json.dumps(first)
    with DbSession.atomic() as db:
        rows = db.exec(SqlBuilder.select.table(AppConnection)).all()
        assert len(rows) == 2
        saved = next(row for row in rows if row.id == inbound.id)
        assert is_native_inbound_connection(saved)


@pytest.mark.parametrize("scope", [
    {"appId": 17, "environmentId": 4},
    {"installedAppId": 3},
    {"helmAppId": "1|default|release"},
])
def test_explicit_target_can_verify_without_native_application_inventory_permission(setup, scope):
    _, _, _, _, calls, state = setup
    state["scope"] = scope
    receipt = register(setup, deployment_scope=scope)
    assert receipt["state"] == "connected" and calls == ["deployments.list"]


@pytest.mark.parametrize("failure", ["stale", "disabled", "role", "inflight-app", "inflight-role", "inflight-secret"])
def test_current_authority_and_secret_revision_prevent_persistence(setup, failure):
    service, board, reference, definition, calls, state = setup
    revision = definition.edit_revision()

    def revoke():
        with DbSession.atomic() as db:
            if failure in {"disabled", "inflight-app"}:
                definition.is_enabled = False
                db.update(definition)
            elif failure in {"role", "inflight-role"}:
                board[4].actions = ["read"]
                db.update(board[4])
            else:
                secret = service.secret_reference._find(board[1], reference["uri"], lock=True)
                secret.revision += 1
                db.update(secret)

    if failure.startswith("inflight-"):
        state["after"] = revoke
    elif failure != "stale":
        revoke()
    with pytest.raises((AppGovernanceDenied, AppRegistryConflict)):
        register(setup, app_revision="0" * 64 if failure == "stale" else revision)
    assert len(calls) == (1 if failure.startswith("inflight-") else 0)
    with DbSession.atomic() as db:
        assert db.exec(SqlBuilder.select.table(AppConnection)).all() == []


def test_discovery_returns_only_bounded_metadata_without_granting_resources(setup, monkeypatch):
    service, board, reference, definition, *_ = setup
    receipt = register(setup)
    calls = []

    def read(base, token, operation, arguments):
        assert not DbSession.has_active_transaction()
        assert operation == "applications.list" and arguments == {"search": "demo", "offset": 0, "size": 50}
        calls.append(operation)
        return {"appContainers": [{"appId": 17, "appName": "demo", "token": "do-not-forward",
                                  "environments": [{"environmentId": 4, "environmentName": "dev"}]}]}

    monkeypatch.setattr(devtron, "read_metadata", read)
    result = devtron.discover_applications(service, board[1], board[2].get_uid(), definition.edit_revision(),
                                          receipt["connection_uid"], search="demo")
    assert result == {"connection_uid": receipt["connection_uid"], "connection_revision": receipt["revision"],
                      "resources": [{"appId": 17, "appName": "demo", "environmentId": 4, "environmentName": "dev"}]}
    assert calls == ["applications.list"]
    assert "do-not-forward" not in json.dumps(result) and reference["uri"] not in json.dumps(result)
    from langboard_shared.domain.models import AppResourceBinding
    with DbSession.atomic() as db:
        assert db.exec(SqlBuilder.select.table(AppResourceBinding)).all() == []


@pytest.mark.parametrize("change", ["connection", "secret", "app", "role"])
def test_discovery_rejects_authority_changes_during_provider_io(setup, monkeypatch, change):
    service, board, reference, definition, *_ = setup
    receipt = register(setup)

    def read(*_args):
        assert not DbSession.has_active_transaction()
        with DbSession.atomic() as db:
            if change == "connection":
                connection = db.exec(SqlBuilder.select.table(AppConnection)).first()
                connection.state = "disconnected"
                db.update(connection)
            elif change == "secret":
                secret = service.secret_reference._find(board[1], reference["uri"], lock=True)
                secret.revision += 1
                db.update(secret)
            elif change == "app":
                definition.is_enabled = False
                db.update(definition)
            else:
                board[4].actions = ["read"]
                db.update(board[4])
        return {"appContainers": []}

    monkeypatch.setattr(devtron, "read_metadata", read)
    with pytest.raises((AppGovernanceDenied, AppRegistryConflict)):
        devtron.discover_applications(service, board[1], board[2].get_uid(), definition.edit_revision(), receipt["connection_uid"])


@pytest.mark.parametrize("invalid", [
    {"appId": True, "appName": "demo", "environments": []},
    {"appId": 1, "appName": "demo", "environments": {}},
    {"appId": 1, "appName": "demo", "environments": [{"environmentId": False, "environmentName": "dev"}]},
    {"appId": 1, "appName": "demo", "environments": [{"environmentId": 2, "environmentName": "dev"}] * 251},
])
def test_discovery_rejects_malformed_or_oversized_inventory(setup, monkeypatch, invalid):
    service, board, _, definition, *_ = setup
    receipt = register(setup)
    monkeypatch.setattr(devtron, "read_metadata", lambda *_args: {"appContainers": [invalid]})
    with pytest.raises(ValueError):
        devtron.discover_applications(service, board[1], board[2].get_uid(), definition.edit_revision(), receipt["connection_uid"])


def test_discovery_rejects_borrowed_transaction_before_provider_io(setup):
    service, board, _, definition, *_ = setup
    receipt = register(setup)
    with DbSession.atomic(), pytest.raises(RuntimeError, match="must own its transaction"):
        devtron.discover_applications(service, board[1], board[2].get_uid(), definition.edit_revision(), receipt["connection_uid"])


@pytest.mark.parametrize("denial", ["foreign-owner", "undeclared-read"])
def test_discovery_denies_foreign_accounts_and_missing_declared_read_before_provider_io(setup, monkeypatch, denial):
    service, board, _, definition, *_ = setup
    receipt = register(setup)
    with DbSession.atomic() as db:
        if denial == "foreign-owner":
            connection = db.exec(SqlBuilder.select.table(AppConnection)).first()
            connection.owner_id = board[2].owner_id
            db.update(connection)
        else:
            definition.declaration = {**definition.declaration, "capabilities": []}
            db.update(definition)

    def forbidden(*_args):
        raise AssertionError("Denied discovery must not contact the provider")

    monkeypatch.setattr(devtron, "read_metadata", forbidden)
    with pytest.raises(AppGovernanceDenied):
        devtron.discover_applications(service, board[1], board[2].get_uid(), definition.edit_revision(), receipt["connection_uid"])


def test_discovery_bounds_the_combined_app_environment_inventory(setup, monkeypatch):
    service, board, _, definition, *_ = setup
    receipt = register(setup)
    rows = [{"appId": app_id, "appName": "app", "environments": [
        {"environmentId": environment_id, "environmentName": "dev"} for environment_id in range(1, 127)
    ]} for app_id in (1, 2)]
    monkeypatch.setattr(devtron, "read_metadata", lambda *_args: {"appContainers": rows})
    with pytest.raises(ValueError, match="page is too large"):
        devtron.discover_applications(service, board[1], board[2].get_uid(), definition.edit_revision(), receipt["connection_uid"])
