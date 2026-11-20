# ruff: noqa: F811
"""Native selection and HTTP authority; only the bounded provider request is replaced."""

from hashlib import sha256
from types import SimpleNamespace
import httpx
import pytest
from langboard.apps import ConnectionReads as reads
from langboard.apps import DevtronConnection as devtron
from langboard.apps.DevtronRead import selected_deployment_arguments
from langboard.apps.MetadataTransport import MetadataUnavailable
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.domain.models import AppConnection, AppGovernancePolicy, AppResourceBinding, BoardAppBinding
from langboard_shared.domain.services.AppConnectionAuthentication import _identity_hash
from langboard_shared.domain.services.AppGovernance import AppGovernanceDenied, _current, _row
from langboard_shared.domain.services.AppRegistry import AppRegistryConflict
from langboard_shared.domain.services.AppResourceSelection import save_resource_selection
from langboard_shared.Env import Env
from langboard_shared.helpers import InfraHelper
from langboard_shared.publishers import AppSettingPublisher
from test_devtron_connection import register, setup  # noqa: F401
from test_devtron_settings_http import http_setup  # noqa: F401


def select_target(setup, external_id="app-17-env-4"):
    service, board, reference, definition, calls, _ = setup
    receipt = register(setup)
    with DbSession.atomic() as db:
        connection = _current(db, AppConnection, InfraHelper.convert_id(receipt["connection_uid"]))
        binding = BoardAppBinding(project_id=board[2].id, app_key=devtron.APP_KEY, state="enabled",
                                  granted_capabilities=["resources.read"])
        db.insert(binding)
        identity = _identity_hash(db, connection, definition=definition)
    selection = save_resource_selection(board[1], board[2].id, devtron.APP_KEY, connection.id, "application",
        external_id, True, 0, binding.edit_revision(), definition.edit_revision(), identity)
    assert selection["access_state"] == "unknown"
    with DbSession.atomic() as db:
        resource = _current(db, AppResourceBinding, InfraHelper.convert_id(selection["resource_uid"]))
        binding = _current(db, BoardAppBinding, binding.id)
    calls.clear()
    return SimpleNamespace(service=service, board=board, reference=reference, definition=definition,
        connection=connection, resource=resource, binding=binding, calls=calls,
        args=dict(project_uid=board[2].get_uid(), app_revision=definition.edit_revision(),
                  binding_revision=binding.edit_revision(), connection_uid=connection.get_uid(),
                  resource_uid=resource.get_uid(), access_revision=resource.access_revision))


@pytest.fixture
def selected(setup, monkeypatch):
    s = select_target(setup)
    s.state = {"after": None, "error": None}
    s.notified = []
    monkeypatch.setattr(AppSettingPublisher, "apps_changed", lambda: s.notified.append(True))

    def read(base, token, operation, arguments, *, timeout):
        assert not DbSession.has_active_transaction()
        assert base == "https://devtron.example.invalid" and token == "fixture-api-token"
        assert operation == "deployments.list" and timeout == 8
        s.calls.append(arguments)
        if s.state["after"]:
            s.state["after"]()
        if s.state["error"]:
            raise s.state["error"]
        return {"cdWorkflows": [], "deploymentHistory": []}

    monkeypatch.setattr(devtron, "read_metadata", read)
    return s


def invoke(s, **overrides):
    return devtron.verify_selected_resource(s.service, s.board[1], **{**s.args, **overrides})


def current_resource(s):
    with DbSession.atomic() as db:
        row = _current(db, AppResourceBinding, s.resource.id)
        return row.access_state, row.health, row.access_revision, row.is_selected


