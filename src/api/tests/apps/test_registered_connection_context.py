# ruff: noqa: F811
"""Registered inbound apps participate in bounded context without provider secrets."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
from langboard.routes.board import AppPanelDataApi, CardAppResourcesApi  # noqa: F401
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.routing import AppRouter
from langboard_shared.core.security import AuthSecurity
from langboard_shared.domain.models import User
from langboard_shared.domain.services import DomainService
from langboard_shared.Env import Env
from test_app_connection_resources import scope


@pytest.mark.parametrize("board", ["sqlite-http", "postgresql-test"], indirect=True)
@pytest.mark.parametrize(
    "gate", ["enabled", "disabled", "capability", "resource", "connection", "foreign-owner", "type"]
)
def test_registered_context_uses_declaration_consent_and_current_owner(board, gate, monkeypatch):
    connection, definition, binding, resources, _ = scope(board)
    with DbSession.atomic() as db:
        if gate == "disabled":
            definition.is_enabled = False
            db.update(definition)
        elif gate == "capability":
            definition.declaration = {**definition.declaration, "capabilities": ["cards.create"]}
            db.update(definition)
        elif gate == "resource":
            for row in resources:
                row.access_state = "revoked"
                db.update(row)
        elif gate == "connection":
            connection.state = "disconnected"
            db.update(connection)
        elif gate == "foreign-owner":
            connection.owner_id = 2
            db.update(connection)
            other = db.exec(SqlBuilder.select.table(User).where(User.id == 2)).first()
            other.is_admin = True
            db.update(other)
        elif gate == "type":
            definition.declaration = {**definition.declaration, "resource_types": ["another-type"]}
            db.update(definition)
    service = DomainService()
    try:
        result = service.workflow_stage.get_connection_context(board[1], board[2].get_uid())
        if gate == "enabled":
            assert {item["resource_uid"] for item in result["items"]} == {row.get_uid() for row in resources}
            assert {item["app_key"] for item in result["items"]} == {definition.key}
            assert "credential" not in str(result) and "secret://" not in str(result)
            app = FastAPI()
            app.include_router(AppRouter.api)
            app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
            path = f"/board/{board[2].get_uid()}/apps/resource-context"
            with monkeypatch.context() as auth_db, TestClient(app) as client:
                auth_db.setattr(DbEngine, "get_readonly_engine", DbEngine.get_main_engine)
                assert client.get(path).status_code == 401
                access, refresh = AuthSecurity.authenticate(board[1].id)
                client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
                headers = {"Authorization": f"Bearer {access}"}
                response = client.get(path, headers=headers)
                assert response.status_code == 200, response.text
                assert response.headers["cache-control"] == "no-store"
                assert len(response.json()["items"]) == 3
                assert client.get(path, headers=headers, params={"cursor": "bad"}).status_code == 400
                assert client.get("/board/00000000001/apps/resource-context", headers=headers).status_code == 404
            with DbSession.atomic() as db:
                binding.granted_capabilities = []
                db.update(binding)
            assert service.workflow_stage.get_connection_context(board[1], board[2].get_uid())["items"] == []
        else:
            assert result["items"] == []
    finally:
        service.close()


@pytest.mark.parametrize("board", ["sqlite-http", "postgresql-test"], indirect=True)
def test_panel_resource_read_is_app_scoped_before_pagination_and_rechecks_authority(board, monkeypatch):
    from langboard_shared.domain.models import AppConnection, AppDefinition, AppResourceBinding, BoardAppBinding

    connection, definition, binding, resources, _ = scope(board)
    with DbSession.atomic() as db:
        definition.declaration = {**definition.declaration, "capabilities": ["panels.render", "resources.read"],
                                  "panel": {"url": "https://example.test/panel", "name": "Resources", "icon": "📋"}}
        db.update(definition)
        binding.granted_capabilities = ["panels.render", "resources.read"]
        db.update(binding)
        other_definition = AppDefinition(key="other-app", declaration={**definition.declaration, "key": "other-app"},
                                         approved_by=board[1].id)
        db.insert(other_definition)
        other_binding = BoardAppBinding(project_id=board[2].id, app_key="other-app", state="enabled",
                                       granted_capabilities=["resources.read"])
        other_connection = AppConnection(app_key="other-app", owner_id=board[1].id, state="connected")
        db.insert(other_binding)
        db.insert(other_connection)
        # These sort before the target app and must not consume its 25-row page.
        for i in range(30):
            db.insert(AppResourceBinding(id=100 + i, board_binding_id=other_binding.id,
                                         connection_id=other_connection.id, resource_type="issue",
                                         external_resource_id=str(i), access_state="granted"))
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    path = f"/board/{board[2].get_uid()}/apps/{definition.key}/panel/resources"
    form = {"app_revision": definition.edit_revision(), "binding_revision": binding.edit_revision(), "cursor": None}
    monkeypatch.setattr(DbEngine, "get_readonly_engine", DbEngine.get_main_engine)
    with TestClient(app) as client:
        assert client.post(path, json=form).status_code == 401
        access, refresh = AuthSecurity.authenticate(board[1].id)
        client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
        headers = {"Authorization": f"Bearer {access}"}
        result = client.post(path, headers=headers, json=form)
        assert result.status_code == 200, result.text
        assert result.headers["cache-control"] == "no-store"
        assert {item["resource_uid"] for item in result.json()["items"]} == {row.get_uid() for row in resources}
        assert {item["app_key"] for item in result.json()["items"]} == {definition.key}
        assert result.json()["next_cursor"] is None
        assert "secret" not in result.text and "credential" not in result.text
        assert client.post(path, headers=headers, json={**form, "app_key": "other-app"}).status_code == 400
        assert client.post(path, headers=headers, json={**form, "cursor": "bad"}).status_code == 400
        assert client.post(path, headers=headers, json={**form, "binding_revision": "0" * 64}).status_code == 409
        with DbSession.atomic() as db:
            connection.owner_id = 2
            db.update(connection)
        assert client.post(path, headers=headers, json=form).json()["items"] == []
        with DbSession.atomic() as db:
            connection.owner_id = board[1].id
            connection.state = "disconnected"
            db.update(connection)
        assert client.post(path, headers=headers, json=form).json()["items"] == []
        with DbSession.atomic() as db:
            binding.granted_capabilities = ["panels.render"]
            db.update(binding)
        assert client.post(path, headers=headers, json=form).status_code == 404
        with DbSession.atomic() as db:
            binding.granted_capabilities = ["panels.render", "resources.read"]
            db.update(binding)
            definition.is_enabled = False
            db.update(definition)
        assert client.post(path, headers=headers, json=form).status_code == 404
