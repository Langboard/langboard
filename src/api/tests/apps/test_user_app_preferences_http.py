# ruff: noqa: F811
"""Separate devices must not overwrite account preferences or each other."""

import langboard.routes.account.AccountApi  # noqa: F401
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.routing import AppRouter
from langboard_shared.core.security import AuthSecurity
from langboard_shared.domain.models import UserAppPreference
from langboard_shared.domain.services.factory.WorkflowStageService_app_test import board  # noqa: F401
from langboard_shared.Env import Env


@pytest.mark.parametrize("board", ["sqlite-http", "postgresql-test"], indirect=True)
def test_preferences_are_current_bounded_account_owned_and_revision_checked(board, monkeypatch, tmp_path):
    monkeypatch.setattr(DbEngine, "get_readonly_engine", DbEngine.get_main_engine)
    monkeypatch.setattr(type(Env), "CACHE_DIR", property(lambda _: tmp_path / "cache"))
    _, actor, *_ = board
    UserAppPreference.__table__.create(DbEngine.get_main_engine())
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    access, refresh = AuthSecurity.authenticate(actor.id)
    headers = {"Authorization": f"Bearer {access}"}
    endpoint = "/account/app-preferences"
    pins = [{"key": "github-panel", "name": "GitHub"}, {"key": "missing-app", "name": "Unavailable"}]
    with TestClient(app) as client:
        assert client.get(endpoint).status_code == 401
        client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
        initial = client.get(endpoint, headers=headers)
        assert initial.status_code == 200, initial.text
        assert initial.headers["Cache-Control"] == "no-store"
        assert initial.json() == {"revision": 0, "pinned_apps": []}
        saved = client.put(endpoint, json={"expected_revision": 0, "pinned_apps": pins}, headers=headers)
        assert saved.status_code == 200, saved.text
        assert saved.json() == {"revision": 1, "pinned_apps": pins}
        # An independent device reopens the same account, without browser storage.
        with TestClient(app) as device:
            device.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
            assert device.get(endpoint, headers=headers).json() == saved.json()
            stale = device.put(endpoint, json={"expected_revision": 0, "pinned_apps": []}, headers=headers)
            assert stale.status_code == 409
            assert device.get(endpoint, headers=headers).json() == saved.json()
        # An unrelated account sees no shortcuts, including for instance admins.
        other_access, other_refresh = AuthSecurity.authenticate(2)
        client.cookies.set(Env.REFRESH_TOKEN_NAME, other_refresh)
        assert client.get(endpoint, headers={"Authorization": f"Bearer {other_access}"}).json() == {
            "revision": 0,
            "pinned_apps": [],
        }
        client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
        for invalid in (
            {"expected_revision": True, "pinned_apps": pins},
            {"expected_revision": 1, "pinned_apps": pins * 2},
            {"expected_revision": 1, "pinned_apps": [{"key": "../private", "name": "x"}]},
            {"expected_revision": 1, "pinned_apps": [{"key": "valid", "name": " "}]},
            {"expected_revision": 1, "pinned_apps": [{"key": "valid", "name": "x", "token": "forbidden"}]},
            {"expected_revision": 1, "pinned_apps": [{"key": f"app-{i}", "name": "x"} for i in range(33)]},
            {"expected_revision": 1, "pinned_apps": pins, "user_id": 2},
        ):
            assert client.put(endpoint, json=invalid, headers=headers).status_code in (400, 422)
        assert client.get(endpoint, headers=headers).json() == saved.json()
        reordered = client.put(
            endpoint, json={"expected_revision": 1, "pinned_apps": list(reversed(pins))}, headers=headers
        )
        assert reordered.json() == {"revision": 2, "pinned_apps": list(reversed(pins))}
        cleared = client.put(endpoint, json={"expected_revision": 2, "pinned_apps": []}, headers=headers)
        assert cleared.json() == {"revision": 3, "pinned_apps": []}
        if DbEngine.get_main_engine().dialect.name == "postgresql":
            from concurrent.futures import ThreadPoolExecutor
            from threading import Barrier
            from langboard_shared.domain.services.UserAppPreferences import PreferenceConflict, app_preferences

            barrier = Barrier(2)

            def competing_write(candidate):
                barrier.wait(timeout=10)
                try:
                    return app_preferences(actor, pinned_apps=candidate, expected_revision=3)
                except PreferenceConflict:
                    return "conflict"

            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(competing_write, [pins, list(reversed(pins))]))
            assert outcomes.count("conflict") == 1
            assert [outcome for outcome in outcomes if isinstance(outcome, dict)][0]["revision"] == 4
        with DbSession.atomic() as db:
            actor.activated_at = None
            db.update(actor)
        assert client.get(endpoint, headers=headers).status_code in (401, 403)
        assert client.put(
            endpoint, json={"expected_revision": 3, "pinned_apps": pins}, headers=headers
        ).status_code in (401, 403)