def test_empty_accessible_history_verifies_only_the_existing_selection(selected):
    s = selected
    result = invoke(s)
    assert result == {"ok": True, "resource": {"resource_uid": s.resource.get_uid(),
        "access_state": "granted", "health": "unknown", "access_revision": 2,
        "binding_revision": s.args["binding_revision"]}}
    assert current_resource(s) == ("granted", "unknown", 2, True)
    assert s.calls == [{"appId": 17, "environmentId": 4, "limit": 1}] and s.notified == [True]
    with DbSession.atomic() as db:
        binding = _current(db, BoardAppBinding, s.binding.id)
        assert binding.edit_revision() == s.args["binding_revision"]
        assert binding.granted_capabilities == ["resources.read"] and binding.workflow_mapping == {}
        assert len(db.exec(SqlBuilder.select.table(AppResourceBinding)).all()) == 1


def test_siblings_and_other_boards_are_neither_verified_nor_accepted_as_the_target(selected):
    s = selected
    with DbSession.atomic() as db:
        other_binding = BoardAppBinding(project_id=11, app_key=devtron.APP_KEY, state="enabled",
                                       granted_capabilities=["resources.read"])
        db.insert(other_binding)
        sibling = AppResourceBinding(board_binding_id=s.binding.id, connection_id=s.connection.id,
            resource_type="application", external_resource_id="app-18-env-4", is_selected=True,
            identity_hash=s.resource.identity_hash, access_revision=7)
        other = AppResourceBinding(board_binding_id=other_binding.id, connection_id=s.connection.id,
            resource_type="application", external_resource_id="app-17-env-4", is_selected=True,
            identity_hash=s.resource.identity_hash, access_revision=8)
        db.insert(sibling)
        db.insert(other)
    with pytest.raises(AppGovernanceDenied):
        invoke(s, resource_uid=other.get_uid(), access_revision=8)
    assert s.calls == []
    assert invoke(s)["ok"] is True
    with DbSession.atomic() as db:
        for expected in (sibling, other):
            row = _current(db, AppResourceBinding, expected.id)
            assert (row.access_state, row.health, row.access_revision) == ("unknown", "unknown", expected.access_revision)


@pytest.mark.parametrize("error,access,code", [
    (MetadataUnavailable(status_code=401), "denied", "upstream_error"),
    (MetadataUnavailable(status_code=403), "denied", "upstream_error"),
    (MetadataUnavailable(status_code=404), "denied", "upstream_error"),
    (MetadataUnavailable(status_code=500), "unknown", "upstream_error"),
    (MetadataUnavailable(), "unknown", "upstream_error"),
    (httpx.ConnectError("private provider body"), "unknown", "unavailable"),
])
def test_failed_probe_cannot_preserve_a_cached_grant_or_expose_provider_details(selected, error, access, code):
    s = selected
    with DbSession.atomic() as db:
        row = _current(db, AppResourceBinding, s.resource.id)
        row.access_state = "granted"
        db.update(row)
    s.state["error"] = error
    result = invoke(s)
    assert result["ok"] is False and result["error"]["code"] == code
    assert current_resource(s) == (access, "unavailable", 2, True) and len(s.calls) == 1
    assert "private provider body" not in str(result) and s.reference["uri"] not in str(result)
    assert "fixture-api-token" not in str(result)


@pytest.mark.parametrize("change", ["role", "app", "grant", "connection", "resource", "identity", "revision"])
def test_current_selection_and_management_authority_are_required_before_io(selected, change):
    s = selected
    with DbSession.atomic() as db:
        if change == "role":
            s.board[4].actions = ["read"]
            db.update(s.board[4])
        elif change == "app":
            s.definition.is_enabled = False
            db.update(s.definition)
        elif change == "grant":
            s.binding.granted_capabilities = []
            db.update(s.binding)
        elif change == "connection":
            s.connection.owner_id = s.board[2].owner_id
            db.update(s.connection)
        else:
            row = _current(db, AppResourceBinding, s.resource.id)
            if change == "resource":
                row.is_selected = False
            elif change == "identity":
                row.identity_hash = "stale-identity"
            else:
                row.access_revision += 1
            db.update(row)
    with pytest.raises((AppGovernanceDenied, AppRegistryConflict)):
        invoke(s)
    assert s.calls == [] and s.notified == []


