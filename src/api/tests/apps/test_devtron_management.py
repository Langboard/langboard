# ruff: noqa: F811
"""Current operator readback/revocation and safe secret input reuse; no provider I/O."""

import pytest
from langboard.apps import DevtronConnection as devtron
from langboard.secrets import SecretInput
from langboard_shared.core.caching import Cache
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.domain.models import (
    AppConnection,
    AppGovernancePolicy,
    AppResourceBinding,
    BoardAppBinding,
    Organization,
    User,
)
from langboard_shared.domain.services.AppGovernance import _current
from langboard_shared.Env import Env
from langboard_shared.publishers import AppSettingPublisher
from test_devtron_connection import register, setup  # noqa: F401
from test_devtron_resource_verification import select_target
from test_devtron_settings_http import http_setup  # noqa: F401


def test_metadata_lists_only_owned_external_accounts_and_supports_exact_receipt_lookup(http_setup):
    client, url, _, headers, setup = http_setup
    own = register(setup)
    with DbSession.atomic() as db:
        foreign = AppConnection(app_key=devtron.APP_KEY, owner_id=setup[1][2].owner_id,
            credential_reference=setup[2]["uri"], instance_url="https://private.example.invalid", state="connected")
        inbound = AppConnection(app_key=devtron.APP_KEY, owner_id=setup[1][1].id, state="connected")
        db.insert(foreign)
        db.insert(inbound)
    calls = len(setup[4])
    result = client.get(url, headers=headers)
    assert result.status_code == 200 and result.headers["cache-control"] == "no-store"
    assert result.json()["items"] == [own] and result.json()["ownership_scopes"] == [None]
    assert "secret://" not in result.text and "fixture-api-token" not in result.text
    assert "private.example.invalid" not in result.text and len(setup[4]) == calls
    for row in (foreign, inbound):
        assert client.get(url, params={"connection_uid": row.get_uid()}, headers=headers).json()["items"] == []
    assert client.get(url, params={"connection_uid": own["connection_uid"]}, headers=headers).json()["items"] == [own]
    assert client.get(url, params={"connection_uid": own["connection_uid"], "after": own["connection_uid"]}, headers=headers).status_code == 400


def test_metadata_pagination_does_not_skip_or_repeat_receipts(http_setup):
    client, url, _, headers, setup = http_setup
    first = register(setup)
    with DbSession.atomic() as db:
        extra = AppConnection(app_key=devtron.APP_KEY, owner_id=setup[1][1].id,
            credential_reference=setup[2]["uri"], instance_url="https://second.example.invalid", state="disconnected")
        db.insert(extra)
    page = client.get(url, params={"limit": 1}, headers=headers).json()
    assert [row["connection_uid"] for row in page["items"]] == [first["connection_uid"]]
    next_page = client.get(url, params={"after": page["next_cursor"], "limit": 1}, headers=headers).json()
    assert [row["connection_uid"] for row in next_page["items"]] == [extra.get_uid()]
    assert next_page["next_cursor"] is None


def test_organization_receipts_require_current_management_and_matching_board(http_setup):
    client, url, _, headers, setup = http_setup
    first = register(setup)
    with DbSession.atomic() as db:
        org = Organization(name="Fixture", slug="devtron-management", owner_user_id=setup[1][1].id)
        db.insert(org)
        connection = db.exec(SqlBuilder.select.table(AppConnection)).first()
        connection.organization_id, connection.ownership = org.id, "organization"
        setup[1][2].organization_id = org.id
        db.update(connection)
        db.update(setup[1][2])
    personal = client.get(url, headers=headers).json()
    assert personal["items"] == [] and personal["ownership_scopes"] == [None, org.get_uid()]
    result = client.get(url, params={"organization_uid": org.get_uid()}, headers=headers)
    assert result.status_code == 200 and result.json()["items"][0]["connection_uid"] == first["connection_uid"]
    with DbSession.atomic() as db:
        org.owner_user_id = setup[1][2].owner_id
        db.update(org)
    assert client.get(url, params={"organization_uid": org.get_uid()}, headers=headers).status_code == 403
    assert client.get(url, headers=headers).json()["ownership_scopes"] == [None]


