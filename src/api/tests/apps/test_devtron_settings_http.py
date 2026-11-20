# ruff: noqa: F811
"""Real JWT setup routes reuse native authority; only provider I/O is replaced."""

import importlib
import json
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langboard.apps import DevtronConnection as devtron
from langboard.apps.MetadataTransport import MetadataUnavailable
from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
from langboard.middlewares.RoleMiddleware import RoleMiddleware
from langboard.routes.board.BoardDevtronAppApi import (
    create_devtron_connection,
    discover_devtron_applications,
    get_devtron_secret_input,
    request_devtron_secret_input,
    verify_devtron_resource,
)
from langboard_shared.core.caching import Cache
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.routing import AppRouter
from langboard_shared.core.security import AuthSecurity
from langboard_shared.domain.models import AppConnection, AppResourceBinding
from langboard_shared.Env import Env
from test_devtron_connection import setup  # noqa: F401 - native shared fixture


@pytest.fixture
def http_setup(setup, monkeypatch):
    service, board, reference, definition, calls, state = setup
    service.close = lambda: None
    monkeypatch.setattr(DbEngine, "get_readonly_engine", DbEngine.get_main_engine)
    monkeypatch.setattr(Cache, "get", lambda *a, **k: None)
    monkeypatch.setattr(Cache, "set", lambda *a, **k: None)
    monkeypatch.setattr(importlib.import_module("langboard.middlewares.ApiAuthMiddleware"), "DomainService", lambda: service)
    app = FastAPI()
    app.include_router(AppRouter.api)
    for route in app.routes:
        if getattr(route, "endpoint", None) in (
            create_devtron_connection, discover_devtron_applications, verify_devtron_resource,
            request_devtron_secret_input, get_devtron_secret_input,
        ):
            for dependency in route.dependant.dependencies:
                if dependency.name == "service":
                    app.dependency_overrides[dependency.call] = lambda: service
    app.add_middleware(RoleMiddleware, routes=AppRouter.api.routes)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    access, refresh = AuthSecurity.authenticate(board[1].id)
    with TestClient(app) as client:
        url = f"/board/{board[2].get_uid()}/settings/apps/devtron-panel/connections"
        body = {"app_revision": definition.edit_revision(), "instance_url": "https://devtron.example.invalid",
                "credential_reference": reference["uri"]}
        assert client.post(url, json=body).status_code == 401
        client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
        yield client, url, body, {"Authorization": f"Bearer {access}"}, setup


@pytest.mark.parametrize("scope", [None, {"appId": 17, "environmentId": 4},
                                   {"installedAppId": 3}, {"helmAppId": "1|default|release"}])
def test_registration_http_reuses_verified_account_without_resource_grants(http_setup, scope):
    client, url, body, headers, setup = http_setup
    setup[5]["scope"] = scope
    body = {**body, "deployment_scope": scope}
    first = client.post(url, json=body, headers=headers)
    second = client.post(url, json=body, headers=headers)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json() and first.json()["state"] == "connected"
    assert first.headers["cache-control"] == "no-store" and len(setup[4]) == 2
    assert setup[2]["uri"] not in first.text and "fixture-api-token" not in first.text
    with DbSession.atomic() as db:
        assert len(db.exec(SqlBuilder.select.table(AppConnection)).all()) == 1
        assert db.exec(SqlBuilder.select.table(AppResourceBinding)).all() == []


def test_http_discovery_is_bounded_and_has_no_selection_effect(http_setup, monkeypatch):
    client, url, body, headers, setup = http_setup
    receipt = client.post(url, json=body, headers=headers).json()
    calls = []

    def read(base, token, operation, arguments):
        assert not DbSession.has_active_transaction()
        assert arguments == {"search": "demo", "offset": 0, "size": 1}
        calls.append(operation)
        return {"appContainers": [{"appId": 17, "appName": "demo", "private": "discard",
            "environments": [{"environmentId": 4, "environmentName": "dev"}]}]}

    monkeypatch.setattr(devtron, "read_metadata", read)
    discover = f"{url}/{receipt['connection_uid']}/discover"
    request = {"app_revision": body["app_revision"], "search": "demo", "size": 1}
    result = client.post(discover, json=request, headers=headers)
    assert result.status_code == 200 and result.headers["cache-control"] == "no-store"
    assert result.json()["resources"] == [{"appId": 17, "appName": "demo", "environmentId": 4, "environmentName": "dev"}]
    assert calls == ["applications.list"] and "discard" not in result.text
    invalid = client.post(discover, json={**request, "size": True}, headers=headers)
    assert invalid.status_code == 400 and len(calls) == 1
    fields = {field for error in invalid.json()["errors"].values() for field in error.get("body", [])}
    assert "size" in fields
    with DbSession.atomic() as db:
        assert db.exec(SqlBuilder.select.table(AppResourceBinding)).all() == []


