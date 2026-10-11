# ruff: noqa: F811
"""Grouping is explicit presentation metadata, with no module authority migration."""

from copy import deepcopy
import pytest
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.domain.models import AppComposition, AppCompositionMember, AppDefinition, BoardAppBinding
from langboard_shared.domain.services.AppComposition import (
    board_compositions,
    list_compositions,
    save_composition,
    ungroup_composition,
)
from langboard_shared.domain.services.AppRegistry import AppRegistryConflict, AppRegistryDenied
from langboard_shared.domain.services.factory.WorkflowStageService_app_test import board  # noqa: F401


@pytest.fixture
def composition(board, monkeypatch):
    from langboard_shared.publishers import AppSettingPublisher
    monkeypatch.setattr(AppSettingPublisher, "apps_changed", lambda: None)
    for model in (AppComposition, AppCompositionMember):
        model.__table__.create(DbEngine.get_main_engine())
    actor = board[1]
    declaration = {
        "schema_version": 1, "key": "independent-panel", "version": "1.0.0", "name": "Independent",
        "description": "Independent module, no provider name heuristic.", "capabilities": ["panels.render"],
        "resource_types": [], "panel": {"url": "https://example.invalid/panel", "name": "Panel", "icon": "x"},
    }
    with DbSession.use(readonly=False) as db:
        actor.is_admin = True
        db.update(actor)
        db.insert(AppDefinition(key="independent-panel", declaration=declaration, approved_by=actor.id))
        db.insert(BoardAppBinding(project_id=board[2].id, app_key="github", state="enabled",
                                 granted_capabilities=["signals.read"], stage_transitions_enabled=False))
    return board


def save(actor, **kwargs):
    return save_composition(actor, "review-tools", "Review tools", "Explicit module grouping.",
                            kwargs.pop("module_keys", ["github", "independent-panel"]), **kwargs)


def test_group_update_ungroup_preserves_exact_authority_records(composition):
    board = composition
    actor = board[1]
    with DbSession.use(readonly=False) as db:
        binding = db.exec(SqlBuilder.select.table(BoardAppBinding).where(BoardAppBinding.app_key == "github")).first()
        before = (binding.id, binding.edit_revision(), deepcopy(binding.granted_capabilities))
        definition = db.exec(SqlBuilder.select.table(AppDefinition)).first()
        definition_before = (definition.id, definition.edit_revision(), deepcopy(definition.declaration))
    saved = save(actor)
    assert saved["module_keys"] == ["github", "independent-panel"]
    assert saved["generation"] == 1
    assert save(actor, expected_revision=saved["revision"]) == saved
    grouped = board_compositions(board[0], actor, board[2].get_uid())
    assert grouped["compositions"] == [saved]
    assert {app["key"] for app in grouped["apps"]} == {"github", "glitchtip", "dokploy", "independent-panel"}
    assert next(app for app in grouped["apps"] if app["key"] == "github")["binding"]["granted_capabilities"] == ["signals.read"]
    assert next(app for app in grouped["apps"] if app["key"] == "independent-panel")["binding"] is None
    with pytest.raises(AppRegistryConflict):
        save(actor)
    changed = save(actor, module_keys=["independent-panel", "github"], expected_revision=saved["revision"])
    assert changed["generation"] == 2 and changed["revision"] != saved["revision"]
    with pytest.raises(AppRegistryConflict):
        ungroup_composition(actor, "review-tools", saved["revision"])
    assert ungroup_composition(actor, "review-tools", changed["revision"])["ungrouped"]
    assert list_compositions(actor) == []
    with DbSession.use(readonly=False) as db:
        binding = db.exec(SqlBuilder.select.table(BoardAppBinding).where(BoardAppBinding.app_key == "github")).first()
        assert (binding.id, binding.edit_revision(), binding.granted_capabilities) == before
        definition = db.exec(SqlBuilder.select.table(AppDefinition)).first()
        assert (definition.id, definition.edit_revision(), definition.declaration) == definition_before
    assert save(actor)["generation"] == 1


def test_competing_membership_and_invalid_targets_rollback(composition):
    actor = composition[1]
    saved = save(actor)
    with pytest.raises(AppRegistryConflict):
        save_composition(actor, "other", "Other", "Competing grouping.", ["github"])
    assert list_compositions(actor) == [saved]
    for keys in (["review-tools"], ["github-panel"], ["github", "github"], []):
        with pytest.raises(ValueError):
            save(actor, module_keys=keys, expected_revision=saved["revision"])
    assert list_compositions(actor) == [saved]


