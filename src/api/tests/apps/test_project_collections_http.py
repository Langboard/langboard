"""Personal collections never become board grants or overwrite other devices."""

import importlib.util
from pathlib import Path
from uuid import uuid4
import langboard.routes.account.AccountApi  # noqa: F401
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
from langboard_shared.domain.models import Project, ProjectAssignedUser, ProjectRole, UserAppPreference
from langboard_shared.Env import Env
from sqlalchemy import select


@pytest.mark.parametrize("board", ["sqlite-http", "postgresql-test"], indirect=True)
def test_collection_persistence_isolation_authority_revision_and_migration(board, monkeypatch):
    _, actor, project, _, role, *_ = board
    engine = DbEngine.get_main_engine()
    monkeypatch.setattr(DbEngine, "get_readonly_engine", DbEngine.get_main_engine)
    UserAppPreference.__table__.create(engine)
    with DbSession.atomic() as db:
        db.insert(UserAppPreference(user_id=actor.id, pinned_apps=[{"key": "existing-app", "name": "Existing pin"}]))

    def existing_data():
        with engine.connect() as connection:
            return {
                model.__tablename__: connection.execute(select(model.__table__)).all()
                for model in (Project, ProjectAssignedUser, ProjectRole, UserAppPreference)
            }

    before = existing_data()
    path = Path(__file__).resolve().parents[2] / "langboard/migrations/versions/20261022040000-f86d4ce73a02.py"
    spec = importlib.util.spec_from_file_location("collections_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine.begin() as connection:
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.upgrade()
    assert existing_data() == before
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    access, refresh = AuthSecurity.authenticate(actor.id)
    headers = {"Authorization": f"Bearer {access}"}
    endpoint = "/account/project-collections"
    collection = {"uid": str(uuid4()), "name": "ERP", "board_uids": [project.get_uid()]}
    with TestClient(app) as client:
        assert client.get(endpoint).status_code == 401
        client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
        initial = client.get(endpoint, headers=headers)
        assert initial.status_code == 200, initial.text
        assert initial.headers["Cache-Control"] == "no-store"
        assert initial.json() == {"revision": 0, "collections": []}
        saved = client.put(endpoint, json={"expected_revision": 0, "collections": [collection]}, headers=headers)
        assert saved.status_code == 200, saved.text
        assert saved.json() == {"revision": 1, "collections": [collection]}
        assert existing_data() == before
        with TestClient(app) as device:
            device.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
            assert device.get(endpoint, headers=headers).json() == saved.json()
            assert device.put(endpoint, json={"expected_revision": 0, "collections": []}, headers=headers).status_code == 409
        other_access, other_refresh = AuthSecurity.authenticate(2)
        with TestClient(app) as other:
            other.cookies.set(Env.REFRESH_TOKEN_NAME, other_refresh)
            assert other.get(endpoint, headers={"Authorization": f"Bearer {other_access}"}).json() == {"revision": 0, "collections": []}
        foreign = {**collection, "board_uids": [SnowflakeID(11).to_short_code()]}
        assert client.put(endpoint, json={"expected_revision": 1, "collections": [foreign]}, headers=headers).status_code == 403
        for invalid in (
            {"expected_revision": True, "collections": [collection]},
            {"expected_revision": 1, "collections": [collection, collection]},
            {"expected_revision": 1, "collections": [{**collection, "board_uids": collection["board_uids"] * 2}]},
            {"expected_revision": 1, "collections": [{**collection, "board_uids": ["../private"]}]},
            {"expected_revision": 1, "collections": [{**collection, "name": " "}]},
            {"expected_revision": 1, "collections": [{**collection, "uid": "not-a-uuid"}]},
            {"expected_revision": 1, "collections": [{**collection, "title": "forbidden"}]},
            {"expected_revision": 1, "collections": [collection], "user_id": 2},
        ):
            assert client.put(endpoint, json=invalid, headers=headers).status_code in (400, 422)
        assert client.get(endpoint, headers=headers).json() == saved.json()
        # Permissions can be revoked after linking. Saved references do not expose
        # names or content, and can still be kept/reordered/removed by their owner.
        with DbSession.atomic() as db:
            db.delete(role)
        renamed = {**collection, "name": "Retained references"}
        retained = client.put(endpoint, json={"expected_revision": 1, "collections": [renamed]}, headers=headers)
        assert retained.status_code == 200, retained.text
        assert retained.json() == {"revision": 2, "collections": [renamed]}
        assert client.get(endpoint, headers=headers).json() == retained.json()
        with engine.begin() as connection:
            monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
            with pytest.raises(RuntimeError, match="Cannot discard"):
                migration.downgrade()
        assert client.put(endpoint, json={"expected_revision": 2, "collections": []}, headers=headers).json() == {"revision": 3, "collections": []}
        assert client.put(endpoint, json={"expected_revision": 3, "collections": [collection]}, headers=headers).status_code == 403
        with DbSession.atomic() as db:
            actor.activated_at = None
            db.update(actor)
        assert client.get(endpoint, headers=headers).status_code in (401, 403)


@pytest.mark.parametrize("board", ["postgresql-test"], indirect=True)
@pytest.mark.parametrize("existing", [False, True])
def test_concurrent_collection_save_has_one_winner(board, existing):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from langboard_shared.domain.models import UserProjectCollection
    from langboard_shared.domain.services.ProjectCollections import project_collections
    from langboard_shared.domain.services.UserAppPreferences import PreferenceConflict

    _, actor, *_ = board
    UserProjectCollection.__table__.create(DbEngine.get_main_engine())
    revision = 0
    if existing:
        revision = project_collections(actor, collections=[], expected_revision=0)["revision"]
    barrier = Barrier(2)

    def save(name):
        barrier.wait(timeout=10)
        try:
            return project_collections(actor, collections=[{"uid": str(uuid4()), "name": name, "board_uids": []}], expected_revision=revision)
        except PreferenceConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, ["Device A", "Device B"]))
    assert results.count("conflict") == 1
    winner = next(item for item in results if isinstance(item, dict))
    assert winner["revision"] == revision + 1
    assert project_collections(actor) == winner
