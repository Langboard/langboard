"""Current app connection authentication and explicit consent on PostgreSQL."""

from copy import deepcopy
from datetime import timedelta
from hashlib import sha256
import pytest
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import (
    AppConnection,
    AppConnectionCredential,
    AppDefinition,
    AppMcpConsent,
    BoardAppBinding,
    Organization,
    User,
)
from langboard_shared.domain.services.AppConnectionAuthentication import (
    authenticate_connection_credential,
    issue_connection_credential,
    list_connection_credentials,
    revoke_connection_credential,
)
from langboard_shared.domain.services.AppGovernance import (
    AppGovernanceConflict,
    AppGovernanceDenied,
    get_policy,
    save_policy,
)
from langboard_shared.domain.services.AppMcpConnectionConsent import (
    effective_mcp_consent,
    read_mcp_connection_consent,
    save_mcp_connection_consent,
)
from sqlalchemy import update
from sqlalchemy.orm import Session
from test_app_governance_postgres import governance  # noqa: F401


DECLARATION = {
    "schema_version": 1,
    "key": "example",
    "version": "1.0.0",
    "name": "Example",
    "description": "Independent app",
    "capabilities": ["mcp.read", "mcp.write"],
    "resource_types": ["issue"],
    "publisher": "Example publisher",
    "mcp": {
        "identity": "example",
        "tools": [
            {"name": "read_issue", "description": "Read issue", "authority_scope": "read"},
            {"name": "write_issue", "description": "Write issue", "authority_scope": "write"},
        ],
    },
}


@pytest.fixture
def connected(governance):  # noqa: F811
    engine, users, org, project, personal, shared = governance
    for model in (AppDefinition, AppConnectionCredential, AppMcpConsent):
        model.__table__.create(engine)
    with Session(engine) as db:
        db.add(AppDefinition(id=70, key="example", declaration=deepcopy(DECLARATION), approved_by=1))
        db.commit()
    return engine, users, personal, shared


def consent(owner, connection, scopes):
    receipt = read_mcp_connection_consent(owner, connection.id)
    return save_mcp_connection_consent(
        owner, connection.id, scopes, receipt["revision"], receipt["identity_revision"], receipt["app_revision"]
    )


def test_hashed_credential_receipts_and_private_owner(connected):
    engine, users, personal, shared = connected
    issued = issue_connection_credential(users[1], personal.id)
    token = issued["token"]
    assert authenticate_connection_credential(token).owner_id == users[1].id
    with Session(engine) as db:
        row = db.query(AppConnectionCredential).one()
        assert row.token_hash == sha256(token.encode()).hexdigest()
        assert token not in repr(row.__dict__)
        credential_id = row.id
    receipt = list_connection_credentials(users[1], personal.id, limit=1)
    assert len(receipt["items"]) == 1 and token not in str(receipt)
    for operation in (
        lambda: issue_connection_credential(users[0], personal.id),
        lambda: list_connection_credentials(users[0], personal.id),
        lambda: revoke_connection_credential(users[0], personal.id, credential_id),
    ):
        with pytest.raises(AppGovernanceDenied):
            operation()
    revoke_connection_credential(users[1], personal.id, credential_id)
    revoke_connection_credential(users[1], personal.id, credential_id)
    with pytest.raises(AppGovernanceDenied):
        authenticate_connection_credential(token)


@pytest.mark.parametrize("mutation", ["expiry", "owner", "connection", "trust", "app", "policy", "organization"])
def test_current_fences_reject_previously_valid_credential(connected, mutation):
    engine, users, personal, shared = connected
    connection = shared if mutation == "organization" else personal
    token = issue_connection_credential(users[1], connection.id)["token"]
    assert authenticate_connection_credential(token).connection_id == connection.id
    with Session(engine) as db:
        if mutation == "expiry":
            db.execute(update(AppConnectionCredential).values(expires_at=SafeDateTime.now() - timedelta(seconds=1)))
        elif mutation == "owner":
            db.execute(update(User).where(User.id == 2).values(activated_at=None))
        elif mutation == "connection":
            db.execute(update(AppConnection).where(AppConnection.id == connection.id).values(state="revoked"))
        elif mutation == "trust":
            db.execute(update(AppDefinition).values(declaration={**DECLARATION, "publisher": "Changed publisher"}))
        elif mutation == "app":
            db.execute(update(AppDefinition).values(is_enabled=False))
        elif mutation == "organization":
            db.execute(update(Organization).where(Organization.id == 10).values(is_active=False))
        db.commit()
    if mutation == "policy":
        save_policy(users[0], "disabled", get_policy(users[0])["revision"])
    with pytest.raises(AppGovernanceDenied):
        authenticate_connection_credential(token)


