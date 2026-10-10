# ruff: noqa: F811
"""Registered inbound apps participate in bounded context without provider secrets."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
from langboard.routes.board import CardAppResourcesApi  # noqa: F401
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.routing import AppRouter
from langboard_shared.core.security import AuthSecurity
from langboard_shared.domain.models import Organization, User
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.factory.WorkflowStageService_app_test import board  # noqa: F401
from langboard_shared.Env import Env
from sqlalchemy import text
from test_app_connection_resources import scope


@pytest.mark.parametrize("board", ["sqlite-http", "postgresql-test"], indirect=True)
@pytest.mark.parametrize(
    "gate", ["enabled", "disabled", "capability", "resource", "connection", "foreign-owner", "type"]
)
def test_registered_context_uses_declaration_consent_and_current_owner(board, gate, monkeypatch):
    engine = DbEngine.get_main_engine()
    if engine.dialect.name == "postgresql":
        with engine.begin() as db:
            db.execute(text("ALTER TABLE organization ADD COLUMN is_active BOOLEAN DEFAULT TRUE"))
            db.execute(text("ALTER TABLE organization ADD COLUMN suspended_at TIMESTAMP"))
    else:
        Organization.__table__.create(engine)
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