def test_disabled_policy_allows_explicit_disconnect_and_revokes_all_board_grants(http_setup, monkeypatch):
    client, url, _, headers, setup = http_setup
    s = select_target(setup)
    notified = []
    monkeypatch.setattr(AppSettingPublisher, "apps_changed", lambda: notified.append(True))
    with DbSession.atomic() as db:
        row = _current(db, AppResourceBinding, s.resource.id)
        row.access_state, row.health = "granted", "healthy"
        db.update(row)
        other_binding = BoardAppBinding(project_id=11, app_key=devtron.APP_KEY, state="enabled",
                                       granted_capabilities=["resources.read"])
        db.insert(other_binding)
        other = AppResourceBinding(board_binding_id=other_binding.id, connection_id=s.connection.id,
            resource_type="application", external_resource_id="app-17-env-4", is_selected=True,
            access_state="granted", health="healthy", access_revision=5)
        db.insert(other)
        unrelated_connection = AppConnection(app_key=devtron.APP_KEY, owner_id=s.board[1].id,
            credential_reference=s.reference["uri"], instance_url="https://other.example.invalid", state="connected")
        db.insert(unrelated_connection)
        unrelated = AppResourceBinding(board_binding_id=s.binding.id, connection_id=unrelated_connection.id,
            resource_type="application", external_resource_id="app-18-env-4", is_selected=True,
            access_state="granted", health="healthy", access_revision=4)
        db.insert(unrelated)
        setup[3].is_enabled = False
        db.update(setup[3])
        db.insert(AppGovernancePolicy(scope_key="global", mode="disabled", generation=2))
    metadata = client.get(url, headers=headers).json()
    assert metadata["available"] is False
    result = client.post(f"{url}/{s.connection.get_uid()}/disconnect",
        json={"expected_revision": metadata["items"][0]["revision"]}, headers=headers)
    assert result.status_code == 200 and result.json()["state"] == "disconnected"
    assert result.headers["cache-control"] == "no-store" and notified == [True]
    assert client.get(url, params={"connection_uid": s.connection.get_uid()}, headers=headers).json()["items"] == [result.json()]
    with DbSession.atomic() as db:
        row = _current(db, AppResourceBinding, s.resource.id)
        assert (row.access_state, row.health, row.access_revision, row.is_selected) == ("revoked", "unavailable", 2, True)
        other = _current(db, AppResourceBinding, other.id)
        assert (other.access_state, other.health, other.access_revision) == ("revoked", "unavailable", 6)
        unrelated = _current(db, AppResourceBinding, unrelated.id)
        assert (unrelated.access_state, unrelated.health, unrelated.access_revision) == ("granted", "healthy", 4)
        assert _current(db, AppConnection, unrelated_connection.id).state == "connected"
        assert len(db.exec(SqlBuilder.select.table(AppConnection)).all()) == 2


@pytest.mark.parametrize("gate", ["stale", "role", "foreign", "inbound"])
def test_disconnect_rejects_wrong_authority_without_mutation(http_setup, gate):
    client, url, _, headers, setup = http_setup
    receipt = register(setup)
    body = {"expected_revision": receipt["revision"]}
    if gate == "stale":
        body["expected_revision"] = "0" * 64
    else:
        with DbSession.atomic() as db:
            connection = db.exec(SqlBuilder.select.table(AppConnection)).first()
            if gate == "role":
                setup[1][4].actions = ["read"]
                db.update(setup[1][4])
            elif gate == "foreign":
                connection.owner_id = setup[1][2].owner_id
                db.update(connection)
            else:
                connection.credential_reference, connection.instance_url = None, ""
                db.update(connection)
    result = client.post(f"{url}/{receipt['connection_uid']}/disconnect", json=body, headers=headers)
    assert result.status_code == (409 if gate == "stale" else 403)
    with DbSession.atomic() as db:
        assert db.exec(SqlBuilder.select.table(AppConnection)).first().state == "connected"


def test_safe_input_uses_existing_personal_owner_transport_and_current_board_authority(http_setup, monkeypatch):
    client, url, body, headers, setup = http_setup
    memory = {}
    monkeypatch.setattr(type(Env), "PUBLIC_UI_URL", property(lambda _: "https://ui.example.invalid"))
    monkeypatch.setattr(Cache, "get", lambda key, *a, **k: memory.get(key))
    monkeypatch.setattr(Cache, "set", lambda key, value, *a, **k: memory.__setitem__(key, value))
    root = url.removesuffix("/connections") + "/secret-input"
    result = client.post(root, json={"app_revision": body["app_revision"]}, headers=headers)
    assert result.status_code == 200 and result.headers["cache-control"] == "no-store"
    pending = result.json()
    assert pending["input_url"] == "https://ui.example.invalid/secret-input/" + pending["input_uid"]
    assert "secret_ref" not in pending and "value" not in pending
    params = {"app_revision": body["app_revision"]}
    assert client.get(f"{root}/{pending['input_uid']}", params=params, headers=headers).json() == {"state": "pending"}
    with DbSession.atomic() as db:
        other_actor = _current(db, User, setup[1][2].owner_id)
    foreign = SecretInput.begin_input(setup[0], other_actor, "personal", "me", "devtron/foreign")
    assert client.get(f"{root}/{foreign['input_uid']}", params=params, headers=headers).status_code == 403
    with DbSession.atomic() as db:
        setup[1][4].actions = ["read"]
        db.update(setup[1][4])
    assert client.get(f"{root}/{pending['input_uid']}", params=params, headers=headers).status_code == 403
    assert client.post(root, json=params, headers=headers).status_code == 403
    assert setup[4] == []


def test_disconnect_rolls_back_account_and_resources_when_revocation_fails(http_setup, monkeypatch):
    client, url, _, headers, setup = http_setup
    selected = select_target(setup)
    notifications = []
    monkeypatch.setattr(AppSettingPublisher, "apps_changed", lambda: notifications.append(True))
    original = devtron.revoke_connection_resources

    def fail_after_revocation(db, connection_id):
        original(db, connection_id)
        raise RuntimeError("Synthetic revocation failure")

    monkeypatch.setattr(devtron, "revoke_connection_resources", fail_after_revocation)
    response = client.post(f"{url}/{selected.connection.get_uid()}/disconnect",
                           json={"expected_revision": selected.connection.edit_revision()}, headers=headers)
    assert response.status_code == 500
    assert "Synthetic revocation failure" not in response.text
    with DbSession.atomic() as db:
        assert _current(db, AppConnection, selected.connection.id).state == "connected"
        resource = _current(db, AppResourceBinding, selected.resource.id)
        assert (resource.access_state, resource.access_revision) == ("unknown", 1)
    assert notifications == []
