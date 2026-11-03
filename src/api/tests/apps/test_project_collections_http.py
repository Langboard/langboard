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


@pytest.mark.parametrize("board_count", [8, 128, 1024])
def test_collection_board_authority_queries_are_bounded(board, board_count):
    from langboard_shared.domain.models import UserProjectCollection
    from langboard_shared.domain.services.ProjectCollections import project_collections
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    _, actor, *_ = board
    engine = DbEngine.get_main_engine()
    UserProjectCollection.__table__.create(engine)
    with Session(engine, expire_on_commit=False) as db:
        boards = [Project(id=1000 + i, owner_id=2, title=f"Source {i}") for i in range(board_count)]
        db.add_all(boards)
        db.flush()
        db.add_all(
            [
                row
                for project in boards
                for row in (
                    ProjectAssignedUser(id=2000 + int(project.id), project_id=project.id, user_id=actor.id),
                    ProjectRole(id=4000 + int(project.id), project_id=project.id, user_id=actor.id, actions=["read"]),
                )
            ]
        )
        db.commit()
    collections = [
        {
            "uid": str(uuid4()),
            "name": f"Sources {start}",
            "board_uids": [p.get_uid() for p in boards[start : start + 128]],
        }
        for start in range(0, board_count, 128)
    ]
    selects = []

    def count_selects(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            selects.append(statement)

    event.listen(engine, "before_cursor_execute", count_selects)
    try:
        result = project_collections(actor, collections=collections, expected_revision=0)
    finally:
        event.remove(engine, "before_cursor_execute", count_selects)
    assert result == {"revision": 1, "collections": [{**item, "starred": False} for item in collections]}
    assert len(selects) <= 5, f"Authority queries grew with {board_count} boards: {len(selects)}"


@pytest.mark.parametrize("denied", ["membership", "role", "read", "conflicting_role", "deleted", "missing"])
def test_collection_batch_rejects_one_unauthorized_reference_without_saving(board, denied):
    from langboard_shared.core.db import SqlBuilder
    from langboard_shared.core.types import SafeDateTime
    from langboard_shared.domain.models import UserProjectCollection
    from langboard_shared.domain.services.AppGovernance import AppGovernanceDenied
    from langboard_shared.domain.services.ProjectCollections import project_collections

    _, actor, project, *_ = board
    engine = DbEngine.get_main_engine()
    UserProjectCollection.__table__.create(engine)
    with DbSession.atomic() as db:
        if denied != "membership":
            db.insert(ProjectAssignedUser(id=21, project_id=11, user_id=actor.id))
        if denied != "role":
            db.insert(ProjectRole(id=31, project_id=11, user_id=actor.id, actions=[] if denied == "read" else ["read"]))
        if denied == "conflicting_role":
            db.insert(ProjectRole(id=32, project_id=11, user_id=actor.id, actions=[]))
        if denied == "deleted":
            foreign = db.exec(SqlBuilder.select.table(Project).where(Project.id == 11)).first()
            foreign.deleted_at = SafeDateTime.now()
            db.update(foreign)
    target = SnowflakeID(99 if denied == "missing" else 11).to_short_code()
    collections = [{"uid": str(uuid4()), "name": "Mixed references", "board_uids": [project.get_uid(), target]}]
    with pytest.raises(AppGovernanceDenied):
        project_collections(actor, collections=collections, expected_revision=0)
    assert project_collections(actor) == {"revision": 0, "collections": []}


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
    collection = {"uid": str(uuid4()), "name": "ERP", "board_uids": [project.get_uid()], "starred": False}
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
            *({"expected_revision": 1, "collections": [{**collection, "starred": value}]} for value in (1, "true", None)),
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


@pytest.mark.parametrize("board", ["sqlite-http", "postgresql-test"], indirect=True)
def test_native_source_creation_survives_collection_save_rollback_and_retry(board, monkeypatch):
    import langboard.routes.dashboard.DashboardApi  # noqa: F401
    from langboard_shared.domain import models
    from langboard_shared.publishers import ProjectColumnPublisher
    from langboard_shared.tasks.activities import ProjectActivityTask, ProjectColumnActivityTask
    from langboard_shared.tasks.bots import ProjectColumnBotTask

    _, actor, *_ = board
    engine = DbEngine.get_main_engine()
    monkeypatch.setattr(DbEngine, "get_readonly_engine", DbEngine.get_main_engine)
    required = {model.__table__ for model in (
        models.UserProjectCollection, models.ProjectTemplate, models.ProjectLabel, models.InternalBot,
        models.ProjectAssignedInternalBot, models.ProjectEmailNotificationPolicy,
        models.ProjectEmailNotificationRecipient, models.Card,
    )}
    pending = list(required)
    while pending:
        for key in pending.pop().foreign_keys:
            if key.column.table not in required:
                required.add(key.column.table)
                pending.append(key.column.table)
    models.Project.metadata.create_all(engine, tables=list(required))
    for owner, method in (
        (ProjectActivityTask, "project_created"), (ProjectColumnPublisher, "created"),
        (ProjectColumnActivityTask, "project_column_created"), (ProjectColumnBotTask, "project_column_created"),
    ):
        monkeypatch.setattr(owner, method, lambda *_args: None)
    with DbSession.atomic() as db:
        db.insert(models.ProjectTemplate(name="Collection source", columns=[{"name": "Inbox", "workflow_stage": "active"}]))
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    access, refresh = AuthSecurity.authenticate(actor.id)
    headers = {"Authorization": f"Bearer {access}"}
    endpoint = "/account/project-collections"
    with TestClient(app, raise_server_exceptions=False) as client:
        client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
        created = client.post("/dashboard/projects/new", json={
            "title": "Created source", "description": "Keep original body", "project_type": "Other",
            "template_name": "Collection source",
        }, headers=headers)
        assert created.status_code == 201, created.text
        source_uid = created.json()["project_uid"]
        assert client.get(endpoint, headers=headers).json() == {"revision": 0, "collections": []}

        def source_snapshot():
            with engine.connect() as connection:
                return {
                    model.__tablename__: connection.execute(select(model.__table__)).all()
                    for model in (Project, models.ProjectColumn, models.ProjectLabel, ProjectAssignedUser, ProjectRole)
                }

        snapshot = source_snapshot()
        source = next(row for row in snapshot["project"] if row.id == SnowflakeID.from_short_code(source_uid))
        assert source.title == "Created source" and source.description == "Keep original body"
        collection = {"uid": str(uuid4()), "name": "Personal source", "board_uids": [source_uid], "starred": False}
        payload = {"expected_revision": 0, "collections": [collection]}
        original_insert = DbSession.insert

        def fail_after_insert(session, row, *args, **kwargs):
            result = original_insert(session, row, *args, **kwargs)
            if isinstance(row, models.UserProjectCollection):
                raise RuntimeError("collection persistence failed after insert")
            return result

        with monkeypatch.context() as failure:
            failure.setattr(DbSession, "insert", fail_after_insert)
            assert client.put(endpoint, json=payload, headers=headers).status_code == 500
        assert client.get(endpoint, headers=headers).json() == {"revision": 0, "collections": []}
        assert source_snapshot() == snapshot
        saved = client.put(endpoint, json=payload, headers=headers)
        assert saved.status_code == 200, saved.text
        assert saved.json() == {"revision": 1, "collections": [collection]}
        assert source_snapshot() == snapshot
        assert client.put(endpoint, json=payload, headers=headers).status_code == 409
        assert client.get(endpoint, headers=headers).json() == saved.json()
        assert source_snapshot() == snapshot


@pytest.mark.parametrize("board", ["sqlite-http", "postgresql-test"], indirect=True)
def test_collection_favorite_normalizes_history_without_rewrite_and_is_independent(board, monkeypatch):
    import langboard.routes.dashboard.DashboardApi  # noqa: F401
    from langboard_shared.domain.models import UserProjectCollection

    _, actor, project, _, role, *_ = board
    engine = DbEngine.get_main_engine()
    monkeypatch.setattr(DbEngine, "get_readonly_engine", DbEngine.get_main_engine)
    UserProjectCollection.__table__.create(engine)
    historical = {"uid": str(uuid4()), "name": "Historical", "board_uids": [project.get_uid()]}
    empty = {"uid": str(uuid4()), "name": "Empty", "board_uids": []}
    with DbSession.atomic() as db:
        db.insert(UserProjectCollection(user_id=actor.id, collections=[historical, empty], revision=7))

    def snapshot(model):
        with engine.connect() as connection:
            return connection.execute(select(model.__table__)).all()

    raw = snapshot(UserProjectCollection)
    boards = snapshot(Project)
    board_favorites = snapshot(ProjectAssignedUser)
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    access, refresh = AuthSecurity.authenticate(actor.id)
    headers = {"Authorization": f"Bearer {access}"}
    endpoint = "/account/project-collections"
    with TestClient(app) as client:
        client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
        normalized = [{**item, "starred": False} for item in (historical, empty)]
        assert client.get(endpoint, headers=headers).json() == {"revision": 7, "collections": normalized}
        assert snapshot(UserProjectCollection) == raw
        starred = [{**historical, "starred": True}, empty]
        saved = client.put(endpoint, headers=headers, json={"expected_revision": 7, "collections": starred})
        assert saved.status_code == 200, saved.text
        normalized[0]["starred"] = True
        assert saved.json() == {"revision": 8, "collections": normalized}
        assert client.get(endpoint, headers=headers).json() == saved.json()
        assert snapshot(UserProjectCollection)[0].collections == normalized
        assert snapshot(Project) == boards
        assert snapshot(ProjectAssignedUser) == board_favorites
        assert client.put(f"/dashboard/projects/{project.get_uid()}/star", headers=headers).status_code == 200
        assert snapshot(ProjectAssignedUser)[0].starred is True
        assert client.get(endpoint, headers=headers).json() == saved.json()
        assert client.put(endpoint, headers=headers, json={"expected_revision": 7, "collections": []}).status_code == 409
        # Revocation restricts board actions, while the owner may unfavorite the personal collection.
        with DbSession.atomic() as db:
            db.delete(role)
        normalized[0]["starred"] = False
        unfavorited = client.put(endpoint, headers=headers, json={"expected_revision": 8, "collections": normalized})
        assert unfavorited.status_code == 200, unfavorited.text
        assert unfavorited.json() == {"revision": 9, "collections": normalized}
        assert snapshot(Project) == boards
        assert client.get(endpoint, headers=headers).json() == unfavorited.json()
        assert snapshot(ProjectAssignedUser)[0].starred is True


@pytest.mark.parametrize("board", ["postgresql-test"], indirect=True)
@pytest.mark.parametrize("revoked", ["role", "actor", "project", "membership"])
def test_native_collection_save_refreshes_preloaded_authority_after_separate_commit(board, revoked):
    from langboard_shared.core.db import SqlBuilder
    from langboard_shared.core.types import SafeDateTime
    from langboard_shared.domain.models import User, UserProjectCollection
    from langboard_shared.domain.services.AppGovernance import AppGovernanceDenied
    from langboard_shared.domain.services.ProjectCollections import project_collections
    from sqlalchemy import delete, update

    _, actor, project, member, role, *_ = board
    engine = DbEngine.get_main_engine()
    UserProjectCollection.__table__.create(engine)
    collection = {"uid": str(uuid4()), "name": "Current authority", "board_uids": [project.get_uid()]}
    mutations = {
        "role": update(ProjectRole).where(ProjectRole.id == role.id).values(actions=[]),
        "actor": update(User).where(User.id == actor.id).values(activated_at=None),
        "project": update(Project).where(Project.id == project.id).values(deleted_at=SafeDateTime.now()),
        "membership": delete(ProjectAssignedUser).where(ProjectAssignedUser.id == member.id),
    }
    with DbSession.atomic() as db:
        # Keep strong references: SQLAlchemy's identity map holds objects weakly.
        preloaded = [
            db.exec(SqlBuilder.select.table(model).where(model.id == row.id)).first()
            for model, row in ((User, actor), (Project, project), (ProjectAssignedUser, member), (ProjectRole, role))
        ]
        assert all(row is not None for row in preloaded)
        assert project_collections(actor) == {"revision": 0, "collections": []}
        with engine.begin() as connection:
            connection.execute(mutations[revoked])
        with pytest.raises(AppGovernanceDenied):
            project_collections(actor, collections=[collection], expected_revision=0)
    with engine.connect() as connection:
        assert connection.execute(select(UserProjectCollection.__table__)).all() == []


@pytest.mark.parametrize("board", ["postgresql-test"], indirect=True)
@pytest.mark.parametrize("write", [False, True])
def test_shared_preferences_recheck_preloaded_actor_after_separate_commit(board, write):
    from langboard_shared.core.db import SqlBuilder
    from langboard_shared.domain.models import User
    from langboard_shared.domain.services.AppGovernance import AppGovernanceDenied
    from langboard_shared.domain.services.UserAppPreferences import app_preferences
    from sqlalchemy import update

    _, actor, *_ = board
    engine = DbEngine.get_main_engine()
    UserAppPreference.__table__.create(engine)
    with DbSession.atomic() as db:
        current = db.exec(SqlBuilder.select.table(User).where(User.id == actor.id)).first()
        assert current.activated_at is not None
        assert app_preferences(actor) == {"revision": 0, "pinned_apps": []}
        with engine.begin() as connection:
            connection.execute(update(User).where(User.id == actor.id).values(activated_at=None))
        with pytest.raises(AppGovernanceDenied):
            app_preferences(actor, **({"pinned_apps": [], "expected_revision": 0} if write else {}))
    with engine.connect() as connection:
        assert connection.execute(select(UserAppPreference.__table__)).all() == []