def test_explicit_consent_versions_and_scope_expansion(connected):
    engine, users, personal, shared = connected
    assert read_mcp_connection_consent(users[1], personal.id)["scopes"] == []
    granted = consent(users[1], personal, ["read"])
    assert granted["scopes"] == ["read"] and granted["execution_supported"] is False
    with pytest.raises(AppGovernanceDenied):
        read_mcp_connection_consent(users[0], personal.id)
    with pytest.raises(AppGovernanceConflict):
        save_mcp_connection_consent(
            users[1], personal.id, ["write"], None, granted["identity_revision"], granted["app_revision"]
        )
    with Session(engine) as db:
        newer = deepcopy(DECLARATION)
        newer["version"] = "1.0.1"
        newer["mcp"]["tools"].append({"name": "close_issue", "description": "Close issue", "authority_scope": "write"})
        db.execute(update(AppDefinition).values(declaration=newer))
        db.commit()
    assert read_mcp_connection_consent(users[1], personal.id)["scopes"] == ["read"]
    assert consent(users[1], personal, ["read", "write"])["scopes"] == ["read", "write"]
    assert consent(users[1], personal, [])["scopes"] == []


def test_consent_rechecks_owner_and_current_capability(connected):
    engine, users, personal, shared = connected
    consent(users[1], personal, ["read"])
    with Session(engine) as db:
        db.execute(update(User).where(User.id == 2).values(activated_at=None))
        db.commit()
    with DbSession.atomic() as db:
        connection = db.exec(SqlBuilder.select.table(AppConnection).where(AppConnection.id == personal.id)).first()
        definition = db.exec(SqlBuilder.select.table(AppDefinition)).first()
        assert effective_mcp_consent(db, connection, definition) == []


@pytest.mark.parametrize("scopes", [[{}], ["read", "read"], ["unknown"], "read"])
def test_malformed_consent_is_validation_failure(connected, scopes):
    _, users, personal, _ = connected
    with pytest.raises(ValueError):
        save_mcp_connection_consent(users[1], personal.id, scopes, None, "", "")


def test_native_authenticated_http_connection_routes(connected, monkeypatch):
    import langboard.routes.settings.AppConnectionCredentialApi  # noqa: F401
    import langboard.routes.settings.AppConnectionIdentityApi  # noqa: F401
    import langboard.routes.settings.AppMcpConsentApi  # noqa: F401
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
    from langboard_shared.core.caching import Cache
    from langboard_shared.core.routing import AppRouter
    from langboard_shared.core.security import AuthSecurity
    from langboard_shared.Env import Env
    from langboard_shared.security import Auth

    engine, (admin, owner, outsider), personal, shared = connected
    monkeypatch.setattr(type(Env), "JWT_SECRET_KEY", property(lambda _: "fixture-connection-http-secret-32bytes"))
    Cache.clear()
    for actor in (admin, owner, outsider):
        Auth.reset_user(actor)
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    try:
        with TestClient(app) as client:

            def request(actor, method, path, **kwargs):
                access, refresh = AuthSecurity.authenticate(actor.id)
                client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
                return client.request(method, path, headers={"Authorization": f"Bearer {access}"}, **kwargs)

            base = f"/settings/apps/connections/{personal.get_uid()}"
            credential_path = base + "/credentials"
            consent_path = base + "/mcp-consent"
            assert client.get(credential_path).status_code == 401
            for actor in (admin, outsider):
                assert request(actor, "GET", credential_path).status_code == 403
                assert request(actor, "POST", credential_path, json={}).status_code == 403
                assert request(actor, "GET", consent_path).status_code == 403
            issued = request(owner, "POST", credential_path, json={"expires_in_seconds": 120})
            assert issued.status_code == 200 and issued.headers["cache-control"] == "no-store"
            token = issued.json()["token"]
            credentials = request(owner, "GET", credential_path, params={"limit": 1})
            assert credentials.status_code == 200 and token not in credentials.text
            for payload in ({"expires_in_seconds": True}, {"expires_in_seconds": 59}, {"extra": True}):
                assert request(owner, "POST", credential_path, json=payload).status_code in (400, 422)
            assert request(owner, "GET", credential_path, params={"limit": 51}).status_code in (400, 422)
            assert request(owner, "GET", credential_path, params={"after": "bad"}).status_code in (400, 422)
            assert request(owner, "GET", "/settings/apps/connections/bad/credentials").status_code in (400, 422)
            identity = client.get("/apps/v1/identity", headers={"Authorization": f"Bearer {token}"})
            assert identity.status_code == 200 and identity.json()["app_key"] == "example"
            assert "owner_id" not in identity.json() and "token" not in identity.json()
            # App authentication never substitutes for a human consent or credential issuer.
            assert client.get(consent_path, headers={"Authorization": f"Bearer {token}"}).status_code != 200
            access, _ = AuthSecurity.authenticate(owner.id)
            assert client.get("/apps/v1/identity", headers={"Authorization": f"Bearer {access}"}).status_code == 401
            initial = request(owner, "GET", consent_path).json()
            body = {
                "scopes": ["read"],
                "expected_revision": initial["revision"],
                "expected_identity_revision": initial["identity_revision"],
                "expected_app_revision": initial["app_revision"],
            }
            saved = request(owner, "PUT", consent_path, json=body)
            assert saved.status_code == 200 and saved.json()["scopes"] == ["read"]
            assert saved.headers["cache-control"] == "no-store"
            assert request(owner, "PUT", consent_path, json=body).status_code == 409
            assert request(owner, "PUT", consent_path, json={**body, "scopes": ["read", "read"]}).status_code == 400
            revoked = request(owner, "POST", credential_path + "/" + issued.json()["credential_uid"] + "/revoke")
            assert revoked.status_code == 200
            assert client.get("/apps/v1/identity", headers={"Authorization": f"Bearer {token}"}).status_code == 401
            with Session(engine) as db:
                db.execute(update(User).where(User.id == owner.id).values(activated_at=None))
                db.commit()
            assert request(owner, "GET", credential_path).status_code == 403
            assert request(owner, "GET", consent_path).status_code == 403
    finally:
        Cache.clear()


