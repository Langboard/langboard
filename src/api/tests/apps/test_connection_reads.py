"""Native current-authority fencing; only provider I/O is replaced."""

import importlib
from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langboard.apps import ConnectionReads as reads
from langboard.apps.DevtronRead import selected_deployment_arguments
from langboard.apps.MetadataTransport import MetadataUnavailable
from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
from langboard.middlewares.RoleMiddleware import RoleMiddleware
from langboard.routes.board.AppConnectionReadApi import read_panel_connection
from langboard_shared.core.caching import Cache
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.routing import AppRouter
from langboard_shared.core.security import AuthSecurity
from langboard_shared.domain.models import AppConnection, AppDefinition, AppResourceBinding, BoardAppBinding
from langboard_shared.domain.services.AppConnectionAuthentication import _identity_hash
from langboard_shared.domain.services.AppGovernance import AppGovernanceConflict, AppGovernanceDenied
from langboard_shared.Env import Env
from pydantic import SecretStr


@pytest.fixture
def read_scope(secrets, monkeypatch):
    secret_service, board, _ = secrets
    reference = secret_service.create(board[1], "personal", "me", "read/api", SecretStr("fixture-api-token"))
    service = SimpleNamespace(workflow_stage=board[0], secret_reference=secret_service, close=lambda: None)
    with DbSession.atomic() as db:
        definition = AppDefinition(key="devtron-panel", approved_by=board[1].id, declaration={
            "schema_version": 1, "key": "devtron-panel", "version": "1.0.0", "name": "Devtron",
            "description": "Read fixture", "capabilities": ["panels.render", "resources.read"],
            "resource_types": ["application"], "panel": {"url": "https://panel.example.invalid", "name": "Devtron"},
        })
        db.insert(definition)
        binding = BoardAppBinding(project_id=board[2].id, app_key=definition.key, state="enabled",
                                  granted_capabilities=["panels.render", "resources.read"])
        db.insert(binding)
        connection = AppConnection(app_key=definition.key, owner_id=board[1].id, state="connected",
                                   instance_url="https://devtron.example.invalid", credential_reference=reference["uri"])
        db.insert(connection)
        resource = AppResourceBinding(board_binding_id=binding.id, connection_id=connection.id,
            resource_type="application", external_resource_id="app-17-env-4", is_selected=True,
            access_state="granted", access_revision=1, health="healthy",
            identity_hash=_identity_hash(db, connection, definition=definition))
        db.insert(resource)
    original = Env.get_from_env
    monkeypatch.setattr(Env, "get_from_env", lambda key, default=None: "https://devtron.example.invalid"
                        if key == "APP_CONNECTION_ALLOWED_BASE_URLS" else original(key, default))
    calls, state = [], {"after": None, "result": {"items": [{"id": 9}]}}

    def execute(base, token, arguments):
        assert not DbSession.has_active_transaction()
        assert base == "https://devtron.example.invalid" and token == "fixture-api-token"
        assert arguments == {"appId": 17, "environmentId": 4, "limit": 50}
        calls.append(arguments)
        if state["after"]:
            state["after"]()
        return state["result"]

    monkeypatch.setattr(reads, "READERS", {(definition.key, "deployments.list"): (selected_deployment_arguments, execute)})
    return SimpleNamespace(service=service, board=board, definition=definition, binding=binding,
                           connection=connection, resource=resource, reference=reference, calls=calls, state=state)


def invoke(scope, **overrides):
    values = dict(service=scope.service, actor=scope.board[1], project_uid=scope.board[2].get_uid(),
        app_key=scope.definition.key, app_revision=scope.definition.edit_revision(),
        binding_revision=scope.binding.edit_revision(), connection_uid=scope.connection.get_uid(),
        read_key="deployments.list", inputs={"resource_uid": scope.resource.get_uid(), "access_revision": 1})
    return reads.read_connection(**{**values, **overrides})


def test_verified_read_releases_transactions_and_does_not_grant_or_update_health(read_scope):
    s = read_scope
    before = (s.resource.access_revision, s.resource.health, s.binding.edit_revision())
    assert invoke(s) == {"ok": True, "data": {"items": [{"id": 9}]}}
    assert len(s.calls) == 1
    assert (s.resource.access_revision, s.resource.health, s.binding.edit_revision()) == before


@pytest.mark.parametrize("failure", ["unknown", "revoked", "unselected", "foreign-app", "foreign-owner", "grant", "role"])
def test_denied_current_scope_never_calls_provider(read_scope, failure):
    s = read_scope
    with DbSession.atomic() as db:
        if failure in {"unknown", "revoked"}:
            s.resource.access_state = failure
            db.update(s.resource)
        elif failure == "unselected":
            s.resource.is_selected = False
            db.update(s.resource)
        elif failure in {"foreign-app", "foreign-owner"}:
            if failure == "foreign-app":
                s.connection.app_key = "other-app"
            else:
                s.connection.owner_id = 2
            db.update(s.connection)
        elif failure == "grant":
            s.binding.granted_capabilities = ["panels.render"]
            db.update(s.binding)
        else:
            s.board[4].actions = []
            db.update(s.board[4])
    with pytest.raises(AppGovernanceDenied):
        invoke(s)
    assert s.calls == []