@pytest.mark.parametrize("phase", ["secret", "provider"])
@pytest.mark.parametrize("change", ["selection", "binding", "secret", "policy"])
def test_authority_fences_suppress_late_results_without_restoring_grants(selected, monkeypatch, phase, change):
    s = selected

    def revoke():
        with DbSession.atomic() as db:
            if change == "selection":
                row = _current(db, AppResourceBinding, s.resource.id)
                row.is_selected, row.access_state = False, "revoked"
                row.access_revision += 1
                db.update(row)
            elif change == "binding":
                s.binding.granted_capabilities = []
                db.update(s.binding)
            elif change == "secret":
                secret = s.service.secret_reference._find(s.board[1], s.reference["uri"], lock=True)
                secret.revision += 1
                db.update(secret)
            else:
                policy = _row(db, None)
                if policy is None:
                    db.insert(AppGovernancePolicy(scope_key="global", mode="approved_only", generation=2))
                else:
                    policy.generation += 1
                    db.update(policy)

    if phase == "secret":
        original = s.service.secret_reference.resolve_for_runtime

        def resolve(*args, **kwargs):
            value = original(*args, **kwargs)
            revoke()
            return value

        monkeypatch.setattr(s.service.secret_reference, "resolve_for_runtime", resolve)
    else:
        s.state["after"] = revoke
    with pytest.raises((AppGovernanceDenied, AppRegistryConflict)):
        invoke(s)
    assert len(s.calls) == (phase == "provider") and s.notified == []
    assert current_resource(s) == (("revoked", "unknown", 2, False) if change == "selection"
                                    else ("unknown", "unknown", 1, True))


def test_overlapping_verification_cannot_save_the_same_revision_twice(selected):
    s = selected

    def finish_other_request():
        s.state["after"] = None
        assert invoke(s)["ok"] is True

    s.state["after"] = finish_other_request
    with pytest.raises(AppRegistryConflict):
        invoke(s)
    assert current_resource(s) == ("granted", "unknown", 2, True)
    assert len(s.calls) == 2 and s.notified == [True]


def test_borrowed_transaction_is_rejected_before_any_provider_request(selected):
    with DbSession.atomic(), pytest.raises(RuntimeError, match="must own its transaction"):
        invoke(selected)
    assert selected.calls == []


@pytest.mark.parametrize("external_id,helm_id,expected", [
    ("helm-app-3", None, {"installedAppId": 3, "limit": 1}),
    ("helm-release-" + sha256(b"1|default|release").hexdigest(), "1|default|release",
     {"helmAppId": "1|default|release", "limit": 1}),
])
def test_helm_selection_reuses_its_exact_closed_identity(setup, monkeypatch, external_id, helm_id, expected):
    s = select_target(setup, external_id)
    calls = []

    def read(base, token, operation, arguments, *, timeout):
        assert not DbSession.has_active_transaction()
        calls.append(arguments)
        return {"deploymentHistory": []}

    monkeypatch.setattr(devtron, "read_metadata", read)
    assert invoke(s, helm_app_id=helm_id)["ok"] is True and calls == [expected]


def test_native_scope_rejects_external_helm_substitution_before_secret_or_io(selected):
    with pytest.raises(ValueError):
        invoke(selected, helm_app_id="1|default|release")
    assert selected.calls == [] and current_resource(selected) == ("unknown", "unknown", 1, True)


