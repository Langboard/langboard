# ruff: noqa: F811
"""User discovery must not expose registry internals or bypass current policy."""

import langboard.routes.settings.AppPackageSettingsApi  # noqa: F401
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.routing import AppRouter
from langboard_shared.core.security import AuthSecurity
from langboard_shared.core.types import SnowflakeID
from langboard_shared.domain.models import (
    AppComposition,
    AppCompositionMember,
    AppDefinition,
    AppGovernancePolicy,
    BoardAppBinding,
    Organization,
)
from langboard_shared.domain.services.AppCatalog import list_catalog
from langboard_shared.Env import Env


@pytest.mark.parametrize("board", ["sqlite-http", "postgresql-test"], indirect=True)
def test_user_catalog_is_paged_sanitized_current_and_not_an_install_grant(board, monkeypatch, tmp_path):
    monkeypatch.setattr(DbEngine, "get_readonly_engine", DbEngine.get_main_engine)
    monkeypatch.setattr(type(Env), "CACHE_DIR", property(lambda _: tmp_path / "cache"))
    _, actor, project, _, role, *_ = board
    with DbSession.atomic() as db:
        for key, enabled in (("alpha", True), ("beta", True), ("disabled", False), ("github-panel", True)):
            db.insert(AppDefinition(key=key, approved_by=2, is_enabled=enabled, declaration={
                "key": key, "name": key.title(), "version": "1.0.0", "description": "Public summary",
                "capabilities": ["panels.render"], "resource_types": [],
                "trust": {"data_origins": ["https://private.example.invalid"]},
                "panel": {"url": "https://private.example.invalid/panel"},
            }, package={"storage": {"secret": "private-storage-locator"}, "kind": "manual", "sha256": "a" * 64,
                        "store": {"icon": "panel/icon.svg", "icon_dark": "panel/icon-dark.svg", "internal": "private-metadata"}}))
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
            "resource_types": [], "is_installed": True, "source": "manual", "installation": {"kind": "manual", "sha256": "a" * 64,
            "store": {"icon": "panel/icon.svg", "icon_dark": "panel/icon-dark.svg"}}}]
        assert len(first["official"]) == 3
        assert first["official"][0]["is_installed"] is True
        assert first["official"][0]["version"] == "1.0.0"
        assert first["official"][0]["source"] == "manual"
        detail = client.get("/apps/catalog", params={"key": "github-panel"}, headers=headers)
        assert [item["key"] for item in detail.json()["items"]] == ["github-panel"]
        assert client.get("/apps/catalog", params={"key": "alpha", "after": "beta"}, headers=headers).status_code == 400
        assert "private" not in response.text
        second = client.get("/apps/catalog", params={"after": "alpha", "limit": 1}, headers=headers).json()
        assert [item["key"] for item in second["items"]] == ["beta"]
        assert second["next_cursor"] == "beta"
        last = client.get("/apps/catalog", params={"after": "beta", "limit": 1}, headers=headers).json()
        assert [item["key"] for item in last["items"]] == ["github-panel"]
        assert last["next_cursor"] is None
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

@pytest.fixture(autouse=True)
def catalog_composition_tables(board):
    for model in (AppComposition, AppCompositionMember):
        model.__table__.create(DbEngine.get_main_engine())


