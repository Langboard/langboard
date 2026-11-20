"""Deterministic role mutation races against a disposable PostgreSQL database."""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event, local
from time import monotonic, sleep
from uuid import uuid4
import pytest
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.domain.models import ApiKeyRole, McpRole, Project, ProjectRole, SettingRole, User
from langboard_shared.infrastructure.repositories.factory.roles.ApiKeyRoleRepository import ApiKeyRoleRepository
from langboard_shared.infrastructure.repositories.factory.roles.McpRoleRepository import McpRoleRepository
from langboard_shared.infrastructure.repositories.factory.roles.ProjectRoleRepository import ProjectRoleRepository
from langboard_shared.infrastructure.repositories.factory.roles.SettingRoleRepository import SettingRoleRepository
from sqlalchemy import create_engine, event, select, text


ROLES = [
    (SettingRole, SettingRoleRepository),
    (ApiKeyRole, ApiKeyRoleRepository),
    (McpRole, McpRoleRepository),
    (ProjectRole, ProjectRoleRepository),
]


@pytest.fixture
def role_database(monkeypatch):
    url = os.getenv("LANGBOARD_FILE_TEST_DATABASE_URL")
    if not url:
        pytest.skip("LANGBOARD_FILE_TEST_DATABASE_URL must point to a disposable PostgreSQL database")
    admin = create_engine(url)
    assert admin.dialect.name == "postgresql"
    schema = "role_race_" + uuid4().hex
    with admin.begin() as db:
        db.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(
        url,
        connect_args={
            "application_name": "role-concurrency-test",
            "options": f"-csearch_path={schema} -cstatement_timeout=10000 -clock_timeout=5000",
        },
    )
    tables = {model.__table__ for model in (User, Project, SettingRole, ApiKeyRole, McpRole, ProjectRole)}
    pending = list(tables)
    while pending:
        for fk in pending.pop().foreign_keys:
            if fk.column.table not in tables:
                tables.add(fk.column.table)
                pending.append(fk.column.table)
    try:
        User.metadata.create_all(engine, tables=list(tables))
        monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
        monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
        with DbSession.atomic() as db:
            users = [
                User(email=f"race{i}@example.invalid", password="test", firstname="Race", lastname=str(i))
                for i in range(2)
            ]
            db.insert_all(users)
            projects = [Project(owner_id=users[0].id, title=f"Scope {i}") for i in range(2)]
            db.insert_all(projects)
        yield engine, users, projects
    finally:
        engine.dispose()
        with admin.begin() as db:
            db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def run_race(engine, model, first, second):
    """Pause the first real role lookup; admit the rival before releasing it.

    Before the fix the rival also reads an absent/stale role. After the fix its
    user lock waits in PostgreSQL. Neither scheduling outcome relies on sleep.
    """
    state = local()
    first_read, second_read, release = Event(), Event(), Event()
    second_pid = []

    def before(conn, cursor, statement, parameters, context, many):
        if getattr(state, "worker", None) == 2:
            second_pid[:] = [cursor.connection.info.backend_pid]

    def after(conn, cursor, statement, parameters, context, many):
        compiled = context.compiled
        query = compiled.statement if compiled else None
        if query is None or not getattr(query, "is_select", False):
            return
        if model.__table__ not in query.get_final_froms():
            return
        if getattr(state, "worker", None) == 1:
            first_read.set()
            assert release.wait(15), "First role lookup was never released"
        elif getattr(state, "worker", None) == 2:
            second_read.set()

    def invoke(worker, operation):
        state.worker = worker
        return operation()

    event.listen(engine, "before_cursor_execute", before)
    event.listen(engine, "after_cursor_execute", after)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            one = pool.submit(invoke, 1, first)
            assert first_read.wait(10), "First mutation did not read its role"
            two = pool.submit(invoke, 2, second)
            try:
                deadline = monotonic() + 10
                with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as observer:
                    while not second_read.is_set():
                        if (
                            second_pid
                            and observer.execute(
                                text("SELECT wait_event_type FROM pg_stat_activity WHERE pid=:pid"),
                                {"pid": second_pid[0]},
                            ).scalar_one_or_none()
                            == "Lock"
                        ):
                            break
                        assert monotonic() < deadline, "Rival neither read the role nor waited for a DB lock"
                        sleep(0.01)
            finally:
                release.set()
            return one.result(timeout=15), two.result(timeout=15)
    finally:
        release.set()
        event.remove(engine, "before_cursor_execute", before)
        event.remove(engine, "after_cursor_execute", after)


