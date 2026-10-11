# ruff: noqa: F811
"""Current MCP declarations require independent consent and owning-card scope."""

import asyncio
from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langboard.routes.settings import AppMcpCatalogApi  # noqa: F401
from langboard_sdk import AppMcp, HttpTransport
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.routing import AppRouter
from langboard_shared.domain.models import AppResourceBinding, BoardAppBinding
from langboard_shared.domain.services.AppConnectionAuthentication import issue_connection_credential
from langboard_shared.domain.services.AppRegistry import save_definition
from langboard_shared.domain.services.factory.WorkflowStageService_app_test import board  # noqa: F401
from test_app_execution_grant import grant_scope


@pytest.mark.parametrize("board", ["sqlite-http", "postgresql-test"], indirect=True)
def test_catalog_native_http_consent_update_revocation_and_sdk(board, monkeypatch):
    connection, definition, binding, resources, card, token = grant_scope(board)
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
        response = client.get(path, headers=headers)
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        result = response.json()
        assert result["execution_supported"] is False
        assert {tool["qualified_name"] for tool in result["tools"]} == {
            f"{connection.app_key}:{connection.get_uid()}:read_issue", f"{connection.app_key}:{connection.get_uid()}:write_issue"}
        assert all(tool["callable"] is False for tool in result["tools"])
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