@pytest.mark.parametrize("board", ["sqlite-http", "postgresql-test"], indirect=True)
def test_catalog_groups_before_pagination_and_preserves_legacy_identity(board, monkeypatch):
    _, actor, project, *_ = board
    with DbSession.atomic() as db:
        for key, enabled in (("alpha", True), ("github-panel", True), ("disabled", False), ("tools", True)):
            db.insert(AppDefinition(key=key, approved_by=2, is_enabled=enabled, declaration={
                "name": key.title(), "version": "1.0.0", "description": "Public summary",
                "capabilities": ["panels.render"], "resource_types": [], "panel": {"url": "https://private.invalid"},
            }))
        group = AppComposition(key="tools", name="Explicit tools", description="Two authorities", approved_by=2)
        db.insert(group)
        for order, key in enumerate(("github", "github-panel", "disabled")):
            db.insert(AppCompositionMember(composition_id=group.id, module_key=key, order=order))
    with DbSession.atomic() as db:
        binding = BoardAppBinding(project_id=project.id, app_key="github-panel", state="enabled", granted_capabilities=["panels.render"])
        db.insert(binding)
        binding_revision = binding.edit_revision()
    page = list_catalog(actor, limit=1, include_compositions=True)
    identities = []
    modules = []
    while True:
        identities.extend((entry["key"], entry.get("kind", "module")) for entry in page["items"])
        modules.extend(module["key"] for entry in page["items"] for module in entry.get("modules", []))
        if not page["next_cursor"]:
            break
        page = list_catalog(actor, limit=1, after_key=page["next_cursor"], include_compositions=True)
    assert identities == [("alpha", "module"), ("tools", "composition"), ("tools", "module")]
    assert modules == ["github", "github-panel", "disabled"]
    logical = list_catalog(actor, composition_key="tools", featured_keys=("github-panel",))
    assert logical["featured"] == logical["items"]
    entry = logical["items"][0]
    assert entry["composition_key"] == "tools" and "capabilities" not in entry
    assert [item["is_enabled"] for item in entry["modules"]] == [True, True, False]
    assert entry["modules"][0]["builtin"] is True
    assert len(entry["modules"][1]["revision"]) == 64
    assert "private" not in str(entry)
    assert list_catalog(actor, app_key="tools")["items"][0]["name"] == "Tools"
    assert list_catalog(actor, app_key="github-panel")["items"][0]["key"] == "github-panel"
    assert list_catalog(actor, after_key="tools")["items"] == []
    with DbSession.atomic() as db:
        definition = db.exec(SqlBuilder.select.table(AppDefinition).where(AppDefinition.key == "github-panel")).first()
        before_revision = definition.edit_revision()
        definition.is_enabled = False
        definition.declaration = {**definition.declaration, "version": "2.0.0", "capabilities": ["panels.render", "signals.read"]}
        db.update(definition)
    current = list_catalog(actor, composition_key="tools")["items"][0]["modules"][1]
    assert current["is_enabled"] is False and current["version"] == "2.0.0"
    assert current["revision"] != before_revision
    assert list_catalog(actor, app_key="github-panel")["items"] == []
    with DbSession.atomic() as db:
        unchanged = db.exec(SqlBuilder.select.table(BoardAppBinding).where(BoardAppBinding.id == binding.id)).first()
        assert unchanged.edit_revision() == binding_revision
        assert unchanged.granted_capabilities == ["panels.render"]
        # Restore availability for the exact legacy HTTP detail checks below.
        definition.is_enabled = True
        db.update(definition)
    with pytest.raises(ValueError):
        list_catalog(actor, app_key="tools", composition_key="tools")
    with pytest.raises(ValueError):
        list_catalog(actor, composition_key="tools", after_key="alpha")
    monkeypatch.setattr(DbEngine, "get_readonly_engine", DbEngine.get_main_engine)
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    access, refresh = AuthSecurity.authenticate(actor.id)
    headers = {"Authorization": f"Bearer {access}"}
    with TestClient(app) as client:
        client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
        legacy = client.get("/apps/catalog", params={"limit": 1}, headers=headers)
        assert legacy.status_code == 200, legacy.text
        assert legacy.json()["next_cursor"] == "alpha"
        legacy_rows = legacy.json()["items"]
        legacy_cursor = legacy.json()["next_cursor"]
        while legacy_cursor:
            legacy_page = client.get("/apps/catalog", params={"limit": 1, "after": legacy_cursor}, headers=headers).json()
            legacy_rows.extend(legacy_page["items"])
            legacy_cursor = legacy_page["next_cursor"]
        assert [row["key"] for row in legacy_rows] == ["alpha", "github-panel", "tools"]
        assert all("kind" not in row and "modules" not in row for row in legacy_rows)
        assert all("kind" not in row and "modules" not in row for row in legacy.json()["official"])
        assert "github-panel" in [row["key"] for row in legacy.json()["official"]]
        assert client.get("/apps/catalog", params={"after": "tools~composition"}, headers=headers).status_code == 400
        response = client.get("/apps/catalog", params={"include_compositions": True}, headers=headers)
        assert response.status_code == 200, response.text
        body = response.json()
        assert [item["key"] for item in body["official"]].count("tools") == 1
        assert "github-panel" not in [item["key"] for item in body["official"]]
        for selector in ({"key": "github-panel"}, {"composition_key": "tools"}, {"key": "tools"}):
            detail = client.get("/apps/catalog", params=selector, headers=headers)
            assert detail.status_code == 200, detail.text
            assert len(detail.json()["items"]) == 1
        cursor = client.get("/apps/catalog", params={"limit": 1, "after": "alpha", "include_compositions": True}, headers=headers).json()["next_cursor"]
        assert cursor == "tools~composition"
        next_page = client.get("/apps/catalog", params={"limit": 1, "after": cursor, "include_compositions": True}, headers=headers)
        assert next_page.status_code == 200, next_page.text
        assert next_page.json()["items"][0]["name"] == "Tools"
        assert client.get("/apps/catalog", params={"composition_key": "tools", "project_uid": SnowflakeID(11).to_short_code()}, headers=headers).status_code == 403
        assert client.post("/settings/apps/packages/official/github-panel/install", json={}, headers=headers).status_code == 403
        with DbSession.atomic() as db:
            db.insert(AppGovernancePolicy(scope_key="global", mode="disabled"))
        hidden = client.get("/apps/catalog", params={"composition_key": "tools"}, headers=headers).json()
        assert hidden["items"] == hidden["official"] == []
        for params in ({}, {"include_compositions": True}, {"key": "github-panel"}):
            denied = client.get("/apps/catalog", params=params, headers=headers).json()
            assert denied["items"] == denied["official"] == []
