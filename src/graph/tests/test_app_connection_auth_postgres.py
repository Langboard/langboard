"""Current app connection authentication and explicit consent on PostgreSQL."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from hashlib import sha256
from threading import Event, get_ident
import langboard.routes.settings.AppBoardConsentApi  # noqa: F401
import langboard.routes.settings.AppConnectionCredentialApi  # noqa: F401
import langboard.routes.settings.AppConnectionIdentityApi  # noqa: F401
import langboard.routes.settings.AppInboundConnectionApi  # noqa: F401
import langboard.routes.settings.AppMcpConsentApi  # noqa: F401
import langboard.routes.settings.AppRegistrySettingsApi  # noqa: F401
import langboard.routes.settings.AppResourceReadApi  # noqa: F401
import langboard.routes.settings.AppResourceSelectionApi  # noqa: F401
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
from langboard_sdk.resources import AppResources
from langboard_sdk.rest import HttpTransport
from langboard_shared.core.caching import Cache
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.routing import AppRouter
from langboard_shared.core.security import AuthSecurity
from langboard_shared.core.types import SafeDateTime, SnowflakeID
from langboard_shared.domain.models import (
    AppConnection,
    AppConnectionCredential,
    AppDefinition,
    AppMcpConsent,
    AppResourceBinding,
    BoardAppBinding,
    Organization,
    ProjectAssignedUser,
    ProjectRole,
    User,
)
from langboard_shared.domain.services.AppBoardConsent import prepare_board_consent, save_board_consent
from langboard_shared.domain.services.AppConnectionAuthentication import (
    authenticate_connection_credential,
    issue_connection_credential,
    list_connection_credentials,
    revoke_connection_credential,
)
from langboard_shared.domain.services.AppConnectionManagement import (
    create_inbound_connection,
    disconnect_inbound_connection,
    list_inbound_connections,
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
from langboard_shared.domain.services.AppRegistry import (
    AppRegistryConflict,
    AppRegistryDenied,
    disable_definition,
    list_definitions,
    save_definition,
)
from langboard_shared.domain.services.AppResourceSelection import list_resource_selections, save_resource_selection
from langboard_shared.Env import Env
from langboard_shared.security import Auth
from sqlalchemy import event, update
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


@pytest.mark.parametrize(
    "field,value",
    [
        ("credential_reference", "provider-secret-ref"),
        ("instance_url", "https://provider.example"),
        ("external_account_id", "provider-user"),
    ],
)
def test_provider_connections_cannot_issue_or_reuse_inbound_credentials(connected, field, value):
    engine, users, personal, _ = connected
    token = issue_connection_credential(users[1], personal.id)["token"]
    with Session(engine) as db:
        db.execute(update(AppConnection).where(AppConnection.id == personal.id).values(**{field: value}))
        db.commit()
    with pytest.raises(AppGovernanceDenied):
        issue_connection_credential(users[1], personal.id)
    with pytest.raises(AppGovernanceDenied):
        authenticate_connection_credential(token)
    assert len(list_connection_credentials(users[1], personal.id)["items"]) == 1


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
            with Session(engine) as db:
                db.execute(
                    update(AppConnection)
                    .where(AppConnection.id == personal.id)
                    .values(credential_reference="provider-secret-ref")
                )
                db.commit()
            assert request(owner, "POST", credential_path, json={}).status_code == 403
            assert client.get("/apps/v1/identity", headers={"Authorization": f"Bearer {token}"}).status_code == 401
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
            package = {"version": "1.0.0", "storage": "private/package.zip", "sha256": "installed-digest"}
            with Session(engine) as db:
                db.execute(update(AppDefinition).where(AppDefinition.key == "example").values(package=package))
                db.commit()
            old = request(admin, "GET", path).json()["items"][0]
            changed = {**DECLARATION, "version": "1.0.1"}
            body = {"declaration": changed, "expected_revision": old["revision"]}
            approved = request(admin, "POST", path, json=body)
            assert approved.status_code == 200 and approved.json()["generation"] == 2
            assert approved.headers["cache-control"] == "no-store"
            assert approved.json()["installation"] == old["installation"]
            with Session(engine) as db:
                assert db.query(AppDefinition).filter_by(key="example").one().package == package
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


def test_native_onboarding_private_and_organization_ownership(registered):
    _, (admin, owner, outsider), personal, shared = registered
    definition = list_definitions(admin)["items"][0]
    with pytest.raises(AppRegistryConflict):
        create_inbound_connection(owner, "example", "0" * 64)
    created = create_inbound_connection(owner, "example", definition["revision"])
    created_id = SnowflakeID.from_short_code(created["connection_uid"])
    assert created["ownership"] == "personal" and created["state"] == "connected"
    token = issue_connection_credential(owner, created_id)["token"]
    assert authenticate_connection_credential(token).owner_id == owner.id
    assert list_inbound_connections(admin, "example")["items"] == []
    assert list_inbound_connections(outsider, "example")["items"] == []
    with pytest.raises(AppGovernanceDenied):
        create_inbound_connection(outsider, "example", definition["revision"], 10)
    with pytest.raises(AppGovernanceDenied):
        disconnect_inbound_connection(admin, created_id, created["revision"])
    organization = create_inbound_connection(owner, "example", definition["revision"], 10)
    assert organization["ownership"] == "organization"
    assert len(list_inbound_connections(admin, "example", organization_id=10)["items"]) == 2
    initial = read_mcp_connection_consent(owner, created_id)
    save_mcp_connection_consent(
        owner, created_id, ["read"], initial["revision"], initial["identity_revision"], initial["app_revision"]
    )
    disconnected = disconnect_inbound_connection(owner, created_id, created["revision"])
    assert disconnected["state"] == "disconnected"
    with pytest.raises(AppGovernanceDenied):
        authenticate_connection_credential(token)
    assert read_mcp_connection_consent(owner, created_id)["granted_tools"] == {}
    with pytest.raises(AppRegistryConflict):
        disconnect_inbound_connection(owner, created_id, created["revision"])


def test_native_connection_listing_is_bounded_without_per_row_definition_queries(registered):
    engine, (admin, owner, _), *_ = registered
    definition = list_definitions(admin)["items"][0]
    for _ in range(3):
        create_inbound_connection(owner, "example", definition["revision"])
    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        page = list_inbound_connections(owner, "example", limit=2)
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert len(page["items"]) == 2 and page["next_cursor"] is not None
    # Current actor, declaration, bounded connection page; no per-row SQL.
    assert len(statements) == 3
    assert list_inbound_connections(
        owner, "example", after_id=SnowflakeID.from_short_code(page["next_cursor"]), limit=2
    )["items"]


def test_native_onboarding_http_revisions_permissions_and_no_credential_injection(registered, monkeypatch):
    engine, (admin, owner, outsider), *_ = registered
    revision = list_definitions(admin)["items"][0]["revision"]
    monkeypatch.setattr(type(Env), "JWT_SECRET_KEY", property(lambda _: "fixture-onboarding-http-secret-32bytes"))
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

            path = "/settings/apps/registry/example/inbound-connections"
            body = {"app_revision": revision}
            assert client.get(path).status_code == 401
            created = request(owner, "POST", path, json=body)
            assert created.status_code == 200 and created.headers["cache-control"] == "no-store"
            record = created.json()
            assert record["ownership"] == "personal"
            assert request(admin, "GET", path).json()["items"] == []
            assert request(owner, "POST", path, json={**body, "app_revision": "0" * 64}).status_code == 409
            for field in ("owner_id", "credential_reference", "instance_url", "granted_capabilities"):
                assert request(owner, "POST", path, json={**body, field: "injected"}).status_code in (400, 422)
            org_body = {**body, "organization_uid": SnowflakeID(10).to_short_code()}
            assert request(outsider, "POST", path, json=org_body).status_code == 403
            assert request(owner, "POST", path, json=org_body).status_code == 200
            assert request(owner, "GET", path, params={"after": "bad"}).status_code in (400, 422)
            assert request(owner, "GET", path, params={"limit": 51}).status_code in (400, 422)
            disconnect = f"/settings/apps/inbound-connections/{record['connection_uid']}/disconnect"
            revoke = {"expected_revision": record["revision"]}
            assert request(admin, "POST", disconnect, json=revoke).status_code == 403
            assert request(owner, "POST", disconnect, json={"expected_revision": "0" * 64}).status_code == 409
            result = request(owner, "POST", disconnect, json=revoke)
            assert result.status_code == 200 and result.json()["state"] == "disconnected"
            with Session(engine) as db:
                db.execute(update(User).where(User.id == owner.id).values(activated_at=None))
                db.commit()
            assert request(owner, "POST", path, json=body).status_code == 403
    finally:
        Cache.clear()


def test_board_consent_current_authority_revisions_and_withdrawal(registered):
    engine, (admin, owner, outsider), *_ = registered
    ProjectRole.__table__.create(engine)
    board_id = SnowflakeID(20)
    initial = prepare_board_consent(owner, board_id, "example")
    with pytest.raises(AppGovernanceDenied):
        prepare_board_consent(outsider, board_id, "example")

    def save(actor, caps, receipt):
        return save_board_consent(actor, board_id, "example", caps, receipt["revision"], receipt["app_revision"])

    with pytest.raises(ValueError):
        save(owner, ["cards.create"], initial)
    with pytest.raises(ValueError):
        save(owner, ["mcp.read", "mcp.read"], initial)
    saved = save(owner, ["mcp.read"], initial)
    with pytest.raises(AppGovernanceConflict):
        save(owner, ["mcp.write"], initial)
    with Session(engine) as db:
        db.add(ProjectAssignedUser(id=91, project_id=20, user_id=outsider.id))
        db.add(ProjectRole(id=92, project_id=20, user_id=outsider.id, actions=["read"]))
        db.commit()
    with pytest.raises(AppGovernanceDenied):
        prepare_board_consent(outsider, board_id, "example")
    with Session(engine) as db:
        db.execute(update(ProjectRole).where(ProjectRole.id == 92).values(actions=["update"]))
        db.commit()
    assert prepare_board_consent(outsider, board_id, "example")["revision"] == saved["revision"]
    with Session(engine) as db:
        db.execute(update(ProjectAssignedUser).where(ProjectAssignedUser.id == 91).values(user_id=admin.id))
        db.commit()
    with pytest.raises(AppGovernanceDenied):
        save(outsider, [], saved)
    policy = get_policy(admin)
    save_policy(admin, "disabled", policy["revision"])
    with pytest.raises(AppGovernanceDenied):
        save(owner, ["mcp.write"], saved)
    assert prepare_board_consent(owner, board_id, "example")["revision"] == saved["revision"]
    withdrawn = save(owner, [], saved)
    assert withdrawn["state"] == "disabled" and not withdrawn["stage_transitions_enabled"]
    assert withdrawn["granted_capabilities"] == []


def test_native_board_consent_http(registered, monkeypatch):
    engine, (admin, owner, outsider), *_ = registered
    monkeypatch.setattr(type(Env), "JWT_SECRET_KEY", property(lambda _: "fixture-board-http-secret-32bytes"))
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

            path = f"/board/{SnowflakeID(20).to_short_code()}/apps/example/consent"
            assert client.post(path + "/prepare").status_code == 401
            # Owner access never depends on a role row.
            token = issue_connection_credential(owner, registered[2].id)["token"]
            assert client.post(path + "/prepare", headers={"Authorization": f"Bearer {token}"}).status_code == 401
            initial = request(owner, "POST", path + "/prepare")
            assert initial.status_code == 200
            receipt = initial.json()
            body = {
                "capabilities": ["mcp.read"],
                "expected_revision": receipt["revision"],
                "expected_app_revision": receipt["app_revision"],
            }
            assert request(owner, "PUT", path, json={**body, "stage_transitions_enabled": True}).status_code in (
                400,
                422,
            )
            approved = request(owner, "PUT", path, json=body)
            assert approved.status_code == 200 and approved.headers["cache-control"] == "no-store"
            assert request(owner, "PUT", path, json=body).status_code == 409
            with Session(engine) as db:
                db.execute(update(User).where(User.id == owner.id).values(activated_at=None))
                db.commit()
            assert request(owner, "POST", path + "/prepare").status_code == 403
    finally:
        Cache.clear()


def test_board_prepare_never_grants_and_rejects_stale_app(registered):
    engine, (_, owner, _), *_ = registered
    with Session(engine) as db:
        db.query(BoardAppBinding).delete()
        db.commit()
    initial = prepare_board_consent(owner, SnowflakeID(20), "example")
    assert initial["state"] == "disabled" and initial["granted_capabilities"] == []
    assert not initial["stage_transitions_enabled"]
    with Session(engine) as db:
        db.execute(update(AppDefinition).where(AppDefinition.key == "example").values(generation=2))
        db.commit()
    with pytest.raises(AppGovernanceConflict):
        save_board_consent(
            owner, SnowflakeID(20), "example", ["mcp.read"], initial["revision"], initial["app_revision"]
        )
    current = prepare_board_consent(owner, SnowflakeID(20), "example")
    with Session(engine) as db:
        db.execute(update(AppDefinition).where(AppDefinition.key == "example").values(is_enabled=False))
        db.commit()
        definition_revision = db.query(AppDefinition).one().edit_revision()
    with pytest.raises(AppGovernanceDenied):
        save_board_consent(owner, SnowflakeID(20), "example", ["mcp.read"], current["revision"], definition_revision)
    assert (
        save_board_consent(owner, SnowflakeID(20), "example", [], current["revision"], definition_revision)["state"]
        == "disabled"
    )


def test_resource_selection_one_to_many_privacy_revisions_and_withdrawal(registered):
    engine, (admin, owner, outsider), personal, _ = registered
    AppResourceBinding.__table__.create(engine)
    with Session(engine) as db:
        declared = deepcopy(DECLARATION)
        declared["capabilities"].append("resources.read")
        db.execute(update(AppDefinition).values(declaration=declared))
        db.execute(update(BoardAppBinding).values(granted_capabilities=["resources.read"]))
        db.commit()
    approval = prepare_board_consent(owner, SnowflakeID(20), "example")

    identity_revision = list_resource_selections(owner, SnowflakeID(20), "example", personal.id)["identity_revision"]

    def save(actor, identity, selected=True, revision=0):
        response = save_resource_selection(
            actor,
            SnowflakeID(20),
            "example",
            personal.id,
            "issue",
            identity,
            selected,
            revision,
            approval["revision"],
            approval["app_revision"],
            identity_revision,
        )
        approval["revision"] = response["binding_revision"]
        return response

    first = save(owner, "one")
    save(owner, "two")
    assert first["access_state"] == "unknown" and first["health"] == "unknown"
    assert first["is_selected"] and first["revision"] == 1
    page = list_resource_selections(owner, SnowflakeID(20), "example", personal.id, limit=1)
    assert len(page["items"]) == 1 and page["next_cursor"]
    assert len(list_resource_selections(owner, SnowflakeID(20), "example", personal.id)["items"]) == 2
    with pytest.raises(AppGovernanceConflict):
        save(owner, "one")
    with pytest.raises(AppGovernanceDenied):
        save(admin, "three")
    with Session(engine) as db:
        db.execute(update(AppConnection).where(AppConnection.id == personal.id).values(state="disconnected"))
        db.commit()
    with pytest.raises(AppGovernanceDenied):
        save(owner, "three")
    revoked = save(owner, "one", False, 1)
    assert not revoked["is_selected"] and revoked["access_state"] == "revoked" and revoked["revision"] == 2


def test_native_resource_selection_http_is_bounded_and_cannot_claim_access(registered, monkeypatch):
    engine, (_, owner, _), personal, _ = registered
    AppResourceBinding.__table__.create(engine)
    with Session(engine) as db:
        declaration = deepcopy(DECLARATION)
        declaration["capabilities"].append("resources.read")
        db.execute(update(AppDefinition).values(declaration=declaration))
        db.execute(update(BoardAppBinding).values(granted_capabilities=["resources.read"]))
        db.commit()
    approval = prepare_board_consent(owner, SnowflakeID(20), "example")
    monkeypatch.setattr(type(Env), "JWT_SECRET_KEY", property(lambda _: "fixture-resource-http-secret-32bytes"))
    Cache.clear()
    Auth.reset_user(owner)
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    try:
        with TestClient(app) as client:
            path = f"/board/{SnowflakeID(20).to_short_code()}/apps/example/connections/{personal.get_uid()}/resources"
            assert client.get(path).status_code == 401
            access, refresh = AuthSecurity.authenticate(owner.id)
            client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
            headers = {"Authorization": f"Bearer {access}"}
            identity_revision = client.get(path, headers=headers).json()["identity_revision"]
            body = {
                "resource_type": "issue",
                "external_resource_id": "one",
                "selected": True,
                "expected_revision": 0,
                "expected_binding_revision": approval["revision"],
                "expected_app_revision": approval["app_revision"],
                "expected_identity_revision": identity_revision,
            }
            assert client.put(path, headers=headers, json={**body, "access_state": "granted"}).status_code in (400, 422)
            saved = client.put(path, headers=headers, json=body)
            assert saved.status_code == 200 and saved.json()["access_state"] == "unknown"
            assert saved.headers["cache-control"] == "no-store"
            assert client.put(path, headers=headers, json=body).status_code == 409
            assert client.get(path, headers=headers, params={"limit": 51}).status_code in (400, 422)
            assert len(client.get(path, headers=headers).json()["items"]) == 1
            token = issue_connection_credential(owner, personal.id)["token"]
            assert client.put(path, headers={"Authorization": f"Bearer {token}"}, json=body).status_code == 401
    finally:
        Cache.clear()


def test_app_resource_http_only_verified_current_identity_and_sdk_shape(registered, monkeypatch):
    engine, (_, owner, _), personal, _ = registered
    AppResourceBinding.__table__.create(engine)
    with Session(engine) as db:
        declaration = deepcopy(DECLARATION)
        declaration["capabilities"].append("resources.read")
        db.execute(update(AppDefinition).values(declaration=declaration))
        db.execute(update(BoardAppBinding).values(granted_capabilities=["resources.read"]))
        db.commit()
    approval = prepare_board_consent(owner, SnowflakeID(20), "example")
    identity = list_resource_selections(owner, SnowflakeID(20), "example", personal.id)["identity_revision"]
    selected = save_resource_selection(
        owner,
        SnowflakeID(20),
        "example",
        personal.id,
        "issue",
        "one",
        True,
        0,
        approval["revision"],
        approval["app_revision"],
        identity,
    )
    token = issue_connection_credential(owner, personal.id)["token"]
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    path = f"/apps/v1/boards/{SnowflakeID(20).to_short_code()}/resources"
    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(app) as client:
        assert client.get(path).status_code == 401
        page = client.get(path, headers=headers).json()
        assert page["schema_version"] == 1 and page["connection_uid"] == personal.get_uid()
        assert page["items"] == []  # Selection is not a verification receipt.
        assert page["binding_revision"] == selected["binding_revision"]
        # Synthetic provider verification fixture, not actual provider acceptance.
        with Session(engine) as db:
            db.execute(update(AppResourceBinding).values(access_state="granted", health="healthy"))
            db.commit()
        response = client.get(path, headers=headers)
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"

        async def sdk_read():
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://testserver", headers=headers
            ) as session:
                return await AppResources(HttpTransport(session)).list(SnowflakeID(20).to_short_code(), limit=1)

        sdk_page = asyncio.run(sdk_read())
        assert sdk_page["items"] == response.json()["items"]
        assert sdk_page["connection_uid"] == personal.get_uid()
        resource = response.json()["items"][0]
        assert set(resource) == {
            "resource_uid",
            "resource_type",
            "external_resource_id",
            "resource_path",
            "access_revision",
            "health",
        }
        assert resource["external_resource_id"] == "one"
        assert client.get(path, headers=headers, params={"limit": 51}).status_code in (400, 422)
        with Session(engine) as db:
            db.execute(
                update(AppConnection)
                .where(AppConnection.id == personal.id)
                .values(updated_at=SafeDateTime.now() + timedelta(seconds=1))
            )
            db.commit()
        assert client.get(path, headers=headers).status_code == 403
        new_token = issue_connection_credential(owner, personal.id)["token"]
        assert client.get(path, headers={"Authorization": f"Bearer {new_token}"}).json()["items"] == []
        current = prepare_board_consent(owner, SnowflakeID(20), "example")
        with pytest.raises(AppGovernanceConflict):
            save_resource_selection(
                owner,
                SnowflakeID(20),
                "example",
                personal.id,
                "issue",
                "one",
                True,
                1,
                current["revision"],
                current["app_revision"],
                identity,
            )
        with Session(engine) as db:
            db.execute(update(BoardAppBinding).values(granted_capabilities=[]))
            db.commit()
        assert client.get(path, headers={"Authorization": f"Bearer {new_token}"}).status_code == 403