@pytest.fixture
def registered(connected):
    engine, users, personal, shared = connected
    BoardAppBinding.__table__.create(engine)
    with Session(engine) as db:
        db.add(
            BoardAppBinding(
                id=80,
                project_id=20,
                app_key="example",
                state="enabled",
                granted_capabilities=["mcp.read", "mcp.write"],
                stage_transitions_enabled=True,
            )
        )
        db.commit()
    return connected


def test_registry_update_preserves_unchanged_scopes_and_never_resurrects_revoked_consent(registered):
    from langboard_shared.domain.services.AppRegistry import list_definitions, save_definition

    engine, (admin, owner, _), personal, _ = registered
    consent(owner, personal, ["read", "write"])
    old = list_definitions(admin)["items"][0]
    changed = deepcopy(DECLARATION)
    changed["version"] = "1.0.1"
    changed["mcp"]["tools"].append({"name": "close_issue", "description": "Close issue", "authority_scope": "write"})
    updated = save_definition(admin, changed, old["revision"])
    assert read_mcp_connection_consent(owner, personal.id)["scopes"] == ["read"]
    with Session(engine) as db:
        assert db.get(BoardAppBinding, 80).granted_capabilities == ["mcp.read"]
    reverted = {**DECLARATION, "version": "1.0.2"}
    save_definition(admin, reverted, updated["revision"])
    # Returning to the earlier tool list cannot restore its former write approval.
    assert read_mcp_connection_consent(owner, personal.id)["scopes"] == ["read"]


def test_registry_trust_disable_and_reenable_require_new_owner_consent(registered):
    from langboard_shared.domain.services.AppRegistry import disable_definition, list_definitions, save_definition

    engine, (admin, owner, _), personal, _ = registered
    consent(owner, personal, ["read"])
    token = issue_connection_credential(owner, personal.id)["token"]
    old = list_definitions(admin)["items"][0]
    trust_change = {**DECLARATION, "version": "1.0.1", "publisher": "New publisher"}
    updated = save_definition(admin, trust_change, old["revision"])
    with pytest.raises(AppGovernanceDenied):
        authenticate_connection_credential(token)
    with Session(engine) as db:
        assert db.get(AppConnection, personal.id).state == "revoked"
        binding = db.get(BoardAppBinding, 80)
        assert binding.state == "needs_attention" and not binding.stage_transitions_enabled
        db.execute(update(AppConnection).where(AppConnection.id == personal.id).values(state="connected"))
        db.commit()
    consent(owner, personal, ["read"])
    disabled = disable_definition(admin, "example", updated["revision"])
    assert disabled["is_enabled"] is False
    save_definition(admin, {**trust_change, "version": "1.0.2"}, disabled["revision"])
    assert read_mcp_connection_consent(owner, personal.id)["scopes"] == []
    with Session(engine) as db:
        assert db.get(BoardAppBinding, 80).state == "disabled"


