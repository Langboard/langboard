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
    views_migration = _saved_view_migration()
    with engine.begin() as connection:
        monkeypatch.setattr(views_migration, "op", Operations(MigrationContext.configure(connection)))
        views_migration.upgrade()
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
        *[
            {"key": "deadlines", "value": value}
            for value in ("today", "week", "month", "quarter", "half-year", "year", "overdue", "none")
        ],
    ]
    with TestClient(app) as client:
        assert client.get(endpoint).status_code == 401
        client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
        initial = client.get(endpoint, headers=headers)
        assert initial.status_code == 200, initial.text
        assert initial.headers["Cache-Control"] == "no-store"
        assert initial.json() == {"revision": 0, "pinned_filters": [], "saved_views": []}
        saved = client.put(endpoint, json={"expected_revision": 0, "pinned_filters": pins}, headers=headers)
        assert saved.status_code == 200, saved.text
        assert saved.json() == {"revision": 1, "pinned_filters": pins, "saved_views": []}
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
            assert owner.get(endpoint, headers=owner_headers).json() == {
                "revision": 0,
                "pinned_filters": [],
                "saved_views": [],
            }
            own_other_board = owner.put(
                foreign_endpoint, json={"expected_revision": 0, "pinned_filters": pins}, headers=owner_headers
            )
            assert own_other_board.status_code == 200, own_other_board.text
            assert owner.get(endpoint, headers=owner_headers).json() == {
                "revision": 0,
                "pinned_filters": [],
                "saved_views": [],
            }
        for invalid in ("tomorrow", "", "Today", "week ", "no-deadline", "93", "today,week"):
            rejected = client.put(
                endpoint,
                json={"expected_revision": 1, "pinned_filters": [{"key": "deadlines", "value": invalid}]},
                headers=headers,
            )
            assert rejected.status_code in (400, 422), rejected.text
            assert client.get(endpoint, headers=headers).json() == saved.json()
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
        assert cleared.json() == {"revision": 2, "pinned_filters": [], "saved_views": []}
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


