# ruff: noqa: F811
"""User discovery must not expose registry internals or bypass current policy."""

import langboard.routes.settings.AppPackageSettingsApi  # noqa: F401
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.routing import AppRouter
from langboard_shared.core.security import AuthSecurity
from langboard_shared.core.types import SnowflakeID
from langboard_shared.domain.models import AppDefinition, AppGovernancePolicy, Organization
from langboard_shared.domain.services.factory.WorkflowStageService_app_test import board  # noqa: F401
from langboard_shared.Env import Env


@pytest.mark.parametrize("board", ["sqlite-http", "postgresql-test"], indirect=True)
def test_user_catalog_is_paged_sanitized_current_and_not_an_install_grant(board, monkeypatch, tmp_path):
    monkeypatch.setattr(DbEngine, "get_readonly_engine", DbEngine.get_main_engine)
    monkeypatch.setattr(type(Env), "CACHE_DIR", property(lambda _: tmp_path / "cache"))
    _, actor, project, _, role, *_ = board
    with DbSession.atomic() as db:
        for key, enabled in (("alpha", True), ("beta", True), ("disabled", False)):
            db.insert(AppDefinition(key=key, approved_by=2, is_enabled=enabled, declaration={
                "key": key, "name": key.title(), "version": "1.0.0", "description": "Public summary",
                "capabilities": ["panels.render"], "resource_types": [],
                "trust": {"data_origins": ["https://private.example.invalid"]},
                "panel": {"url": "https://private.example.invalid/panel"},
            }, package={"storage": {"secret": "private-storage-locator"}, "sha256": "private-digest"}))
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    access, refresh = AuthSecurity.authenticate(actor.id)
    headers = {"Authorization": f"Bearer {access}"}
    with TestClient(app) as client:
        assert client.get("/apps/catalog").status_code == 401
        client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
        response = client.get("/apps/catalog", params={"limit": 1}, headers=headers)
        assert response.status_code == 200, response.text
        assert response.headers["Cache-Control"] == "no-store"
        first = response.json()
        assert first["can_manage_installation"] is False
        assert first["effective_mode"] == "approved_only"
        assert first["next_cursor"] == "alpha"
        assert first["items"] == [{"key": "alpha", "name": "Alpha", "version": "1.0.0",
            "description": "Public summary", "capabilities": ["panels.render"],
            "resource_types": [], "is_installed": True}]
        assert len(first["official"]) == 3
        assert "private" not in response.text
        second = client.get("/apps/catalog", params={"after": "alpha", "limit": 1}, headers=headers).json()
        assert [item["key"] for item in second["items"]] == ["beta"]
        assert second["next_cursor"] is None
        assert client.get("/settings/apps/packages", headers=headers).status_code == 403
        assert client.post("/settings/apps/packages/official/github-panel/install", json={}, headers=headers).status_code == 403
        for params in ({"limit": 0}, {"limit": 51}, {"after": "../private"}):
            assert client.get("/apps/catalog", params=params, headers=headers).status_code in (400, 422)
        assert client.get("/apps/catalog", params={"project_uid": SnowflakeID(11).to_short_code()}, headers=headers).status_code == 403
        assert client.get("/apps/catalog", params={"project_uid": project.get_uid()}, headers=headers).status_code == 200
        with DbSession.atomic() as db:
            org = Organization(name="Restricted", slug="restricted", owner_user_id=2)
            db.insert(org)
            project.organization_id = org.id
            db.update(project)
            db.insert(AppGovernancePolicy(scope_key=f"organization:{int(org.id)}", organization_id=org.id, mode="disabled"))
        restricted_response = client.get("/apps/catalog", params={"project_uid": project.get_uid()}, headers=headers)
        assert restricted_response.status_code == 200, restricted_response.text
        restricted = restricted_response.json()
        assert restricted["effective_mode"] == "disabled"
        assert restricted["items"] == restricted["official"] == []
        assert client.get("/apps/catalog", headers=headers).json()["items"]
        with DbSession.atomic() as db:
            role.actions = []
            db.update(role)
        assert client.get("/apps/catalog", params={"project_uid": project.get_uid()}, headers=headers).status_code == 403
        with DbSession.atomic() as db:
            db.insert(AppGovernancePolicy(scope_key="global", mode="disabled"))
        disabled = client.get("/apps/catalog", headers=headers).json()
        assert disabled["items"] == disabled["official"] == []
        with DbSession.atomic() as db:
            actor.activated_at = None
            db.update(actor)
        assert client.get("/apps/catalog", headers=headers).status_code in (401, 403)