@pytest.mark.parametrize("gate", ["stale", "role", "organization", "scope", "address", "extra", "boolean"])
def test_invalid_or_unauthorized_setup_never_calls_provider(http_setup, gate):
    client, url, body, headers, setup = http_setup
    expected = 400
    if gate == "stale":
        body = {**body, "app_revision": "0" * 64}
        expected = 409
    elif gate == "role":
        with DbSession.atomic() as db:
            setup[1][4].actions = ["read"]
            db.update(setup[1][4])
        expected = 403
    elif gate == "organization":
        body = {**body, "organization_uid": setup[1][2].get_uid()}
        expected = 403
    elif gate == "scope":
        body = {**body, "deployment_scope": {"appId": 17, "environmentId": 4, "installedAppId": 3}}
    elif gate == "address":
        body = {**body, "instance_url": "https://unapproved.example.invalid"}
    elif gate == "boolean":
        body = {**body, "deployment_scope": {"installedAppId": True}}
        expected = 400
    else:
        body = {**body, "token": "not-a-secret"}
        expected = 400
    result = client.post(url, json=body, headers=headers)
    assert result.status_code == expected and setup[4] == []
    if gate in {"extra", "boolean"}:
        fields = {field for error in result.json()["errors"].values() for field in error.get("body", [])}
        assert ("token" if gate == "extra" else "deployment_scope") in fields
    with DbSession.atomic() as db:
        assert db.exec(SqlBuilder.select.table(AppConnection)).all() == []


def test_provider_rejection_is_safe_and_never_creates_an_account(http_setup, monkeypatch):
    client, url, body, headers, setup = http_setup
    calls = []

    def rejected(*args):
        calls.append(True)
        raise MetadataUnavailable(status_code=403)

    monkeypatch.setattr(devtron, "read_metadata", rejected)
    result = client.post(url, json=body, headers=headers)
    assert result.status_code == 200
    assert result.json() == {"ok": False, "error": {"code": "upstream_error", "status": 403}}
    assert len(calls) == 1 and "fixture-api-token" not in json.dumps(result.json())
    with DbSession.atomic() as db:
        assert db.exec(SqlBuilder.select.table(AppConnection)).all() == []


@pytest.mark.parametrize("echo", ["fixture-api-token", "reference"])
def test_discovery_does_not_echo_resolved_secret_in_resource_labels(http_setup, monkeypatch, echo):
    client, url, body, headers, setup = http_setup
    receipt = client.post(url, json=body, headers=headers).json()
    secret = setup[2]["uri"] if echo == "reference" else echo
    monkeypatch.setattr(devtron, "read_metadata", lambda *a: {
        "appContainers": [{"appId": 17, "appName": secret,
                           "environments": [{"environmentId": 4, "environmentName": "dev"}]}]})
    result = client.post(f"{url}/{receipt['connection_uid']}/discover",
                         json={"app_revision": body["app_revision"]}, headers=headers)
    assert result.status_code == 200
    assert result.json() == {"ok": False, "error": {"code": "upstream_error"}}
    assert secret not in result.text


@pytest.mark.parametrize("setup", ['fixture-"api\\token'], indirect=True, ids=["escaped-credential"])
def test_discovery_rejects_json_escaped_credentials_inside_resource_labels(http_setup, monkeypatch):
    client, url, body, headers, _ = http_setup
    receipt = client.post(url, json=body, headers=headers).json()

    def echo(base, token, operation, arguments):
        return {"appContainers": [{"appId": 17, "appName": "prefix-" + token + "-suffix",
            "environments": [{"environmentId": 4, "environmentName": "dev"}]}]}

    monkeypatch.setattr(devtron, "read_metadata", echo)
    result = client.post(f"{url}/{receipt['connection_uid']}/discover",
                         json={"app_revision": body["app_revision"]}, headers=headers)
    assert result.status_code == 200
    assert result.json() == {"ok": False, "error": {"code": "upstream_error"}}
