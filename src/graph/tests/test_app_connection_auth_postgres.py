"""Current app connection authentication and explicit consent on PostgreSQL."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
from threading import Event, get_ident
from time import monotonic, sleep
import langboard.routes.settings.AppBoardConsentApi  # noqa: F401
import langboard.routes.settings.AppConnectionCredentialApi  # noqa: F401
import langboard.routes.settings.AppConnectionIdentityApi  # noqa: F401
import langboard.routes.settings.AppExecutionLeaseApi  # noqa: F401
import langboard.routes.settings.AppExecutionRequestReadApi  # noqa: F401
import langboard.routes.settings.AppInboundConnectionApi  # noqa: F401
import langboard.routes.settings.AppMcpConsentApi  # noqa: F401
import langboard.routes.settings.AppRegistrySettingsApi  # noqa: F401
import langboard.routes.settings.AppResourceReadApi  # noqa: F401
import langboard.routes.settings.AppResourceSelectionApi  # noqa: F401
import langboard.routes.settings.AppWorkflowMappingApi  # noqa: F401
import langboard.routes.settings.BoardWorkflowSettingsApi  # noqa: F401
import langboard.routes.settings.CardAppOwnershipApi  # noqa: F401
import langboard.routes.settings.CardAppResourcesApi  # noqa: F401
import langboard.routes.settings.CardExecutionIntentApi  # noqa: F401
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from langboard.card_workspace.application.queries import get_card_bundle, get_project_identity
from langboard.card_workspace.domain import CommentPage, SectionPage
from langboard.card_workspace.infrastructure.native import NativeCardWorkspaceAdapter
from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
from langboard_sdk.client import MutationOutcomeUnknown
from langboard_sdk.execution import AppExecution
from langboard_sdk.resources import AppResources
from langboard_sdk.rest import HttpTransport, NativeApiError
from langboard_shared.core.caching import Cache
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.routing import AppRouter
from langboard_shared.core.security import AuthSecurity
from langboard_shared.core.types import SafeDateTime, SnowflakeID
from langboard_shared.domain.models import (
    AppConnection,
    AppConnectionCredential,
    AppDefinition,
    AppExecutionAcknowledgment,
    AppExecutionOutbox,
    AppMcpConsent,
    AppResourceBinding,
    BoardAppBinding,
    Card,
    CardAppOwnership,
    CardAppOwnershipAudit,
    CardAppResourceSelection,
    CardAppResourceSelectionAudit,
    CardExecutionGeneration,
    CardExecutionIntent,
    CardRelationship,
    GlobalCardRelationshipType,
    Organization,
    ProjectAssignedUser,
    ProjectColumn,
    ProjectRole,
    User,
    WorkflowStageDefinition,
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
from langboard_shared.domain.services.AppWorkflowMapping import read_workflow_mapping, save_workflow_mapping
from langboard_shared.domain.services.BoardWorkflowSettings import read_column_workflow, save_column_workflow
from langboard_shared.domain.services.CardAppGovernance import read_card_app_ownership, set_card_app_ownership
from langboard_shared.domain.services.CardAppResources import read_card_app_resources, set_card_app_resources
from langboard_shared.domain.services.CardDependencyState import card_dependency_state
from langboard_shared.domain.services.CardExecutionIntent import (
    read_execution_intent_context,
    request_card_execution_intent,
)
from langboard_shared.domain.services.DomainService import DomainService
from langboard_shared.Env import Env
from langboard_shared.publishers import CardRelationshipPublisher, ProjectColumnPublisher
from langboard_shared.security import Auth
from langboard_shared.tasks.activities import CardRelationshipActivityTask
from langboard_shared.tasks.bots import CardBotTask
from sqlalchemy import event, select, text, update
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


@pytest.mark.parametrize("operation", ["board", "mcp", "credential"])
@pytest.mark.parametrize("organization", [False, True])
@pytest.mark.parametrize("existing_policy", [False, True])
def test_policy_disable_serializes_with_consent_writes_including_absent_rows(
    registered, organization, existing_policy, operation
):
    engine, (admin, owner, _), personal, shared = registered
    org_id = SnowflakeID(10) if organization else None
    connection = shared if organization else personal
    connection_receipt = read_mcp_connection_consent(owner, connection.id)
    initial = prepare_board_consent(owner, SnowflakeID(20), "example")
    baseline = get_policy(admin, org_id)
    if existing_policy:
        save_policy(admin, "approved_only", baseline["revision"], org_id)
        baseline = get_policy(admin, org_id)
    policy_locked, consent_attempting = Event(), Event()
    policy_thread = []
    waiting_pid = []

    def statement_done(connection, cursor, statement, parameters, context, executemany):
        if get_ident() in policy_thread and "pg_advisory_xact_lock" in statement:
            policy_locked.set()
            assert consent_attempting.wait(5)
            deadline = monotonic() + 5
            while monotonic() < deadline:
                with engine.connect() as observer:
                    blocked = observer.execute(
                        text("SELECT count(*) FROM pg_locks WHERE pid=:pid AND locktype='advisory' AND NOT granted"),
                        {"pid": waiting_pid[0]},
                    ).scalar()
                if blocked:
                    break
                sleep(0.01)
            else:
                raise AssertionError("Consent write never waited for the policy transaction lock")

    def before_statement(connection, cursor, statement, parameters, context, executemany):
        if "pg_advisory_xact_lock_shared" in statement:
            waiting_pid.append(connection.connection.driver_connection.info.backend_pid)
            consent_attempting.set()

    def disable():
        policy_thread.append(get_ident())
        return save_policy(admin, "disabled", baseline["revision"], org_id)

    def approve():
        assert policy_locked.wait(5)
        with pytest.raises(AppGovernanceDenied):
            if operation == "board":
                save_board_consent(
                    owner, SnowflakeID(20), "example", ["mcp.read"], initial["revision"], initial["app_revision"]
                )
            elif operation == "mcp":
                save_mcp_connection_consent(
                    owner,
                    connection.id,
                    ["read"],
                    connection_receipt["revision"],
                    connection_receipt["identity_revision"],
                    connection_receipt["app_revision"],
                )
            else:
                issue_connection_credential(owner, connection.id)

    event.listen(engine, "after_cursor_execute", statement_done)
    event.listen(engine, "before_cursor_execute", before_statement)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            disabled = executor.submit(disable)
            attempted = executor.submit(approve)
            assert disabled.result(timeout=10)["effective_mode"] == "disabled"
            attempted.result(timeout=10)
    finally:
        event.remove(engine, "after_cursor_execute", statement_done)
        event.remove(engine, "before_cursor_execute", before_statement)
    assert prepare_board_consent(owner, SnowflakeID(20), "example")["revision"] == initial["revision"]


def test_generic_builtin_workflow_mapping_preserves_ambiguity_and_requires_opt_in(registered, monkeypatch):
    engine, (_, owner, _), *_ = registered
    WorkflowStageDefinition.__table__.create(engine)
    ProjectColumn.__table__.create(engine)
    with Session(engine) as db:
        definition = deepcopy(DECLARATION)
        definition["workflow_requirements"] = {"required": ["active", "review"], "optional": ["closed"]}
        db.execute(update(AppDefinition).values(declaration=definition))
        db.execute(update(BoardAppBinding).values(stage_transitions_enabled=False))
        db.add(
            WorkflowStageDefinition(
                id=201, key="active", name="Active", description="Work being executed", is_builtin=True
            )
        )
        db.add(WorkflowStageDefinition(id=202, key="review", name="Review", is_builtin=True))
        db.add(WorkflowStageDefinition(id=203, key="closed", name="Closed", is_builtin=True))
        db.flush()
        db.add(
            ProjectColumn(
                id=211,
                project_id=20,
                name="Any name",
                description="Board-specific instructions",
                workflow_stage="active",
            )
        )
        db.add(ProjectColumn(id=212, project_id=20, name="Another active", workflow_stage="active"))
        db.add(ProjectColumn(id=213, project_id=20, name="Any review", workflow_stage="review"))
        db.add(ProjectColumn(id=214, project_id=20, name="Archive", workflow_stage="closed", is_archive=True))
        db.commit()
    monkeypatch.setattr(type(Env), "JWT_SECRET_KEY", property(lambda _: "fixture-workflow-http-secret-32bytes"))
    Cache.clear()
    Auth.reset_user(owner)
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    with TestClient(app) as client:
        path = f"/board/{SnowflakeID(20).to_short_code()}/apps/example/workflow-mapping"
        assert client.get(path).status_code == 401
        access, refresh = AuthSecurity.authenticate(owner.id)
        client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
        response = client.get(path, headers={"Authorization": f"Bearer {access}"})
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        initial = response.json()
        invalid = {
            "mapping": {},
            "enabled": True,
            "expected_revision": initial["revision"],
            "expected_app_revision": initial["app_revision"],
        }
        assert client.put(path, headers={"Authorization": f"Bearer {access}"}, json=invalid).status_code == 400
    Cache.clear()
    initial = read_workflow_mapping(owner, SnowflakeID(20), "example")
    assert not initial["enabled"] and not initial["transitions_resolved"]
    assert initial["choices"][0]["status"] == "ambiguous"
    assert initial["choices"][2]["status"] == "missing"

    def save(mapping, enabled, receipt=initial):
        return save_workflow_mapping(
            owner, SnowflakeID(20), "example", mapping, enabled, receipt["revision"], receipt["app_revision"]
        )

    with pytest.raises(ValueError):
        save({}, True)
    with pytest.raises(ValueError):
        save({"provider-stage": SnowflakeID(211).to_short_code()}, True)
    enabled = save({"active": SnowflakeID(211).to_short_code()}, True)
    assert enabled["enabled"] and enabled["transitions_resolved"]
    assert enabled["choices"][0]["workflow_guidance"] == "Work being executed\n\nBoard-specific instructions"
    with pytest.raises(AppGovernanceConflict):
        save({}, False)
    with Session(engine) as db:
        db.execute(update(ProjectColumn).where(ProjectColumn.id == 211).values(deleted_at=SafeDateTime.now()))
        db.commit()
    current = read_workflow_mapping(owner, SnowflakeID(20), "example")
    assert not current["enabled"]
    assert current["choices"][0]["status"] == "invalid"  # Never substitute the other active column.
    with pytest.raises(ValueError):
        save({"active": SnowflakeID(211).to_short_code()}, True, current)
    assert not save({}, False, current)["enabled"]


def test_workflow_metadata_and_column_editing_http_current_authority(registered, monkeypatch):
    engine, (admin, owner, outsider), *_ = registered
    WorkflowStageDefinition.__table__.create(engine)
    ProjectColumn.__table__.create(engine)
    ProjectRole.__table__.create(engine)
    with Session(engine) as db:
        db.add(
            WorkflowStageDefinition(id=201, key="active", name="Active", description="Executing work", is_builtin=True)
        )
        db.add(ProjectColumn(id=211, project_id=20, name="Doing"))
        db.add(ProjectColumn(id=212, project_id=20, name="Archive", is_archive=True))
        db.commit()
    monkeypatch.setattr(type(Env), "JWT_SECRET_KEY", property(lambda _: "fixture-stage-http-secret-32bytes"))
    Cache.clear()
    for user in (admin, owner, outsider):
        Auth.reset_user(user)
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    try:
        with TestClient(app) as client:

            def request(user, method, path, **kwargs):
                access, refresh = AuthSecurity.authenticate(user.id)
                client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
                return client.request(method, path, headers={"Authorization": f"Bearer {access}"}, **kwargs)

            path = "/settings/workflow-stages"
            assert client.get(path).status_code == 401
            stage = request(owner, "GET", path).json()["items"][0]
            translations = {
                "en": {"name": "Active"},
                **{
                    language: fields
                    for language, fields in json.loads(
                        (
                            Path(__file__).parents[2]
                            / "shared/py/langboard_shared/core/resources/locales/workflow_stages.json"
                        ).read_text()
                    )[0]["translations"].items()
                    if language != "en"
                },
                "fr": {"name": "Actif"},
            }
            body = {
                "name": "Active",
                "description": "Current work",
                "color": "#123456",
                "translations": translations,
                "expected_revision": stage["revision"],
            }
            assert request(owner, "PUT", path + "/active", json=body).status_code == 403
            saved = request(admin, "PUT", path + "/active", json=body)
            assert saved.status_code == 200 and saved.json()["translations"]["fr"]["name"] == "Actif"
            assert request(admin, "PUT", path + "/active", json=body).status_code == 409
            assert request(admin, "PUT", path + "/active", json={**body, "is_builtin": False}).status_code in (400, 422)
            column_path = f"/board/{SnowflakeID(20).to_short_code()}/column/{SnowflakeID(211).to_short_code()}/workflow"
            assert request(outsider, "GET", column_path).status_code == 403
            initial = request(owner, "GET", column_path).json()
            column = {
                "workflow_stage": "active",
                "description": "Board instructions",
                "translations": {"ko": {"name": "Work"}},
                "expected_revision": initial["revision"],
            }
            approved = request(owner, "PUT", column_path, json=column)
            assert approved.status_code == 200
            assert approved.json()["workflow_guidance"] == "Current work\n\nBoard instructions"
            assert request(owner, "PUT", column_path, json=column).status_code == 409
            archive_path = column_path.replace(SnowflakeID(211).to_short_code(), SnowflakeID(212).to_short_code())
            archive = request(owner, "GET", archive_path).json()
            assert (
                request(
                    owner, "PUT", archive_path, json={**column, "expected_revision": archive["revision"]}
                ).status_code
                == 400
            )
            assert (
                request(
                    owner,
                    "PUT",
                    column_path,
                    json={**column, "workflow_stage": "app_custom", "expected_revision": approved.json()["revision"]},
                ).status_code
                == 400
            )
            assert (
                request(
                    owner,
                    "PUT",
                    column_path,
                    json={
                        **column,
                        "translations": {"ko": {"script": "bad"}},
                        "expected_revision": approved.json()["revision"],
                    },
                ).status_code
                == 400
            )
            with Session(engine) as db:
                db.execute(update(User).where(User.id == admin.id).values(is_admin=False))
                db.commit()
            fresh = saved.json()
            assert (
                request(
                    admin, "PUT", path + "/active", json={**body, "expected_revision": fresh["revision"]}
                ).status_code
                == 403
            )
    finally:
        Cache.clear()


def test_column_metadata_publication_occurs_after_commit_and_not_on_rollback(registered, monkeypatch):
    engine, (_, owner, _), *_ = registered
    WorkflowStageDefinition.__table__.create(engine)
    ProjectColumn.__table__.create(engine)
    with Session(engine) as db:
        db.add(WorkflowStageDefinition(id=201, key="active", name="Active", is_builtin=True))
        db.commit()
        db.add(ProjectColumn(id=211, project_id=20, name="Doing"))
        db.commit()
    events = []

    def dispatch(payload, models):
        with Session(engine) as observer:
            column = observer.get(ProjectColumn, 211)
            assert column.description == payload["description"]
            assert column.workflow_stage == payload["workflow_stage"]
        events.append((payload, models))

    monkeypatch.setattr(ProjectColumnPublisher, "put_dispather", dispatch)
    initial = read_column_workflow(owner, SnowflakeID(20), SnowflakeID(211))
    with pytest.raises(RuntimeError):
        with DbSession.atomic():
            save_column_workflow(
                owner, SnowflakeID(20), SnowflakeID(211), "active", "Uncommitted", {}, initial["revision"]
            )
            assert events == []
            raise RuntimeError("rollback fixture")
    assert events == []
    save_column_workflow(
        owner, SnowflakeID(20), SnowflakeID(211), "active", "Committed", {"ko": {"name": "Work"}}, initial["revision"]
    )
    assert len(events) == 1
    payload, models = events[0]
    assert payload["name"] == "Doing" and payload["description"] == "Committed"
    assert payload["translations"] == {"ko": {"name": "Work"}}
    assert len(models) == 2 and all(model.topic_id == SnowflakeID(20).to_short_code() for model in models)
    assert {model.event for model in models} == {
        f"board:column:name:changed:{SnowflakeID(20).to_short_code()}",
        f"dashboard:project:column:name:changed:{SnowflakeID(20).to_short_code()}",
    }
    with pytest.raises(AppGovernanceConflict):
        save_column_workflow(owner, SnowflakeID(20), SnowflakeID(211), None, "Stale", {}, initial["revision"])
    assert len(events) == 1


@pytest.mark.parametrize("operation", ["resources", "mapping", "column"])
def test_settings_reads_hold_no_write_locks_and_allow_concurrent_updates(registered, operation):
    engine, (_, owner, _), personal, _ = registered
    AppResourceBinding.__table__.create(engine)
    WorkflowStageDefinition.__table__.create(engine)
    ProjectColumn.__table__.create(engine)
    with Session(engine) as db:
        declaration = deepcopy(DECLARATION)
        declaration["workflow_requirements"] = {"required": ["active"], "optional": []}
        db.execute(update(AppDefinition).values(declaration=declaration))
        db.add(WorkflowStageDefinition(id=201, key="active", name="Active", is_builtin=True))
        db.flush()
        db.add(ProjectColumn(id=211, project_id=20, name="Doing", workflow_stage="active"))
        db.commit()
    statements = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        if get_ident() == reader_thread:
            statements.append(statement)

    reader_thread = get_ident()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        with DbSession.atomic():
            if operation == "resources":
                assert list_resource_selections(owner, SnowflakeID(20), "example", personal.id)["items"] == []
            elif operation == "mapping":
                assert read_workflow_mapping(owner, SnowflakeID(20), "example")["choices"]
            else:
                assert read_column_workflow(owner, SnowflakeID(20), SnowflakeID(211))["workflow_stage"] == "active"
            assert not any("FOR UPDATE" in sql.upper() or "PG_ADVISORY" in sql.upper() for sql in statements)

            def writer():
                with engine.begin() as connection:
                    connection.execute(text("SET LOCAL lock_timeout = '750ms'"))
                    for model in (User, AppDefinition, ProjectColumn, AppConnection, BoardAppBinding):
                        connection.execute(update(model).values(updated_at=SafeDateTime.now()))

            with ThreadPoolExecutor(max_workers=1) as executor:
                executor.submit(writer).result(timeout=5)
    finally:
        event.remove(engine, "before_cursor_execute", capture)


def test_native_mcp_workflow_guidance_uses_explicit_stage_and_batch_lookup(registered, monkeypatch):
    engine, (_, owner, _), *_ = registered
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    WorkflowStageDefinition.__table__.create(engine)
    ProjectColumn.__table__.create(engine)
    Card.__table__.create(engine)
    with Session(engine) as db:
        db.add(WorkflowStageDefinition(id=201, key="active", name="Active", description="Stage instructions"))
        db.flush()
        db.add(
            ProjectColumn(
                id=211, project_id=20, name="Arbitrary", description="Column instructions", workflow_stage="active"
            )
        )
        db.add(ProjectColumn(id=212, project_id=20, name="Doing", description="Unmapped instructions"))
        db.add(ProjectColumn(id=213, project_id=20, name="Archive", is_archive=True))
        db.flush()
        db.add(Card(id=221, project_id=20, project_column_id=211, title="Example"))
        db.commit()
    statements = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    service = DomainService()
    adapter = NativeCardWorkspaceAdapter(owner, service)
    event.listen(engine, "before_cursor_execute", capture)
    try:
        identity = get_project_identity(adapter, SnowflakeID(20).to_short_code()).model_dump()
        columns = identity["columns"]["items"]
        mapped = next(column for column in columns if column["uid"] == SnowflakeID(211).to_short_code())
        assert mapped["workflow_guidance"] == "Stage instructions\n\nColumn instructions"
        assert mapped["workflow_stage"] == "active"
        unmapped = next(column for column in columns if column["uid"] == SnowflakeID(212).to_short_code())
        assert unmapped["workflow_stage"] is None
        assert unmapped["workflow_guidance"] == "Unmapped instructions"
        assert len([sql for sql in statements if "FROM workflow_stage_definition" in sql]) == 1
        bundle = get_card_bundle(
            adapter, SnowflakeID(20).to_short_code(), SnowflakeID(221).to_short_code(), CommentPage(), SectionPage()
        ).model_dump()
        assert bundle["card"]["workflow"]["workflow_guidance"] == mapped["workflow_guidance"]
        with Session(engine) as db:
            db.execute(update(WorkflowStageDefinition).values(description="Updated instructions", is_active=False))
            db.commit()
        refreshed = get_card_bundle(
            adapter, SnowflakeID(20).to_short_code(), SnowflakeID(221).to_short_code(), CommentPage(), SectionPage()
        ).model_dump()
        assert refreshed["card"]["workflow"]["workflow_stage_status"] == "inactive"
        assert refreshed["card"]["workflow"]["workflow_guidance"] == "Updated instructions\n\nColumn instructions"
    finally:
        event.remove(engine, "before_cursor_execute", capture)
        service.close()


def test_card_app_ownership_human_authority_cas_and_audit_retention(registered, monkeypatch):
    engine, (admin, owner, outsider), personal, _ = registered
    WorkflowStageDefinition.__table__.create(engine)
    ProjectColumn.__table__.create(engine)
    Card.__table__.create(engine)
    CardAppOwnership.__table__.create(engine)
    CardAppOwnershipAudit.__table__.create(engine)
    ProjectRole.__table__.create(engine)
    with Session(engine) as db:
        declaration = deepcopy(DECLARATION)
        declaration["capabilities"].append("cards.create")
        declaration["card_lifecycle"] = {
            "issue": {
                "archive": {"mode": "retain", "description": "Retain external state"},
                "restore": "retain_external_state",
            }
        }
        db.execute(update(AppDefinition).values(declaration=declaration))
        db.execute(update(BoardAppBinding).values(granted_capabilities=["cards.create"]))
        db.add(ProjectColumn(id=211, project_id=20, name="Backlog"))
        db.flush()
        db.add(Card(id=221, project_id=20, project_column_id=211, title="Native issue"))
        db.commit()
        definition_revision = db.query(AppDefinition).one().edit_revision()
    token = issue_connection_credential(owner, personal.id)["token"]
    assert read_card_app_ownership(owner, SnowflakeID(20), SnowflakeID(221))["revision"] is None
    with pytest.raises(AppGovernanceDenied):
        set_card_app_ownership(outsider, SnowflakeID(20), SnowflakeID(221), "example", None, definition_revision)

    def assign():
        try:
            return set_card_app_ownership(
                owner, SnowflakeID(20), SnowflakeID(221), "example", None, definition_revision
            )
        except AppGovernanceConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _: assign(), range(2)))
    assert outcomes.count("conflict") == 1
    assigned = next(outcome for outcome in outcomes if outcome != "conflict")
    assert assigned == {"app_key": "example", "revision": 1, "changed": True}
    assert not set_card_app_ownership(owner, SnowflakeID(20), SnowflakeID(221), "example", 1, definition_revision)[
        "changed"
    ]
    with pytest.raises(AppGovernanceConflict):
        set_card_app_ownership(owner, SnowflakeID(20), SnowflakeID(221), None, None)
    with Session(engine) as db:
        db.execute(update(AppDefinition).values(is_enabled=False))
        db.commit()
    cleared = set_card_app_ownership(owner, SnowflakeID(20), SnowflakeID(221), None, 1)
    assert cleared == {"app_key": None, "revision": 2, "changed": True}
    with pytest.raises(AppGovernanceDenied):
        set_card_app_ownership(owner, SnowflakeID(20), SnowflakeID(221), "example", 2, definition_revision)
    with Session(engine) as db:
        assert db.query(CardAppOwnershipAudit).count() == 2
        assert db.query(Card).one().title == "Native issue"
    monkeypatch.setattr(type(Env), "JWT_SECRET_KEY", property(lambda _: "fixture-ownership-secret-32bytes"))
    Cache.clear()
    for user in (admin, owner, outsider):
        Auth.reset_user(user)
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    try:
        with TestClient(app) as client:
            path = f"/board/{SnowflakeID(20).to_short_code()}/card/{SnowflakeID(221).to_short_code()}/app-ownership"
            assert client.get(path).status_code == 401
            access, refresh = AuthSecurity.authenticate(owner.id)
            client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
            headers = {"Authorization": f"Bearer {access}"}
            assert client.get(path, headers=headers).json()["revision"] == 2
            assert (
                client.put(path, headers=headers, json={"app_key": None, "expected_revision": 2}).json()["changed"]
                is False
            )
            assert client.put(path, headers=headers, json={"app_key": None, "expected_revision": 1}).status_code == 409
            assert client.put(
                path, headers=headers, json={"app_key": None, "expected_revision": 2, "actor_id": 1}
            ).status_code in (400, 422)
            assert (
                client.put(
                    path,
                    headers={"Authorization": f"Bearer {token}"},
                    json={"app_key": None, "expected_revision": None},
                ).status_code
                == 401
            )
    finally:
        Cache.clear()

    with Session(engine) as db:
        db.query(Card).delete()
        db.commit()
        assert db.query(CardAppOwnership).count() == 0
        assert db.query(CardAppOwnershipAudit).count() == 2


def test_card_resource_snapshots_reject_identity_ownership_drift_and_preserve_cleanup(registered, monkeypatch):
    engine, (admin, owner, outsider), personal, _ = registered
    for model in (
        WorkflowStageDefinition,
        ProjectColumn,
        Card,
        CardAppOwnership,
        CardAppOwnershipAudit,
        AppResourceBinding,
        CardAppResourceSelection,
        CardAppResourceSelectionAudit,
        ProjectRole,
    ):
        model.__table__.create(engine)
    with Session(engine) as db:
        declaration = deepcopy(DECLARATION)
        declaration["capabilities"] += ["cards.create", "resources.read"]
        declaration["card_lifecycle"] = {
            "issue": {"archive": {"mode": "retain", "description": "Retain"}, "restore": "retain_external_state"}
        }
        db.execute(update(AppDefinition).values(declaration=declaration))
        db.execute(update(BoardAppBinding).values(granted_capabilities=declaration["capabilities"]))
        db.add(ProjectColumn(id=211, project_id=20, name="Backlog"))
        db.flush()
        db.add(Card(id=221, project_id=20, project_column_id=211, title="Example"))
        db.commit()
        app_revision = db.query(AppDefinition).one().edit_revision()
        binding_revision = db.query(BoardAppBinding).one().edit_revision()
    set_card_app_ownership(owner, SnowflakeID(20), SnowflakeID(221), "example", None, app_revision)
    initial = read_card_app_resources(owner, SnowflakeID(20), SnowflakeID(221), "example", personal.id)
    with Session(engine) as db:
        db.add(
            AppResourceBinding(
                id=301,
                board_binding_id=80,
                connection_id=personal.id,
                resource_type="issue",
                external_resource_id="one",
                is_selected=True,
                access_state="granted",
                identity_hash=initial["identity_revision"],
            )
        )
        db.add(
            AppResourceBinding(
                id=302,
                board_binding_id=80,
                connection_id=personal.id,
                resource_type="issue",
                external_resource_id="two",
                is_selected=True,
                access_state="granted",
                identity_hash=initial["identity_revision"],
            )
        )
        db.commit()
    resources = [SnowflakeID(301).to_short_code(), SnowflakeID(302).to_short_code()]

    def save(actor=owner, revision=None, ownership=1, identity=None, selected=None):
        return set_card_app_resources(
            actor,
            SnowflakeID(20),
            SnowflakeID(221),
            "example",
            personal.id,
            resources if selected is None else selected,
            revision,
            ownership,
            identity or initial["identity_revision"],
            app_revision,
            binding_revision,
        )

    with pytest.raises(AppGovernanceDenied):
        save(actor=outsider)
    with pytest.raises(AppGovernanceDenied):
        read_card_app_resources(admin, SnowflakeID(20), SnowflakeID(221), "example", personal.id)
    with pytest.raises(ValueError):
        save(selected=resources + resources)
    selected = save()
    assert selected["resource_uids"] == sorted(resources) and selected["snapshot_current"]
    assert not save(revision=1)["changed"]
    monkeypatch.setattr(type(Env), "JWT_SECRET_KEY", property(lambda _: "fixture-card-resource-secret-32bytes"))
    Cache.clear()
    Auth.reset_user(owner)
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    try:
        with TestClient(app) as client:
            path = (
                f"/board/{SnowflakeID(20).to_short_code()}/card/{SnowflakeID(221).to_short_code()}"
                f"/apps/example/connections/{personal.get_uid()}/resources"
            )
            assert client.get(path).status_code == 401
            access, refresh = AuthSecurity.authenticate(owner.id)
            client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
            headers = {"Authorization": f"Bearer {access}"}
            receipt = client.get(path, headers=headers)
            assert receipt.status_code == 200 and receipt.headers["cache-control"] == "no-store"
            body = {
                "resource_uids": resources,
                "expected_revision": 1,
                "expected_ownership_revision": receipt.json()["ownership_revision"],
                "expected_identity_revision": receipt.json()["identity_revision"],
                "expected_app_revision": receipt.json()["app_revision"],
                "expected_binding_revision": receipt.json()["binding_revision"],
            }
            assert client.put(path, headers=headers, json=body).json()["changed"] is False
            assert client.put(path, headers=headers, json={**body, "access_state": "granted"}).status_code in (400, 422)
            assert client.put(path, headers=headers, json={**body, "expected_revision": 2}).status_code == 409
            token = issue_connection_credential(owner, personal.id)["token"]
            assert client.put(path, headers={"Authorization": f"Bearer {token}"}, json=body).status_code == 401
    finally:
        Cache.clear()
    with pytest.raises(AppGovernanceConflict):
        save()
    set_card_app_ownership(owner, SnowflakeID(20), SnowflakeID(221), None, 1)
    set_card_app_ownership(owner, SnowflakeID(20), SnowflakeID(221), "example", 2, app_revision)
    assert not read_card_app_resources(owner, SnowflakeID(20), SnowflakeID(221), "example", personal.id)[
        "snapshot_current"
    ]
    with pytest.raises(AppGovernanceConflict):
        save(revision=1)
    assert save(revision=1, ownership=3)["revision"] == 2
    with Session(engine) as db:
        db.execute(
            update(AppConnection)
            .where(AppConnection.id == personal.id)
            .values(updated_at=SafeDateTime.now() + timedelta(seconds=1))
        )
        db.commit()
    current = read_card_app_resources(owner, SnowflakeID(20), SnowflakeID(221), "example", personal.id)
    assert not current["snapshot_current"]
    with pytest.raises(AppGovernanceConflict):
        save(revision=2, ownership=3)
    with pytest.raises(AppGovernanceDenied):
        save(revision=2, ownership=3, identity=current["identity_revision"])
    with Session(engine) as db:
        db.execute(update(AppConnection).where(AppConnection.id == personal.id).values(state="disconnected"))
        db.execute(update(AppDefinition).values(is_enabled=False))
        db.commit()
    cleared = save(revision=2, selected=[])
    assert cleared["revision"] == 3 and cleared["resource_uids"] == []
    with Session(engine) as db:
        assert db.query(CardAppResourceSelectionAudit).count() == 3
        db.query(Card).delete()
        db.commit()
        assert db.query(CardAppResourceSelection).count() == 0
        assert db.query(CardAppResourceSelectionAudit).count() == 3


def test_human_execution_intent_cas_idempotency_and_current_resource_fences(registered, monkeypatch):
    engine, (_, owner, outsider), personal, shared = registered
    from langboard_shared.domain.models import AppExecutionLease

    for model in (
        AppExecutionLease,
        WorkflowStageDefinition,
        ProjectColumn,
        Card,
        GlobalCardRelationshipType,
        CardRelationship,
        CardAppOwnership,
        CardAppOwnershipAudit,
        AppResourceBinding,
        CardAppResourceSelection,
        CardAppResourceSelectionAudit,
        CardExecutionGeneration,
        CardExecutionIntent,
        AppExecutionOutbox,
        AppExecutionAcknowledgment,
        ProjectRole,
    ):
        model.__table__.create(engine)
    with Session(engine) as db:
        declaration = deepcopy(DECLARATION)
        declaration["capabilities"] += ["cards.create", "resources.read", "execution.run"]
        declaration["card_lifecycle"] = {
            "issue": {"archive": {"mode": "retain", "description": "Retain"}, "restore": "retain_external_state"}
        }
        db.execute(update(AppDefinition).values(declaration=declaration))
        db.execute(
            update(BoardAppBinding).values(
                granted_capabilities=declaration["capabilities"],
                stage_transitions_enabled=True,
                workflow_mapping={"ready": SnowflakeID(211).to_short_code()},
            )
        )
        db.add(WorkflowStageDefinition(id=201, key="ready", name="Ready", is_builtin=True))
        db.flush()
        db.add(ProjectColumn(id=211, project_id=20, name="Queue", workflow_stage="ready"))
        db.flush()
        db.add(Card(id=221, project_id=20, project_column_id=211, title="Example"))
        db.commit()
        app_revision = db.query(AppDefinition).one().edit_revision()
        binding_revision = db.query(BoardAppBinding).one().edit_revision()
    set_card_app_ownership(owner, SnowflakeID(20), SnowflakeID(221), "example", None, app_revision)
    initial = read_card_app_resources(owner, SnowflakeID(20), SnowflakeID(221), "example", personal.id)
    with Session(engine) as db:
        db.add(
            AppResourceBinding(
                id=301,
                board_binding_id=80,
                connection_id=personal.id,
                resource_type="issue",
                external_resource_id="one",
                is_selected=True,
                access_state="granted",
                identity_hash=initial["identity_revision"],
            )
        )
        db.commit()
    set_card_app_resources(
        owner,
        SnowflakeID(20),
        SnowflakeID(221),
        "example",
        personal.id,
        [SnowflakeID(301).to_short_code()],
        None,
        1,
        initial["identity_revision"],
        app_revision,
        binding_revision,
    )
    args = (owner, SnowflakeID(20), SnowflakeID(221), "example", personal.id)
    observed = read_execution_intent_context(*args)
    assert observed["generation"] == 0 and observed["started"] is False
    snapshot = {key: value for key, value in observed.items() if key not in {"generation", "started"}}
    with Session(engine) as db:
        db.add(
            WorkflowStageDefinition(
                id=202, key="completed", name="Completed", is_builtin=True, counts_as_completed=True
            )
        )
        db.flush()
        db.add(ProjectColumn(id=212, project_id=20, name="Finished", workflow_stage="completed"))
        db.flush()
        db.add(Card(id=222, project_id=20, project_column_id=211, title="Sensitive prerequisite"))
        db.add_all(
            [
                GlobalCardRelationshipType(
                    id=501, parent_name="Unfinished plan", child_name="Member", machine_semantic="contains"
                ),
                GlobalCardRelationshipType(
                    id=502, parent_name="Context", child_name="Related", machine_semantic="references"
                ),
                GlobalCardRelationshipType(
                    id=503,
                    parent_name="Renamed prerequisite",
                    child_name="Dependent",
                    machine_semantic="blocks",
                    affects_readiness=True,
                ),
            ]
        )
        db.flush()
        db.add(CardRelationship(id=601, card_id_parent=222, card_id_child=221, relationship_type_id=501))
        db.commit()
    assert read_execution_intent_context(*args)["dependency_revision"] == observed["dependency_revision"]
    with Session(engine) as db:
        db.execute(update(CardRelationship).values(relationship_type_id=502))
        db.commit()
    assert read_execution_intent_context(*args)["dependency_revision"] == observed["dependency_revision"]
    with Session(engine) as db:
        db.execute(update(CardRelationship).values(relationship_type_id=503))
        db.commit()
    with DbSession.atomic() as db:
        blocked = card_dependency_state(db, SnowflakeID(20), SnowflakeID(221))
    assert blocked["state"] == "blocked" and blocked["direct_blocker_count"] == 1
    assert "Sensitive" not in json.dumps(blocked) and "prerequisite_id" not in blocked
    with pytest.raises(AppGovernanceDenied):
        read_execution_intent_context(*args)
    with pytest.raises(AppGovernanceDenied):
        request_card_execution_intent(*args, "blocked-request-0001", 0, snapshot)
    with Session(engine) as db:
        assert db.query(CardExecutionIntent).count() == 0
        db.execute(update(Card).where(Card.id == 222).values(project_column_id=212))
        db.commit()
    observed = read_execution_intent_context(*args)
    snapshot = {key: value for key, value in observed.items() if key not in {"generation", "started"}}
    with Session(engine) as db:
        db.execute(update(Card).where(Card.id == 222).values(archived_at=SafeDateTime.now()))
        db.commit()
    with pytest.raises(AppGovernanceDenied):
        read_execution_intent_context(*args)
    with Session(engine) as db:
        db.execute(update(Card).where(Card.id == 222).values(archived_at=None))
        db.commit()
    with Session(engine) as db:
        db.execute(update(WorkflowStageDefinition).where(WorkflowStageDefinition.id == 202).values(is_active=False))
        db.commit()
    with pytest.raises(AppGovernanceDenied):
        read_execution_intent_context(*args)
    with Session(engine) as db:
        db.execute(update(WorkflowStageDefinition).where(WorkflowStageDefinition.id == 202).values(is_active=True))
        db.execute(update(Card).where(Card.id == 222).values(deleted_at=SafeDateTime.now()))
        db.commit()
    with pytest.raises(AppGovernanceDenied):
        read_execution_intent_context(*args)
    with Session(engine) as db:
        db.execute(update(Card).where(Card.id == 222).values(deleted_at=None))
        db.commit()
    with pytest.raises(AppGovernanceConflict):
        request_card_execution_intent(*args, "stale-dependency-0001", 0, snapshot)
    observed = read_execution_intent_context(*args)
    snapshot = {key: value for key, value in observed.items() if key not in {"generation", "started"}}
    key = "execution-request-0001"
    with pytest.raises(AppGovernanceDenied):
        request_card_execution_intent(outsider, *args[1:], key, 0, snapshot)

    def submit():
        return request_card_execution_intent(*args, key, 0, snapshot)

    with ThreadPoolExecutor(max_workers=2) as executor:
        receipts = list(executor.map(lambda _: submit(), range(2)))
    assert sum(receipt["changed"] for receipt in receipts) == 1
    assert receipts[0]["request_uid"] == receipts[1]["request_uid"]
    assert all(
        receipt["generation"] == 1 and not receipt["started"] and not receipt["execution_resumed"]
        for receipt in receipts
    )
    original_update = DbSession.update

    def fail_event_update(db, model, *update_args, **update_kwargs):
        if isinstance(model, AppExecutionOutbox):
            raise RuntimeError("Outbox persistence failed")
        return original_update(db, model, *update_args, **update_kwargs)

    with monkeypatch.context() as failure_patch:
        failure_patch.setattr(DbSession, "update", fail_event_update)
        with pytest.raises(RuntimeError, match="Outbox persistence failed"):
            request_card_execution_intent(*args, "atomic-outbox-failure", 1, snapshot)
    with Session(engine) as db:
        assert db.query(CardExecutionGeneration).one().execution_generation == 1
        assert db.query(CardExecutionIntent).count() == 1
        assert db.query(AppExecutionOutbox).count() == 1
    with pytest.raises(AppGovernanceConflict):
        request_card_execution_intent(*args, key, 0, {**snapshot, "selection_revision": 999})
    with pytest.raises(AppGovernanceConflict):
        request_card_execution_intent(*args, "execution-request-0002", 0, snapshot)
    with Session(engine) as db:
        db.execute(update(AppResourceBinding).values(access_state="revoked"))
        db.commit()
    with pytest.raises(AppGovernanceDenied):
        submit()
    with Session(engine) as db:
        assert db.query(CardExecutionGeneration).one().execution_generation == 1
        assert db.query(CardExecutionIntent).count() == 1
        outbox = db.query(AppExecutionOutbox).one()
        assert outbox.request_id == SnowflakeID.from_short_code(receipts[0]["request_uid"])
        assert outbox.payload["event_uid"] == outbox.get_uid()
        assert outbox.payload["generation"] == 1 and outbox.payload["started"] is False
        assert outbox.payload["resource_uids"] == [SnowflakeID(301).to_short_code()]
        assert outbox.state == "pending"
        event_uid = outbox.get_uid()
        db.execute(update(AppResourceBinding).values(access_state="granted"))
        db.commit()
    monkeypatch.setattr(type(Env), "JWT_SECRET_KEY", property(lambda _: "fixture-intent-secret-32bytes"))
    Cache.clear()
    Auth.reset_user(owner)
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    try:
        with TestClient(app) as client:
            path = (
                f"/board/{SnowflakeID(20).to_short_code()}/card/{SnowflakeID(221).to_short_code()}"
                f"/apps/example/connections/{personal.get_uid()}/execution-intent"
            )
            assert client.get(path).status_code == 401
            access, refresh = AuthSecurity.authenticate(owner.id)
            client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
            headers = {"Authorization": f"Bearer {access}"}
            assert client.get(path, headers=headers).json()["generation"] == 1
            body = {"idempotency_key": key, "expected_generation": 0, "expected_snapshot": snapshot}
            assert client.post(path, headers=headers, json=body).json()["changed"] is False
            assert client.post(path, headers=headers, json={**body, "started": True}).status_code in (400, 422)
            token = issue_connection_credential(owner, personal.id)["token"]
            assert client.post(path, headers={"Authorization": f"Bearer {token}"}, json=body).status_code == 401

            async def sdk_read():
                async with AsyncClient(
                    transport=ASGITransport(app=app),
                    base_url="http://testserver",
                    headers={"Authorization": f"Bearer {token}"},
                ) as session:
                    return await AppExecution(HttpTransport(session)).read_request(
                        SnowflakeID(20).to_short_code(),
                        SnowflakeID(221).to_short_code(),
                        request_uid=receipts[0]["request_uid"],
                    )

            app_receipt = asyncio.run(sdk_read())
            assert app_receipt["state"] == "requested" and app_receipt["started"] is False
            assert app_receipt["authority"]["generation"] == 1 and app_receipt["authority"]["ownership_revision"] == 1
            app_path = f"/apps/v1/boards/{SnowflakeID(20).to_short_code()}/cards/{SnowflakeID(221).to_short_code()}/execution-requests/{receipts[0]['request_uid']}"
            assert client.get(app_path).status_code == 401
            assert client.get(app_path, headers=headers).status_code == 403
            assert (
                client.get(app_path, headers={"Authorization": f"Bearer {token}"}).headers["cache-control"]
                == "no-store"
            )
            other_token = issue_connection_credential(owner, shared.id)["token"]
            assert client.get(app_path, headers={"Authorization": f"Bearer {other_token}"}).status_code == 403
            ack_path = app_path + "/acknowledgments"
            ack_body = {"event_uid": event_uid, "runtime_reference": "runtime-example-1"}
            app_headers = {"Authorization": f"Bearer {token}"}
            assert client.get(ack_path, headers=app_headers).json()["state"] == "not_received"
            assert client.get(ack_path).status_code == 401
            assert client.get(ack_path, headers=headers).status_code == 403
            assert client.get(ack_path, headers={"Authorization": f"Bearer {other_token}"}).status_code == 403
            assert client.post(ack_path, headers=app_headers, json=ack_body).status_code == 403
            with Session(engine) as db:
                assert db.query(AppExecutionAcknowledgment).count() == 0
                db.execute(update(AppExecutionOutbox).values(state="delivering"))
                db.commit()
            with Session(engine) as db:
                delivered_payload = deepcopy(db.query(AppExecutionOutbox).one().payload)
                db.execute(update(AppExecutionOutbox).values(payload={**delivered_payload, "generation": 999}))
                db.commit()
            assert client.post(ack_path, headers=app_headers, json=ack_body).status_code == 403
            with Session(engine) as db:
                db.execute(update(AppExecutionOutbox).values(payload=delivered_payload))
                db.commit()
            assert client.post(ack_path, json=ack_body).status_code == 401
            assert client.post(ack_path, headers=headers, json=ack_body).status_code == 403
            assert (
                client.post(ack_path, headers={"Authorization": f"Bearer {other_token}"}, json=ack_body).status_code
                == 403
            )
            assert (
                client.post(
                    ack_path, headers=app_headers, json={**ack_body, "event_uid": SnowflakeID(999).to_short_code()}
                ).status_code
                == 403
            )

            async def sdk_ack():
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://testserver", headers=app_headers
                ) as session:
                    return await AppExecution(HttpTransport(session)).acknowledge(
                        SnowflakeID(20).to_short_code(),
                        SnowflakeID(221).to_short_code(),
                        request_uid=receipts[0]["request_uid"],
                        event_uid=event_uid,
                        runtime_reference="runtime-example-1",
                    )

            async def sdk_ack_read():
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://testserver", headers=app_headers
                ) as session:
                    return await AppExecution(HttpTransport(session)).read_acknowledgment(
                        SnowflakeID(20).to_short_code(),
                        SnowflakeID(221).to_short_code(),
                        request_uid=receipts[0]["request_uid"],
                    )

            assert asyncio.run(sdk_ack_read())["state"] == "not_received"
            original_insert = DbSession.insert

            def fail_ack_insert(db, model):
                if isinstance(model, AppExecutionAcknowledgment):
                    raise RuntimeError("Acknowledgment persistence failed")
                return original_insert(db, model)

            monkeypatch.setattr(DbSession, "insert", fail_ack_insert)
            with pytest.raises(MutationOutcomeUnknown):
                asyncio.run(sdk_ack())
            monkeypatch.setattr(DbSession, "insert", original_insert)
            with Session(engine) as db:
                assert db.query(AppExecutionAcknowledgment).count() == 0
                assert db.query(CardExecutionGeneration).one().execution_generation == 1
            assert client.get(ack_path, headers=app_headers).json()["state"] == "not_received"
            with ThreadPoolExecutor(max_workers=2) as pool:
                acknowledgments = list(pool.map(lambda _: asyncio.run(sdk_ack()), range(2)))
            assert sorted(row["changed"] for row in acknowledgments) == [False, True]
            assert len({row["acknowledgment_uid"] for row in acknowledgments}) == 1
            assert all(row["state"] == "received" and row["started"] is False for row in acknowledgments)
            recovered_ack = client.get(ack_path, headers=app_headers)
            assert recovered_ack.headers["cache-control"] == "no-store"
            assert recovered_ack.json()["acknowledgment_uid"] == acknowledgments[0]["acknowledgment_uid"]
            assert recovered_ack.json()["changed"] is False
            assert recovered_ack.json()["started"] is False
            assert asyncio.run(sdk_ack_read()) == recovered_ack.json()
            assert client.post(ack_path, headers=app_headers, json=ack_body).headers["cache-control"] == "no-store"
            assert (
                client.post(
                    ack_path, headers=app_headers, json={**ack_body, "runtime_reference": "runtime-example-2"}
                ).status_code
                == 409
            )
            runtime_token = "a" * 64

            async def sdk_permit():
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://testserver", headers=app_headers
                ) as session:
                    return await AppExecution(HttpTransport(session)).authorize_runtime(
                        SnowflakeID(20).to_short_code(),
                        SnowflakeID(221).to_short_code(),
                        request_uid=receipts[0]["request_uid"],
                        acknowledgment_uid=acknowledgments[0]["acknowledgment_uid"],
                        runtime_token=runtime_token,
                    )

            with ThreadPoolExecutor(max_workers=2) as pool:
                permits = list(pool.map(lambda _: asyncio.run(sdk_permit()), range(2)))
            assert sorted(row["changed"] for row in permits) == [False, True]
            permit = permits[0]
            assert permit["state"] == "authorized" and permit["permit_execution"] and not permit["started"]
            assert len({row["lease_uid"] for row in permits}) == 1
            recovery_path = f"/apps/v1/execution-requests/{receipts[0]['request_uid']}/runtime-permit-recovery"
            check_path = f"/apps/v1/runtime-permits/{permit['lease_uid']}/check"
            recovered = client.post(recovery_path, json={"runtime_token": runtime_token})
            assert recovered.status_code == 200 and recovered.json()["expires_at"] == permit["expires_at"]
            assert client.post(check_path, json={"runtime_token": "b" * 64}).status_code == 403
            assert client.post(check_path, json={"runtime_token": runtime_token, "stopped": "yes"}).status_code in (
                400,
                422,
            )
            heartbeat = client.post(check_path, json={"runtime_token": runtime_token})
            assert heartbeat.status_code == 200 and heartbeat.json()["expires_at"] > permit["expires_at"]
            with Session(engine) as db:
                lease = db.query(AppExecutionLease).one()
                assert lease.runtime_token_hash == sha256(runtime_token.encode()).hexdigest()
                assert runtime_token not in json.dumps(lease.history)
            with Session(engine) as db:
                db.execute(
                    update(AppConnectionCredential)
                    .where(AppConnectionCredential.connection_id == personal.id)
                    .values(revoked_at=SafeDateTime.now())
                )
                db.commit()
            assert client.post(ack_path, headers=app_headers, json=ack_body).status_code == 403
            assert client.get(ack_path, headers=app_headers).status_code == 403
            refused = client.post(check_path, json={"runtime_token": runtime_token}).json()
            assert refused["state"] == "stop_requested" and refused["permit_execution"] is False
            assert client.post(recovery_path, json={"runtime_token": runtime_token}).json()["state"] == "stop_requested"
            stopped = client.post(check_path, json={"runtime_token": runtime_token, "stopped": True}).json()
            assert stopped["state"] == "stopped" and not stopped["permit_execution"]

            with Session(engine) as db:
                db.execute(
                    update(AppConnectionCredential)
                    .where(AppConnectionCredential.connection_id == personal.id)
                    .values(revoked_at=None)
                )
                db.commit()
            assert client.post(check_path, json={"runtime_token": runtime_token}).json()["state"] == "stopped"
            with Session(engine) as db:
                # Test-only restored lease to isolate expiry from credential revocation.
                db.execute(
                    update(AppExecutionLease).values(
                        state="authorized", expires_at=SafeDateTime.now() - timedelta(seconds=1)
                    )
                )
                db.commit()
            expired = client.post(recovery_path, json={"runtime_token": runtime_token}).json()
            assert expired["state"] == "stop_requested" and not expired["permit_execution"]
            assert (
                client.post(check_path, json={"runtime_token": runtime_token}).json()["expires_at"]
                == expired["expires_at"]
            )
            assert (
                client.post(check_path, json={"runtime_token": runtime_token, "stopped": True}).json()["state"]
                == "stopped"
            )
            with pytest.raises(NativeApiError) as refusal:
                asyncio.run(sdk_permit())
            assert refusal.value.status_code == 409
            assert client.post(recovery_path, json={"runtime_token": runtime_token}).json()["state"] == "stopped"
            with ThreadPoolExecutor(max_workers=1) as pool, Session(engine) as revoker:
                credential = revoker.execute(
                    select(AppConnectionCredential)
                    .where(AppConnectionCredential.connection_id == personal.id)
                    .with_for_update()
                ).scalar_one()
                revoker.execute(text("SET LOCAL lock_timeout='5s'"))
                future = pool.submit(lambda: client.post(ack_path, headers=app_headers, json=ack_body))
                deadline = monotonic() + 5
                waiting = False
                while monotonic() < deadline:
                    revoker.execute(text("SELECT pg_stat_clear_snapshot()"))
                    waiting = revoker.execute(
                        text(
                            "SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE wait_event_type='Lock' AND query LIKE '%app_connection_credential%' AND pid<>pg_backend_pid())"
                        )
                    ).scalar()
                    if waiting:
                        break
                    sleep(0.01)
                revoker.execute(
                    update(AppConnectionCredential)
                    .where(AppConnectionCredential.id == credential.id)
                    .values(revoked_at=SafeDateTime.now())
                )
                revoker.commit()
                assert waiting
                assert future.result(timeout=5).status_code == 403
            with Session(engine) as db:
                db.execute(
                    update(AppConnectionCredential)
                    .where(AppConnectionCredential.connection_id == personal.id)
                    .values(revoked_at=None)
                )
                db.commit()
            with Session(engine) as db:
                db.execute(update(CardExecutionGeneration).values(execution_generation=2))
                db.commit()
            assert client.get(app_path, headers={"Authorization": f"Bearer {token}"}).status_code == 403
            assert client.post(ack_path, headers=app_headers, json=ack_body).status_code == 403
            with Session(engine) as db:
                db.execute(update(CardExecutionGeneration).values(execution_generation=1))
                db.execute(update(User).where(User.id == owner.id).values(activated_at=None))
                db.commit()
            assert client.get(app_path, headers={"Authorization": f"Bearer {token}"}).status_code == 403
            with Session(engine) as db:
                db.execute(update(User).where(User.id == owner.id).values(activated_at=SafeDateTime.now()))
                db.commit()
            receipt_path = path + "/" + receipts[0]["request_uid"]
            assert client.get(receipt_path, headers=headers).json()["snapshot"] == snapshot
            with Session(engine) as db:
                db.execute(update(AppDefinition).values(is_enabled=False))
                db.execute(update(WorkflowStageDefinition).values(is_active=False))
                db.commit()
            retained = client.get(receipt_path, headers=headers)
            assert retained.status_code == 200 and retained.json()["execution_resumed"] is False
            assert client.get(app_path, headers={"Authorization": f"Bearer {token}"}).status_code == 403
            assert client.post(path, headers=headers, json=body).status_code == 403
    finally:
        Cache.clear()
    with Session(engine) as db:
        db.query(Card).delete()
        db.commit()
        assert db.query(CardExecutionGeneration).count() == 0
        assert db.query(CardExecutionIntent).count() == 1
        assert db.query(AppExecutionOutbox).count() == 1
        assert db.query(AppExecutionAcknowledgment).count() == 1


def test_relationship_replacement_atomic_blocks_cycles_and_concurrent_writers(request, monkeypatch):
    engine, (_, owner, _), _, project, *_ = request.getfixturevalue("governance")
    for model in (WorkflowStageDefinition, ProjectColumn, Card, GlobalCardRelationshipType, CardRelationship):
        model.__table__.create(engine)
    events = []
    monkeypatch.setattr(CardRelationshipPublisher, "updated", lambda *args: events.append("published"))
    monkeypatch.setattr(CardRelationshipActivityTask, "card_relationship_updated", lambda *args: None)
    monkeypatch.setattr(CardBotTask, "card_relationship_updated", lambda *args: None)
    with Session(engine) as db:
        db.add(ProjectColumn(id=211, project_id=project.id, name="Queue"))
        db.flush()
        db.add_all(
            [Card(id=i, project_id=project.id, project_column_id=211, title=f"Card {i}") for i in (221, 222, 223)]
        )
        db.add_all(
            [
                GlobalCardRelationshipType(
                    id=501, parent_name="Plan", child_name="Member", machine_semantic="contains"
                ),
                GlobalCardRelationshipType(
                    id=502, parent_name="Context", child_name="Related", machine_semantic="references"
                ),
                GlobalCardRelationshipType(
                    id=503,
                    parent_name="Custom prerequisite",
                    child_name="Dependent",
                    machine_semantic="blocks",
                    affects_readiness=True,
                ),
            ]
        )
        db.commit()

    def replace(card_id, related_id, type_id):
        return DomainService().card_relationship.update(
            owner,
            project.id,
            SnowflakeID(card_id),
            False,
            [(SnowflakeID(related_id).to_short_code(), SnowflakeID(type_id).to_short_code())],
        )

    replace(221, 222, 503)
    replace(222, 223, 503)
    assert len(events) == 2
    with pytest.raises(ValueError, match="blocks cycle"):
        replace(223, 221, 503)
    with Session(engine) as db:
        assert db.query(CardRelationship).count() == 2
    with pytest.raises(ValueError, match="unavailable"):
        replace(221, 999, 503)
    with Session(engine) as db:
        assert db.query(CardRelationship).filter_by(card_id_parent=221, card_id_child=222).count() == 1
    replace(223, 221, 501)
    replace(222, 221, 502)
    with DbSession.atomic():
        result = replace(223, 222, 501)
        assert len(events) == 4
        assert any(row["machine_semantic"] == "contains" for row in result)
    assert len(events) == 5
    with pytest.raises(RuntimeError, match="rollback"):
        with DbSession.atomic():
            replace(223, 221, 501)
            raise RuntimeError("rollback")
    assert len(events) == 5
    with Session(engine) as db:
        db.query(CardRelationship).delete()
        db.commit()

    def concurrent(pair):
        try:
            replace(*pair, 503)
            return "saved"
        except ValueError:
            return "cycle"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(concurrent, [(221, 222), (222, 221)]))
    assert sorted(outcomes) == ["cycle", "saved"]
    with Session(engine) as db:
        assert db.query(CardRelationship).count() == 1
