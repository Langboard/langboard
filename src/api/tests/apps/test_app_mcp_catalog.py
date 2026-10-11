# ruff: noqa: F811
"""Current MCP declarations require independent consent and owning-card scope."""

import asyncio
from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
from langboard.routes.settings import (
    AppMcpCatalogApi,  # noqa: F401
    AppMcpConsentApi,  # noqa: F401
)
from langboard_sdk import AppMcp, HttpTransport
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.routing import AppRouter
from langboard_shared.domain.models import AppResourceBinding, BoardAppBinding
from langboard_shared.domain.services.AppConnectionAuthentication import issue_connection_credential
from langboard_shared.domain.services.AppMcpConnectionConsent import (
    read_mcp_connection_consent,
    save_mcp_connection_consent,
)
from langboard_shared.domain.services.AppRegistry import save_definition
from langboard_shared.domain.services.factory.WorkflowStageService_app_test import board  # noqa: F401
from test_app_execution_grant import grant_scope


@pytest.mark.parametrize("board", ["sqlite-http", "postgresql-test"], indirect=True)
def test_catalog_native_http_consent_update_revocation_and_sdk(board, monkeypatch):
    connection, definition, binding, resources, card, token = grant_scope(board)
    import importlib
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from langboard_shared.core.db.DbEngine import DbEngine
    monkeypatch.setattr(DbEngine, "get_readonly_engine", DbEngine.get_main_engine)
    migration = importlib.import_module("langboard.migrations.versions.20261011230000-c97659de63f4")
    original = migration.op
    try:
        with DbEngine.get_main_engine().begin() as db:
            migration.op = Operations(MigrationContext.configure(db))
            migration.upgrade()
    finally:
        migration.op = original
    with DbSession.atomic() as db:
        definition.declaration = {**definition.declaration, "capabilities": [*definition.declaration["capabilities"],
                                  "cards.read", "mcp.read", "mcp.write"],
                                 "mcp": {"identity": "example-erp", "tools": [
                                     {"name": "read_issue", "description": "Read issue", "authority_scope": "read"},
                                     {"name": "write_issue", "description": "Write issue", "authority_scope": "write"}]}}
        binding.granted_capabilities = [*binding.granted_capabilities, "cards.read"]
        board[1].is_admin = True
        db.update(board[1])
        db.update(definition)
        db.update(binding)
    # Adding an MCP identity changes the trust fingerprint; old credentials
    # correctly fail until the owner explicitly obtains a current credential.
    old_token = token
    token = issue_connection_credential(board[1], connection.id)["token"]
    from langboard_shared.publishers import AppSettingPublisher
    monkeypatch.setattr(AppSettingPublisher, "apps_changed", lambda: None)
    app = FastAPI()
    app.include_router(AppRouter.api)
    path = f"/apps/v1/boards/{board[2].get_uid()}/cards/{card.get_uid()}/mcp-tools"
    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(app) as client:
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"Authorization": f"Bearer {old_token}"}).status_code == 401
        assert client.get(path, headers=headers).json()["tools"] == []  # Card/read consent does not grant MCP.
        with DbSession.atomic() as db:
            binding.granted_capabilities = [*binding.granted_capabilities, "mcp.read", "mcp.write"]
            db.update(binding)
        assert client.get(path, headers=headers).json()["tools"] == []  # Board consent cannot approve a private connection.
        consent = read_mcp_connection_consent(board[1], connection.id)
        assert consent["available_tools"] == {"read": ["read_issue"], "write": ["write_issue"]}
        assert consent["granted_tools"] == {}
        save_mcp_connection_consent(board[1], connection.id, ["read", "write"], consent["revision"], consent["identity_revision"], consent["app_revision"])
        response = client.get(path, headers=headers)
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        result = response.json()
        assert result["execution_supported"] is False
        assert {tool["qualified_name"] for tool in result["tools"]} == {
            f"{connection.app_key}:{connection.get_uid()}:read_issue", f"{connection.app_key}:{connection.get_uid()}:write_issue"}
        assert all(tool["callable"] is False for tool in result["tools"])
        # User transport is independent from the app token. An unrelated
        # instance administrator cannot approve another person's connection.
        from langboard_shared.core.security import AuthSecurity
        from langboard_shared.core.types import SafeDateTime
        from langboard_shared.domain.models import User
        from langboard_shared.Env import Env
        with DbSession.atomic() as db:
            outsider = User(firstname="Other", lastname="Admin", email="other-admin@example.invalid",
                            password="test", activated_at=SafeDateTime.now(), is_admin=True)
            db.insert(outsider)
        user_app = FastAPI()
        user_app.include_router(AppRouter.api)
        user_app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
        management_path = f"/settings/apps/connections/{connection.get_uid()}/mcp-consent"
        for actor, status in ((outsider, 403), (board[1], 200)):
            access, refresh = AuthSecurity.authenticate(actor.id)
            with TestClient(user_app) as human:
                human.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
                assert human.get(management_path, headers={"Authorization": f"Bearer {access}"}).status_code == status
                current_consent = read_mcp_connection_consent(board[1], connection.id)
                body = {"scopes": ["read", "write"], "expected_revision": current_consent["revision"],
                        "expected_identity_revision": current_consent["identity_revision"],
                        "expected_app_revision": current_consent["app_revision"]}
                assert human.put(management_path, json=body, headers={"Authorization": f"Bearer {access}"}).status_code == status
                if status == 200:
                    denied = human.put(management_path, json={**body, "expected_app_revision": "0" * 64},
                                       headers={"Authorization": f"Bearer {access}"})
                    assert denied.status_code == 409
                    revoked = human.put(management_path, json={**body, "scopes": []}, headers={"Authorization": f"Bearer {access}"})
                    assert revoked.status_code == 200 and revoked.json()["scopes"] == []
                    assert client.get(path, headers=headers).json()["tools"] == []
                    fresh = read_mcp_connection_consent(board[1], connection.id)
                    save_mcp_connection_consent(board[1], connection.id, ["read", "write"], fresh["revision"],
                                               fresh["identity_revision"], fresh["app_revision"])
        assert client.get(path.replace(card.get_uid(), board[2].get_uid()), headers=headers).status_code == 403
        async def request(method, route, **kwargs):
            return client.request(method, route, headers=headers, **kwargs)
        sdk = AppMcp(HttpTransport(SimpleNamespace(request=request)))
        assert asyncio.run(sdk.catalog(board[2].get_uid(), card.get_uid())) == result
        updated = {**definition.declaration, "version": "1.0.1", "mcp": {**definition.declaration["mcp"],
                   "tools": [*definition.declaration["mcp"]["tools"],
                             {"name": "close_issue", "description": "Close issue", "authority_scope": "write"}]}}
        # Real registry update withdraws only affected MCP scope consent.
        save_definition(board[1], updated, definition.edit_revision())
        from langboard_shared.domain.services.AppGovernance import AppGovernanceConflict
        with pytest.raises(AppGovernanceConflict):
            save_mcp_connection_consent(board[1], connection.id, ["read", "write"], 1,
                                       consent["identity_revision"], consent["app_revision"])
        reviewed = read_mcp_connection_consent(board[1], connection.id)
        assert reviewed["scopes"] == ["read"]
        assert reviewed["granted_tools"] == {"read": ["read_issue"]}
        assert reviewed["available_tools"]["write"] == ["close_issue", "write_issue"]
        # Reverting the tool set never resurrects the revoked write consent.
        with DbSession.use(readonly=False) as db:
            from langboard_shared.domain.models import AppDefinition
            saved = db.exec(SqlBuilder.select.table(AppDefinition)).first()
        reverted = {**updated, "version": "1.0.2", "mcp": {**updated["mcp"], "tools": updated["mcp"]["tools"][:2]}}
        save_definition(board[1], reverted, saved.edit_revision())
        assert read_mcp_connection_consent(board[1], connection.id)["scopes"] == ["read"]
        result = client.get(path, headers=headers).json()
        assert [tool["name"] for tool in result["tools"]] == ["read_issue"]
        with DbSession.atomic() as db:
            current = db.exec(SqlBuilder.select.table(BoardAppBinding)).first()
            assert "mcp.read" in current.granted_capabilities and "mcp.write" not in current.granted_capabilities
            current.granted_capabilities = [grant for grant in current.granted_capabilities if grant != "mcp.read"]
            db.update(current)
        assert client.get(path, headers=headers).json()["tools"] == []
        with DbSession.atomic() as db:
            current = db.exec(SqlBuilder.select.table(AppResourceBinding).where(AppResourceBinding.id == resources[0].id)).first()
            current.access_state = "revoked"
            db.update(current)
        assert client.get(path, headers=headers).status_code == 403
        with DbSession.atomic() as db:
            connection.state = "revoked"
            db.update(connection)
        assert client.get(path, headers=headers).status_code == 401