def test_current_admin_and_board_read_authority_are_required(composition):
    actor = composition[1]
    saved = save(actor)
    with DbSession.use(readonly=False) as db:
        actor.is_admin = False
        db.update(actor)
    for action in (lambda: list_compositions(actor), lambda: save(actor, expected_revision=saved["revision"]),
                   lambda: ungroup_composition(actor, "review-tools", saved["revision"])):
        with pytest.raises(AppRegistryDenied):
            action()
    assert board_compositions(composition[0], actor, composition[2].get_uid())["compositions"] == [saved]
    with DbSession.use(readonly=False) as db:
        db.delete(composition[3])
    with pytest.raises(AppRegistryDenied):
        board_compositions(composition[0], actor, composition[2].get_uid())


@pytest.mark.parametrize("board", ["sqlite-http", "postgresql-test"], indirect=True)
def test_authenticated_http_composition_management_and_old_catalog(composition, monkeypatch):
    import langboard.routes.board.BoardSettingApi  # noqa: F401
    import langboard.routes.settings.AppRegistrySettingsApi  # noqa: F401
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
    from langboard_shared.core.routing import AppRouter
    from langboard_shared.core.security import AuthSecurity
    from langboard_shared.Env import Env
    board = composition
    # HTTP authentication uses the normal read engine; service tests separately
    # enforce primary-only composition authority reads.
    monkeypatch.setattr(DbEngine, "get_readonly_engine", DbEngine.get_main_engine)
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    access, refresh = AuthSecurity.authenticate(board[1].id)
    headers = {"Authorization": f"Bearer {access}"}
    path = "/settings/apps/compositions"
    board_path = f"/board/{board[2].get_uid()}/settings/apps"
    payload = {"key": "review-tools", "name": "Review tools", "description": "Explicit grouping.",
               "module_keys": ["github", "independent-panel"]}
    with TestClient(app) as client:
        client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
        before = client.get(board_path, headers=headers).json()
        catalog = client.get(path, headers=headers).json()
        assert catalog["compositions"] == []
        keys = {row["key"] for row in catalog["modules"]}
        assert {"github", "independent-panel"}.issubset(keys)
        assert "github-panel" not in keys
        assert "declaration" not in catalog["modules"][-1]
        saved = client.post(path, headers=headers, json=payload)
        assert saved.status_code == 200
        revision = saved.json()["composition"]["revision"]
        assert client.post(path, headers=headers, json=payload).status_code == 409
        assert client.post(path, headers=headers, json={**payload, "module_keys": ["not-installed"],
                                                       "expected_revision": revision}).status_code == 400
        assert client.get(board_path, headers=headers).json() == before
        grouped = client.get(board_path + "/compositions", headers=headers)
        assert grouped.status_code == 200
        assert grouped.json()["apps"] == before["apps"]
        assert grouped.json()["compositions"][0]["module_keys"] == payload["module_keys"]
        assert client.post(path + "/review-tools/ungroup", headers=headers,
                           json={"expected_revision": "0" * 64}).status_code == 409
        with DbSession.use(readonly=False) as db:
            board[1].is_admin = False
            db.update(board[1])
        assert client.get(path, headers=headers).status_code == 403
        assert client.post(path + "/review-tools/ungroup", headers=headers,
                           json={"expected_revision": revision}).status_code == 403
        assert client.get(board_path + "/compositions", headers=headers).status_code == 200


def test_schema_upgrade_rollback_preserves_module_tables(board, monkeypatch):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from langboard_shared.publishers import AppSettingPublisher
    from sqlalchemy import inspect
    monkeypatch.setattr(AppSettingPublisher, "apps_changed", lambda: None)
    engine = DbEngine.get_main_engine()
    path = Path(__file__).resolve().parents[2] / "langboard/migrations/versions/20261011150000-e71b63c8a204.py"
    spec = importlib.util.spec_from_file_location("composition_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with engine.begin() as connection:
        module.op = Operations(MigrationContext.configure(connection))
        module.upgrade()
        for model in (AppComposition, AppCompositionMember):
            assert {c["name"] for c in inspect(connection).get_columns(model.__tablename__)} == set(model.__table__.columns.keys())
    with DbSession.use(readonly=False) as db:
        board[1].is_admin = True
        db.update(board[1])
    saved = save_composition(board[1], "native-tools", "Native tools", "Explicit native modules.", ["github", "dokploy"])
    with engine.begin() as connection:
        module.op = Operations(MigrationContext.configure(connection))
        with pytest.raises(RuntimeError, match="Ungroup"):
            module.downgrade()
    ungroup_composition(board[1], "native-tools", saved["revision"])
    with engine.begin() as connection:
        module.op = Operations(MigrationContext.configure(connection))
        module.downgrade()
        tables = inspect(connection).get_table_names()
        assert "app_composition" not in tables and "app_composition_member" not in tables
        assert "app_definition" in tables and "board_app_binding" in tables
