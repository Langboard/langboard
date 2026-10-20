"""Filter shortcuts survive devices without crossing account or board scope."""

import importlib.util
from pathlib import Path
import langboard.routes.board.BoardFilterPreferencesApi  # noqa: F401
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.routing import AppRouter
from langboard_shared.core.security import AuthSecurity
from langboard_shared.core.types import SnowflakeID
from langboard_shared.Env import Env


@pytest.mark.parametrize("board", ["sqlite-http", "postgresql-test"], indirect=True)
def test_filter_preferences_http_scope_revision_validation_and_revocation(board, monkeypatch):
    _, actor, project, _, role, *_ = board
    engine = DbEngine.get_main_engine()
    monkeypatch.setattr(DbEngine, "get_readonly_engine", DbEngine.get_main_engine)
    path = Path(__file__).resolve().parents[2] / "langboard/migrations/versions/20261021010000-b392a7f43165.py"
    spec = importlib.util.spec_from_file_location("filter_preference_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine.begin() as connection:
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.upgrade()
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    access, refresh = AuthSecurity.authenticate(actor.id)
    headers = {"Authorization": f"Bearer {access}"}
    endpoint = f"/board/{project.get_uid()}/filter-preferences"
    pins = [
        {"key": "unfinished", "value": "true"},
        {"key": "members", "value": "me"},
        {"key": "keyword", "value": " exact phrase "},
    ]
    with TestClient(app) as client:
        assert client.get(endpoint).status_code == 401
        client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
        initial = client.get(endpoint, headers=headers)
        assert initial.status_code == 200, initial.text
        assert initial.headers["Cache-Control"] == "no-store"
        assert initial.json() == {"revision": 0, "pinned_filters": []}
        saved = client.put(endpoint, json={"expected_revision": 0, "pinned_filters": pins}, headers=headers)
        assert saved.status_code == 200, saved.text
        assert saved.json() == {"revision": 1, "pinned_filters": pins}
        with TestClient(app) as device:
            device.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
            assert device.get(endpoint, headers=headers).json() == saved.json()
            assert (
                device.put(endpoint, json={"expected_revision": 0, "pinned_filters": []}, headers=headers).status_code
                == 409
            )
            assert device.get(endpoint, headers=headers).json() == saved.json()
        foreign_endpoint = f"/board/{SnowflakeID(11).to_short_code()}/filter-preferences"
        assert client.get(foreign_endpoint, headers=headers).status_code == 403
        assert (
            client.put(
                foreign_endpoint, json={"expected_revision": 0, "pinned_filters": pins}, headers=headers
            ).status_code
            == 403
        )
        owner_access, owner_refresh = AuthSecurity.authenticate(2)
        with TestClient(app) as owner:
            owner.cookies.set(Env.REFRESH_TOKEN_NAME, owner_refresh)
            owner_headers = {"Authorization": f"Bearer {owner_access}"}
            assert owner.get(endpoint, headers=owner_headers).json() == {"revision": 0, "pinned_filters": []}
            own_other_board = owner.put(
                foreign_endpoint, json={"expected_revision": 0, "pinned_filters": pins}, headers=owner_headers
            )
            assert own_other_board.status_code == 200, own_other_board.text
            assert owner.get(endpoint, headers=owner_headers).json() == {"revision": 0, "pinned_filters": []}
        for payload in (
            {"expected_revision": True, "pinned_filters": pins},
            {"expected_revision": 1, "pinned_filters": pins * 2},
            {"expected_revision": 1, "pinned_filters": [{"key": "other", "value": "x"}]},
            {"expected_revision": 1, "pinned_filters": [{"key": "keyword", "value": " "}]},
            {"expected_revision": 1, "pinned_filters": [{"key": "keyword", "value": 1}]},
            {"expected_revision": 1, "pinned_filters": [{"key": "keyword", "value": "x" * 257}]},
            {"expected_revision": 1, "pinned_filters": [{"key": "keyword", "value": str(i)} for i in range(33)]},
            {"expected_revision": 1, "pinned_filters": [{"key": "members", "value": "me", "title": "private"}]},
            {"expected_revision": 1, "pinned_filters": pins, "user_id": 2},
        ):
            assert client.put(endpoint, json=payload, headers=headers).status_code in (400, 422)
        assert client.get(endpoint, headers=headers).json() == saved.json()
        cleared = client.put(endpoint, json={"expected_revision": 1, "pinned_filters": []}, headers=headers)
        assert cleared.json() == {"revision": 2, "pinned_filters": []}
        if engine.dialect.name == "postgresql":
            from concurrent.futures import ThreadPoolExecutor
            from threading import Barrier
            from langboard_shared.domain.services.BoardFilterPreferences import board_filter_preferences
            from langboard_shared.domain.services.UserAppPreferences import PreferenceConflict

            barrier = Barrier(2)

            def competing_write(candidate):
                barrier.wait(timeout=10)
                try:
                    return board_filter_preferences(actor, project.id, pinned_filters=candidate, expected_revision=2)
                except PreferenceConflict:
                    return "conflict"

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(competing_write, [pins, list(reversed(pins))]))
            assert results.count("conflict") == 1
        with DbSession.atomic() as db:
            role.actions = []
            db.update(role)
        assert client.get(endpoint, headers=headers).status_code == 403
        assert (
            client.put(endpoint, json={"expected_revision": 2, "pinned_filters": pins}, headers=headers).status_code
            == 403
        )
    with engine.begin() as connection:
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        with pytest.raises(RuntimeError, match="Cannot discard"):
            migration.downgrade()
