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