def test_registry_admin_revision_paging_and_card_lifecycle_contract(registered):
    from langboard_shared.domain.services.AppRegistry import (
        AppRegistryConflict,
        AppRegistryDenied,
        list_definitions,
        save_definition,
    )

    engine, (admin, owner, _), personal, _ = registered
    with pytest.raises(AppRegistryDenied):
        list_definitions(owner)
    old = list_definitions(admin)["items"][0]
    with pytest.raises(AppRegistryConflict):
        save_definition(admin, {**DECLARATION, "version": "1.0.1"}, "0" * 64)
    with pytest.raises(ValueError):
        save_definition(admin, DECLARATION, old["revision"])
    with pytest.raises(ValueError):
        save_definition(admin, {**DECLARATION, "key": "card-maker", "capabilities": ["cards.create"]})
    # A second independently named app uses exactly the same declaration approval API.
    saved = save_definition(admin, {**DECLARATION, "key": "second-app"})
    page = list_definitions(admin, limit=1)
    assert page["next_cursor"] == "example" and len(page["items"]) == 1
    assert list_definitions(admin, after_key=page["next_cursor"], limit=1)["items"][0] == saved
    with Session(engine) as db:
        db.execute(update(User).where(User.id == admin.id).values(is_admin=False))
        db.commit()
    with pytest.raises(AppRegistryDenied):
        save_definition(admin, {**DECLARATION, "key": "third-app"})


def test_registry_authenticated_http_approval_and_current_admin(registered, monkeypatch):
    import langboard.routes.settings.AppRegistrySettingsApi  # noqa: F401
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
    from langboard_shared.core.caching import Cache
    from langboard_shared.core.routing import AppRouter
    from langboard_shared.core.security import AuthSecurity
    from langboard_shared.Env import Env
    from langboard_shared.security import Auth

    engine, (admin, owner, _), *_ = registered
    monkeypatch.setattr(type(Env), "JWT_SECRET_KEY", property(lambda _: "fixture-registry-http-secret-32bytes"))
    Cache.clear()
    Auth.reset_user(admin)
    Auth.reset_user(owner)
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    try:
        with TestClient(app) as client:

            def request(actor, method, path, **kwargs):
                access, refresh = AuthSecurity.authenticate(actor.id)
                client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
                return client.request(method, path, headers={"Authorization": f"Bearer {access}"}, **kwargs)

            path = "/settings/apps/registry"
            assert client.get(path).status_code == 401
            assert request(owner, "GET", path).status_code == 403
            old = request(admin, "GET", path).json()["items"][0]
            changed = {**DECLARATION, "version": "1.0.1"}
            body = {"declaration": changed, "expected_revision": old["revision"]}
            approved = request(admin, "POST", path, json=body)
            assert approved.status_code == 200 and approved.json()["generation"] == 2
            assert approved.headers["cache-control"] == "no-store"
            assert request(admin, "POST", path, json=body).status_code == 409
            assert request(admin, "GET", path, params={"limit": 51}).status_code in (400, 422)
            assert request(
                admin, "POST", path + "/bad key/disable", json={"expected_revision": old["revision"]}
            ).status_code in (400, 422)
            with Session(engine) as db:
                db.execute(update(User).where(User.id == admin.id).values(is_admin=False))
                db.commit()
            assert (
                request(admin, "POST", path, json={"declaration": {**DECLARATION, "key": "second"}}).status_code == 403
            )
    finally:
        Cache.clear()


def test_concurrent_trust_update_and_owner_consent_cannot_deadlock_or_restore_grant(registered):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, get_ident
    from langboard_shared.domain.services.AppRegistry import list_definitions, save_definition
    from sqlalchemy import event

    engine, (admin, owner, _), personal, _ = registered
    receipt = consent(owner, personal, ["read"])
    old = list_definitions(admin)["items"][0]
    definition_locked, owner_attempting = Event(), Event()
    registry_thread = []

    def after_statement(conn, cursor, statement, parameters, context, executemany):
        if (
            registry_thread
            and get_ident() == registry_thread[0]
            and "app_definition" in statement
            and "FOR UPDATE" in statement
        ):
            definition_locked.set()
            assert owner_attempting.wait(5)

    def update_trust():
        registry_thread.append(get_ident())
        return save_definition(
            admin, {**DECLARATION, "version": "1.0.1", "publisher": "New publisher"}, old["revision"]
        )

    def save_old_consent():
        assert definition_locked.wait(5)
        owner_attempting.set()
        with pytest.raises((AppGovernanceConflict, AppGovernanceDenied)):
            save_mcp_connection_consent(
                owner, personal.id, ["read"], receipt["revision"], receipt["identity_revision"], receipt["app_revision"]
            )

    event.listen(engine, "after_cursor_execute", after_statement)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            updated = executor.submit(update_trust)
            attempted = executor.submit(save_old_consent)
            assert updated.result(timeout=10)["generation"] == 2
            attempted.result(timeout=10)
    finally:
        event.remove(engine, "after_cursor_execute", after_statement)
    assert read_mcp_connection_consent(owner, personal.id)["scopes"] == []