def test_filter_preference_metadata_compiles_for_postgres_and_matches_native_migration(monkeypatch):
    from io import StringIO
    from langboard_shared.domain.models import UserBoardFilterPreference
    from sqlalchemy import UniqueConstraint
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateTable

    table = UserBoardFilterPreference.__table__
    model_ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
    constraints = [item for item in table.constraints if isinstance(item, UniqueConstraint)]
    assert len(constraints) == 1 and [column.name for column in constraints[0].columns] == ["user_id", "project_id"]
    name = constraints[0].name
    assert len(name.encode()) <= postgresql.dialect().max_identifier_length
    path = Path(__file__).resolve().parents[2] / "langboard/migrations/versions/20261021010000-b392a7f43165.py"
    spec = importlib.util.spec_from_file_location("filter_preference_pg_ddl_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    output = StringIO()
    context = MigrationContext.configure(dialect=postgresql.dialect(), opts={"as_sql": True, "output_buffer": output})
    monkeypatch.setattr(migration, "op", Operations(context))
    migration.upgrade()
    expected = f"CONSTRAINT {name} UNIQUE (user_id, project_id)"
    assert expected in model_ddl and expected in output.getvalue()


def _saved_view_migration():
    path = Path(__file__).resolve().parents[2] / "langboard/migrations/versions/20261024160000-cefa345b6789.py"
    spec = importlib.util.spec_from_file_location("saved_filter_views_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


@pytest.mark.parametrize("board", ["sqlite-http", "postgresql-test"], indirect=True)
def test_saved_view_http_roundtrip_compatibility_validation_and_migration(board, monkeypatch):
    import json
    import sqlalchemy as sa
    from langboard_shared.domain.models import UserBoardFilterPreference

    _, actor, project, _, role, *_ = board
    engine = DbEngine.get_main_engine()
    monkeypatch.setattr(DbEngine, "get_readonly_engine", DbEngine.get_main_engine)
    # Exercise the additive migration against a genuine old-schema row.
    base_path = Path(__file__).resolve().parents[2] / "langboard/migrations/versions/20261021010000-b392a7f43165.py"
    spec = importlib.util.spec_from_file_location("old_filter_views_table", base_path)
    old = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old)
    migration = _saved_view_migration()
    pins = [{"key": "columns", "value": "ready"}]
    with engine.begin() as connection:
        monkeypatch.setattr(old, "op", Operations(MigrationContext.configure(connection)))
        old.upgrade()
        connection.execute(
            sa.text(
                "INSERT INTO user_board_filter_preference "
                "(id, created_at, updated_at, user_id, project_id, revision, pinned_filters) "
                "VALUES (:id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, :user_id, :project_id, 4, :pins)"
            ),
            {
                "id": 999,
                "user_id": int(actor.id),
                "project_id": int(project.id),
                "pins": '[{"key":"columns","value":"ready"}]',
            },
        )
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.upgrade()
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    access, refresh = AuthSecurity.authenticate(actor.id)
    headers = {"Authorization": f"Bearer {access}"}
    endpoint = f"/board/{project.get_uid()}/filter-preferences"
    views = [{"name": "My week", "filters": [{"key": "members", "value": "me"}, {"key": "deadlines", "value": "week"}]}]
    with TestClient(app) as client:
        client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
        initial = client.get(endpoint, headers=headers)
        assert initial.json() == {"revision": 4, "pinned_filters": pins, "saved_views": []}
        payload = {"expected_revision": 4, "pinned_filters": pins, "saved_views": views}
        saved = client.put(endpoint, headers=headers, json=payload)
        assert saved.status_code == 200, saved.text
        assert saved.json() == {"revision": 5, "pinned_filters": pins, "saved_views": views}
        assert client.get(endpoint, headers=headers).json() == saved.json()
        assert client.put(endpoint, headers=headers, json=payload).status_code == 409
        assert client.get(endpoint, headers=headers).json() == saved.json()
        invalid_views = [
            [{"name": " ", "filters": views[0]["filters"]}],
            [{"name": "x" * 65, "filters": views[0]["filters"]}],
            [views[0], {**views[0], "name": " my WEEK "}],
            [{"name": str(i), "filters": views[0]["filters"]} for i in range(17)],
            [{"name": "empty", "filters": []}],
            [{"name": "unknown", "filters": [{"key": "other", "value": "x"}]}],
            [{"name": "oversized", "filters": [{"key": "keyword", "value": str(i)} for i in range(33)]}],
            [{"name": "duplicate", "filters": views[0]["filters"] * 2}],
            [{"name": "invalid", "filters": [{"key": "deadlines", "value": "tomorrow"}]}],
            [{**views[0], "user_id": 2}],
        ]
        for invalid in invalid_views:
            result = client.put(
                endpoint, headers=headers, json={**payload, "expected_revision": 5, "saved_views": invalid}
            )
            assert result.status_code in (400, 422), result.text
            assert client.get(endpoint, headers=headers).json() == saved.json()
        # Legacy pin-only clients must retain named views on the shared revision.
        updated = client.put(endpoint, headers=headers, json={"expected_revision": 5, "pinned_filters": []})
        assert updated.json() == {"revision": 6, "pinned_filters": [], "saved_views": views}
        other_access, other_refresh = AuthSecurity.authenticate(2)
        with TestClient(app) as other:
            other.cookies.set(Env.REFRESH_TOKEN_NAME, other_refresh)
            assert other.get(endpoint, headers={"Authorization": f"Bearer {other_access}"}).json()["saved_views"] == []
        foreign = f"/board/{SnowflakeID(11).to_short_code()}/filter-preferences"
        assert client.get(foreign, headers=headers).status_code == 403
        assert client.put(foreign, headers=headers, json={**payload, "expected_revision": 6}).status_code == 403
        with engine.begin() as connection:
            monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
            with pytest.raises(RuntimeError, match="Cannot discard saved filter views"):
                migration.downgrade()
        deleted = client.put(
            endpoint, headers=headers, json={"expected_revision": 6, "pinned_filters": pins, "saved_views": []}
        )
        assert deleted.json() == {"revision": 7, "pinned_filters": pins, "saved_views": []}
        with DbSession.atomic() as db:
            role.actions = []
            db.update(role)
        assert client.put(endpoint, headers=headers, json={**payload, "expected_revision": 7}).status_code == 403
    with engine.begin() as connection:
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.downgrade()
        assert "saved_views" not in {
            column["name"] for column in sa.inspect(connection).get_columns("user_board_filter_preference")
        }
        retained = connection.execute(
            sa.text("SELECT revision, pinned_filters FROM user_board_filter_preference WHERE id=999")
        ).one()
        assert retained[0] == 7
        assert (json.loads(retained[1]) if isinstance(retained[1], str) else retained[1]) == pins
        migration.upgrade()
        assert connection.execute(sa.select(UserBoardFilterPreference.__table__.c.saved_views)).scalar_one() == []