@pytest.mark.parametrize("failure", ["resource", "connection", "app", "grant", "role", "secret"])
def test_inflight_authority_change_suppresses_provider_result(read_scope, failure):
    s = read_scope

    def revoke():
        with DbSession.atomic() as db:
            if failure == "resource":
                s.resource.access_revision += 1
                db.update(s.resource)
            elif failure == "connection":
                s.connection.state = "disconnected"
                db.update(s.connection)
            elif failure == "app":
                s.definition.is_enabled = False
                db.update(s.definition)
            elif failure == "grant":
                s.binding.granted_capabilities = []
                db.update(s.binding)
            elif failure == "role":
                s.board[4].actions = []
                db.update(s.board[4])
            else:
                secret = s.service.secret_reference._find(s.board[1], s.reference["uri"], lock=True)
                secret.revision += 1
                db.update(secret)

    s.state["after"] = revoke
    with pytest.raises((AppGovernanceDenied, AppGovernanceConflict)):
        invoke(s)
    assert len(s.calls) == 1


def test_revocation_during_secret_resolution_prevents_provider_dispatch(read_scope, monkeypatch):
    s = read_scope
    original = s.service.secret_reference.resolve_for_runtime

    def resolve(*args, **kwargs):
        value = original(*args, **kwargs)
        with DbSession.atomic() as db:
            s.binding.granted_capabilities = []
            db.update(s.binding)
        return value

    monkeypatch.setattr(s.service.secret_reference, "resolve_for_runtime", resolve)
    with pytest.raises((AppGovernanceDenied, AppGovernanceConflict)):
        invoke(s)
    assert s.calls == []


@pytest.mark.parametrize("revision", [0, True])
def test_stale_or_noninteger_revision_never_dispatches(read_scope, revision):
    s = read_scope
    with pytest.raises((AppGovernanceConflict, ValueError)):
        invoke(s, inputs={"resource_uid": s.resource.get_uid(), "access_revision": revision})
    assert s.calls == []


@pytest.mark.parametrize("value", ["fixture-api-token", "reference"])
def test_adapter_cannot_return_credential_or_secret_reference(read_scope, value):
    s = read_scope
    s.state["result"] = {"items": [s.reference["uri"] if value == "reference" else value]}
    with pytest.raises(MetadataUnavailable):
        invoke(s)


def test_borrowed_transaction_rejected_before_dispatch(read_scope):
    with DbSession.atomic():
        with pytest.raises(RuntimeError, match="must own their transaction"):
            invoke(read_scope)
    assert read_scope.calls == []


def test_authenticated_http_current_scope_and_revocation(read_scope, monkeypatch):
    s = read_scope
    monkeypatch.setattr(DbEngine, "get_readonly_engine", DbEngine.get_main_engine)
    monkeypatch.setattr(Cache, "get", lambda *a, **k: None)
    monkeypatch.setattr(Cache, "set", lambda *a, **k: None)
    monkeypatch.setattr(importlib.import_module("langboard.middlewares.ApiAuthMiddleware"), "DomainService", lambda: s.service)
    app = FastAPI()
    app.include_router(AppRouter.api)
    for route in app.routes:
        if getattr(route, "endpoint", None) is read_panel_connection:
            for dependency in route.dependant.dependencies:
                if dependency.name == "service":
                    app.dependency_overrides[dependency.call] = lambda: s.service
    app.add_middleware(RoleMiddleware, routes=AppRouter.api.routes)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    access, refresh = AuthSecurity.authenticate(s.board[1].id)
    url = f"/board/{s.board[2].get_uid()}/apps/devtron-panel/panel/connections/read"
    body = {"app_revision": s.definition.edit_revision(), "binding_revision": s.binding.edit_revision(),
            "connection_uid": s.connection.get_uid(), "read_key": "deployments.list",
            "input": {"resource_uid": s.resource.get_uid(), "access_revision": 1}}
    with TestClient(app) as client:
        assert client.post(url, json=body).status_code == 401
        client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
        response = client.post(url, json=body, headers={"Authorization": f"Bearer {access}"})
        assert response.status_code == 200 and response.json()["ok"] is True
        assert response.headers["cache-control"] == "no-store"
        with DbSession.atomic() as db:
            s.resource.access_state = "unknown"
            db.update(s.resource)
        denied = client.post(url, json=body, headers={"Authorization": f"Bearer {access}"})
        assert denied.status_code == 403 and denied.json() == {"ok": False, "error": {"code": "forbidden"}}
        assert len(s.calls) == 1
