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
