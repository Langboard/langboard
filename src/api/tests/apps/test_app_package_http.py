# ruff: noqa: F811
"""Authenticated ZIP install, revision guard and disabled asset readback."""
import langboard.routes.settings.AppPackageSettingsApi  # noqa: F401
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.routing import AppRouter
from langboard_shared.core.security import AuthSecurity
from langboard_shared.domain.services.factory.WorkflowStageService_app_test import board  # noqa: F401
from langboard_shared.Env import Env
from test_app_package import DECLARATION, bundle


@pytest.mark.parametrize("board", ["sqlite-http", "postgresql-test"], indirect=True)
def test_install_revision_update_disable_and_revocation(board, monkeypatch, tmp_path):
    from langboard_shared.publishers import AppSettingPublisher
    monkeypatch.setattr(AppSettingPublisher, "apps_changed", lambda: None)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", DbEngine.get_main_engine)
    monkeypatch.setattr(type(Env), "API_URL", property(lambda _: "https://api.example.invalid"))
    monkeypatch.setattr(type(Env), "PUBLIC_UI_URL", property(lambda _: "https://host.example.invalid"))
    monkeypatch.setattr(type(Env), "LOCAL_STORAGE_DIR", property(lambda _: tmp_path / "files"))
    monkeypatch.setattr(type(Env), "CACHE_DIR", property(lambda _: tmp_path / "cache"))
    actor = board[1]
    with DbSession.use(readonly=False) as db:
        actor.is_admin = True
        db.update(actor)
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    access, refresh = AuthSecurity.authenticate(actor.id)
    headers = {"Authorization": f"Bearer {access}"}
    with TestClient(app) as client:
        client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
        response = client.post("/settings/apps/packages/upload", files={"file": ("app.zip", bundle(), "application/zip")}, headers=headers)
        assert response.status_code == 200, response.text
        installed = response.json()["app"]
        assert installed["installation"]["kind"] == "manual"
        assert "storage" not in installed["installation"]
        assert installed["is_enabled"]
        path = installed["declaration"]["panel"]["url"].removeprefix("https://api.example.invalid")
        asset = client.get(path)
        assert asset.status_code == 200
        assert "sandbox allow-scripts" in asset.headers["Content-Security-Policy"]
        host_module = client.get(path.replace("index.html", "host.mjs"))
        assert host_module.status_code == 200
        assert host_module.text == 'export const hostOrigin = "https://host.example.invalid";'
        assert "placeholder.invalid" not in host_module.text
        catalog = client.get("/settings/apps/packages", headers=headers).json()
        assert catalog["installed"][0]["revision"] == installed["revision"]
        changed = bundle(declaration={**DECLARATION, "version": "1.1.0"})
        missing_revision = client.post("/settings/apps/packages/upload", files={"file": ("app.zip", changed)}, headers=headers)
        assert missing_revision.status_code == 409
        update = client.post("/settings/apps/packages/upload", files={"file": ("app.zip", changed)}, data={"expected_revision": installed["revision"]}, headers=headers)
        assert update.status_code == 200, update.text
        updated = update.json()["app"]
        assert updated["revision"] != installed["revision"]
        assert client.get(path).status_code == 404
        disabled = client.post("/settings/apps/registry/github-panel/disable", json={"expected_revision": updated["revision"]}, headers=headers)
        assert disabled.status_code == 200, disabled.text
        new_path = updated["declaration"]["panel"]["url"].removeprefix("https://api.example.invalid")
        assert client.get(new_path).status_code == 404
        with DbSession.use(readonly=False) as db:
            actor.is_admin = False
            db.update(actor)
        denied = client.post("/settings/apps/packages/upload", files={"file": ("app.zip", changed)}, headers=headers)
        assert denied.status_code == 403