@pytest.mark.parametrize("model,repository", ROLES)
@pytest.mark.parametrize("method", ["grant", "grant_all", "grant_default"])
def test_concurrent_grants_create_one_authority_row(role_database, model, repository, method):
    engine, users, projects = role_database
    repo = repository(None, None)
    scope = {"user_id": users[0].id}
    if model is ProjectRole:
        scope["project_id"] = projects[0].id
    action = model.get_all_actions()[0].value
    args = {**scope, "actions": [action]} if method == "grant" else scope
    first_args = {**args, "actions": []} if method == "grant" else args
    first, second = run_race(
        engine, model, lambda: getattr(repo, method)(**first_args), lambda: getattr(repo, method)(**args)
    )
    with engine.connect() as db:
        rows = db.execute(select(model.id, model.actions).where(model.user_id == users[0].id)).all()
    assert len(rows) == 1, f"Concurrent {method} created duplicate authority rows: {rows}"
    assert first.id == second.id == rows[0].id
    expected = (
        [action]
        if method == "grant"
        else ["*"]
        if method == "grant_all"
        else [a.value for a in model.get_default_actions()]
    )
    assert rows[0].actions == expected


@pytest.mark.parametrize("model,repository", ROLES)
def test_existing_update_then_withdraw_is_serialized(role_database, model, repository):
    engine, users, projects = role_database
    repo = repository(None, None)
    scope = {"user_id": users[0].id}
    if model is ProjectRole:
        scope["project_id"] = projects[0].id
    original = repo.grant(**scope, actions=[])
    updated, withdrawn = run_race(engine, model, lambda: repo.grant_all(**scope), lambda: repo.withdraw(**scope))
    assert updated.id == withdrawn.id == original.id
    assert withdrawn.actions == ["*"]
    with engine.connect() as db:
        assert db.execute(select(model.id).where(model.user_id == users[0].id)).all() == []
    assert repo.withdraw(**scope) is None


def test_project_scopes_users_and_transaction_effects_stay_separate(role_database):
    engine, users, projects = role_database
    repo = ProjectRoleRepository(None, None)
    a = {"user_id": users[0].id, "project_id": projects[0].id}
    b = {"user_id": users[0].id, "project_id": projects[1].id}
    first, second = run_race(
        engine, ProjectRole, lambda: repo.grant(**a, actions=["read"]), lambda: repo.grant(**b, actions=["card_update"])
    )
    _, third = run_race(
        engine,
        ProjectRole,
        lambda: repo.grant(**a, actions=["read"]),
        lambda: repo.grant(user_id=users[1].id, project_id=projects[0].id, actions=[]),
    )
    assert len({first.id, second.id, third.id}) == 3
    effects = []
    with pytest.raises(RuntimeError, match="rollback"):
        with DbSession.atomic() as db:
            repo.grant_all(**a)
            db.after_commit(lambda: effects.append("rolled-back"))
            raise RuntimeError("rollback")
    assert effects == []
    with DbSession.atomic() as db:
        assert repo.grant(**a, actions=["read"]).id == first.id
        db.after_commit(lambda: effects.append("committed"))
        assert effects == []
    assert effects == ["committed"]
    with engine.connect() as db:
        rows = db.execute(select(ProjectRole.user_id, ProjectRole.project_id, ProjectRole.actions)).all()
    assert {(row.user_id, row.project_id): row.actions for row in rows} == {
        (users[0].id, projects[0].id): ["read"],
        (users[0].id, projects[1].id): ["card_update"],
        (users[1].id, projects[0].id): [],
    }


@pytest.mark.parametrize("model,repository", ROLES)
def test_withdraw_then_grant_creates_fresh_authority(role_database, model, repository):
    engine, users, projects = role_database
    repo = repository(None, None)
    scope = {"user_id": users[0].id}
    if model is ProjectRole:
        scope["project_id"] = projects[0].id
    original = repo.grant(**scope, actions=[])
    removed, granted = run_race(engine, model, lambda: repo.withdraw(**scope), lambda: repo.grant_all(**scope))
    assert removed.id == original.id
    assert granted.id != original.id
    with engine.connect() as db:
        rows = db.execute(select(model.id, model.actions).where(model.user_id == users[0].id)).all()
    assert rows == [(granted.id, ["*"])]