@pytest.mark.parametrize("invalid", [None, {"access_revision": True}, {"url": "https://other.example.invalid"}])
def test_authenticated_http_verification_is_closed_and_no_store(http_setup, monkeypatch, invalid):
    client, url, _, headers, setup = http_setup
    s = select_target(setup)
    endpoint = f"{url}/{s.connection.get_uid()}/resources/{s.resource.get_uid()}/verify"
    body = {key: s.args[key] for key in ("app_revision", "binding_revision", "access_revision")}
    calls = []

    def read(*args, **kwargs):
        assert not DbSession.has_active_transaction()
        calls.append(True)
        return {"cdWorkflows": []}

    monkeypatch.setattr(devtron, "read_metadata", read)
    refresh = client.cookies.get(Env.REFRESH_TOKEN_NAME)
    assert client.post(endpoint, json=body).status_code == 401
    client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
    result = client.post(endpoint, json={**body, **(invalid or {})}, headers=headers)
    assert result.status_code == (400 if invalid else 200)
    if invalid:
        assert calls == [] and current_resource(s) == ("unknown", "unknown", 1, True)
    else:
        assert result.headers["cache-control"] == "no-store"
        assert result.json()["ok"] is True and calls == [True]
        assert client.post(endpoint, json=body, headers=headers).status_code == 409
        assert calls == [True]


@pytest.mark.parametrize("gate", ["role", "provider"])
def test_http_separates_native_authority_from_safe_provider_rejection(http_setup, monkeypatch, gate):
    client, url, _, headers, setup = http_setup
    s = select_target(setup)
    endpoint = f"{url}/{s.connection.get_uid()}/resources/{s.resource.get_uid()}/verify"
    body = {key: s.args[key] for key in ("app_revision", "binding_revision", "access_revision")}
    calls = []

    def rejected(*args, **kwargs):
        calls.append(True)
        raise MetadataUnavailable(status_code=403)

    monkeypatch.setattr(devtron, "read_metadata", rejected)
    if gate == "role":
        with DbSession.atomic() as db:
            s.board[4].actions = ["read"]
            db.update(s.board[4])
    result = client.post(endpoint, json=body, headers=headers)
    assert result.headers["cache-control"] == "no-store"
    if gate == "role":
        assert result.status_code == 403 and calls == []
        assert current_resource(s) == ("unknown", "unknown", 1, True)
    else:
        assert result.status_code == 200 and calls == [True]
        assert result.json()["ok"] is False
        assert result.json()["error"] == {"code": "upstream_error", "status": 403}
        assert current_resource(s) == ("denied", "unavailable", 2, True)


def test_selection_requires_verification_before_the_registered_panel_read(setup, monkeypatch):
    definition = setup[3]
    with DbSession.atomic() as db:
        definition.declaration = {**definition.declaration, "capabilities": ["panels.render", "resources.read"],
            "panel": {"url": "https://panel.example.invalid", "name": "Devtron", "icon": "app"}}
        db.update(definition)
    s = select_target(setup)
    with DbSession.atomic() as db:
        s.binding.granted_capabilities = ["panels.render", "resources.read"]
        db.update(s.binding)
    s.args["binding_revision"] = s.binding.edit_revision()
    verification_calls, read_calls = [], []

    def verify(*args, **kwargs):
        assert not DbSession.has_active_transaction()
        verification_calls.append(True)
        return {"cdWorkflows": []}

    def execute(base, token, arguments):
        assert not DbSession.has_active_transaction()
        read_calls.append(arguments)
        return {"items": []}

    monkeypatch.setattr(devtron, "read_metadata", verify)
    monkeypatch.setattr(reads, "READERS", {(devtron.APP_KEY, "deployments.list"): (selected_deployment_arguments, execute)})

    def panel_read(revision):
        return reads.read_connection(s.service, s.board[1], s.args["project_uid"], devtron.APP_KEY,
            s.args["app_revision"], s.args["binding_revision"], s.connection.get_uid(), "deployments.list",
            {"resource_uid": s.resource.get_uid(), "access_revision": revision})

    with pytest.raises(AppGovernanceDenied):
        panel_read(1)
    assert read_calls == []
    receipt = invoke(s)
    assert panel_read(receipt["resource"]["access_revision"]) == {"ok": True, "data": {"items": []}}
    assert verification_calls == [True] and read_calls == [{"appId": 17, "environmentId": 4, "limit": 50}]
