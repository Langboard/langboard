"""Actual database authority and transaction proof for organization project bindings."""

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from types import SimpleNamespace
from uuid import uuid4
import pytest
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import Organization, Project, User
from langboard_shared.domain.services import DomainService
from sqlalchemy import create_engine, event, select, text, update


@pytest.fixture(params=["sqlite", "postgresql"])
def binding_database(request, tmp_path, monkeypatch):
    admin = None
    schema = None
    if request.param == "postgresql":
        url = os.getenv("LANGBOARD_FILE_TEST_DATABASE_URL")
        if not url:
            pytest.skip("Requires an explicitly disposable PostgreSQL database")
        admin = create_engine(url)
        assert admin.dialect.name == "postgresql"
        schema = "organization_binding_" + uuid4().hex
        with admin.begin() as db:
            db.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_engine(
            url, connect_args={"options": f"-csearch_path={schema} -cstatement_timeout=10000 -clock_timeout=5000"}
        )
    else:
        engine = create_engine(f"sqlite:///{tmp_path / 'bindings.sqlite'}")
    try:
        for model in (User, Organization, Project):
            model.__table__.create(engine)
        monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)

        def no_replica():
            raise AssertionError("Binding authority must use the current primary transaction")

        monkeypatch.setattr(DbEngine, "get_readonly_engine", no_replica)
        with DbSession.atomic() as db:
            users = []
            for index in range(2):
                user = User(
                    firstname="Binding",
                    lastname=str(index),
                    username=f"binding-{index}",
                    email=f"binding-{index}@example.invalid",
                    password="fixture-only",
                    activated_at=SafeDateTime.now(),
                )
                db.insert(user)
                users.append(user)
            organizations = []
            for index in range(3):
                organization = Organization(
                    name=f"Organization {index}",
                    slug=f"organization-{index}",
                    owner_user_id=users[index == 2].id,
                    # Keep conflicting legacy identities without violating the active tenant index.
                    is_active=index == 0,
                )
                db.insert(organization)
                organizations.append(organization)
            projects = []
            for index in range(2):
                project = Project(title=f"Project {index}", owner_id=users[index].id)
                db.insert(project)
                projects.append(project)
        service = DomainService()
        yield SimpleNamespace(
            engine=engine,
            owner=users[0],
            other=users[1],
            organizations=organizations,
            projects=projects,
            service=service,
        )
        service.close()
    finally:
        engine.dispose()
        if admin is not None:
            with admin.begin() as db:
                db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            admin.dispose()


def binding_snapshot(f):
    with f.engine.connect() as db:
        return (
            list(
                db.execute(
                    select(
                        Project.id, Project.owner_id, Project.organization_id, Project.title, Project.updated_at
                    ).order_by(Project.id)
                )
            ),
            list(
                db.execute(
                    select(Organization.id, Organization.owner_user_id, Organization.is_active).order_by(
                        Organization.id
                    )
                )
            ),
            list(db.execute(select(User.id, User.activated_at, User.deleted_at).order_by(User.id))),
        )


def test_foreign_project_cannot_be_assigned(binding_database):
    f = binding_database
    before = binding_snapshot(f)
    try:
        f.service.organization.assign_project(f.owner, f.organizations[0], f.projects[1])
    except ValueError:
        pass
    after = binding_snapshot(f)
    assert after == before, f"Unauthorized actual DB mutation: before={before[0]}, after={after[0]}"


def test_existing_other_organization_cannot_be_overwritten(binding_database):
    f = binding_database
    with f.engine.begin() as db:
        db.execute(update(Project).where(Project.id == f.projects[0].id).values(organization_id=f.organizations[1].id))
    before = binding_snapshot(f)
    try:
        f.service.organization.assign_project(f.owner, f.organizations[0], f.projects[0].get_uid())
    except ValueError:
        pass
    after = binding_snapshot(f)
    assert after == before, f"Unauthorized actual DB mutation: before={before[0]}, after={after[0]}"


@pytest.mark.parametrize("input_kind", ["model", "id", "uid"])
def test_authorized_assign_idempotent_detach(binding_database, input_kind):
    f = binding_database
    organization, project = f.organizations[0], f.projects[0]
    convert = {"model": lambda value: value, "id": lambda value: value.id, "uid": lambda value: value.get_uid()}[
        input_kind
    ]
    original = binding_snapshot(f)
    assigned = f.service.organization.assign_project(f.owner, convert(organization), convert(project))
    assert assigned.id == organization.id
    bound = binding_snapshot(f)
    assert bound[0][0].organization_id == organization.id
    assert bound[0][1:] == original[0][1:]
    assert bound[1:] == original[1:]
    statements = []

    def record(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(f.engine, "before_cursor_execute", record)
    try:
        assert (
            f.service.organization.assign_project(f.owner, convert(organization), convert(project)).id
            == organization.id
        )
    finally:
        event.remove(f.engine, "before_cursor_execute", record)
    assert not any(statement.lstrip().upper().startswith("UPDATE") for statement in statements)
    assert binding_snapshot(f) == bound
    assert (
        f.service.organization.unassign_project(f.owner, convert(organization), convert(project)).id == organization.id
    )
    detached = binding_snapshot(f)
    assert detached[0][0].organization_id is None
    assert detached[0][0].owner_id == original[0][0].owner_id
    assert detached[0][0].title == original[0][0].title
    assert detached[0][1:] == original[0][1:]
    assert detached[1:] == original[1:]


@pytest.mark.parametrize("operation", ["assign", "unassign"])
@pytest.mark.parametrize(
    "change",
    [
        "inactive_actor",
        "deleted_actor",
        "missing_actor",
        "organization_owner",
        "inactive_organization",
        "missing_organization",
        "project_owner",
        "missing_project",
        "other_binding",
        "foreign_organization",
    ],
)
def test_current_rows_override_stale_models(binding_database, operation, change):
    f = binding_database
    organization, project = f.organizations[0], f.projects[0]
    # Retained model snapshots deliberately disagree with the authoritative rows below.
    with f.engine.begin() as db:
        if operation == "unassign":
            db.execute(update(Project).where(Project.id == project.id).values(organization_id=organization.id))
        if change == "inactive_actor":
            db.execute(update(User).where(User.id == f.owner.id).values(activated_at=None))
        elif change == "deleted_actor":
            db.execute(update(User).where(User.id == f.owner.id).values(deleted_at=SafeDateTime.now()))
        elif change == "organization_owner":
            db.execute(update(Organization).where(Organization.id == organization.id).values(owner_user_id=f.other.id))
        elif change == "inactive_organization":
            db.execute(update(Organization).where(Organization.id == organization.id).values(is_active=False))
        elif change == "project_owner":
            db.execute(update(Project).where(Project.id == project.id).values(owner_id=f.other.id))
        elif change == "other_binding":
            db.execute(update(Project).where(Project.id == project.id).values(organization_id=f.organizations[1].id))
        elif change == "foreign_organization":
            organization = f.organizations[2]
        elif change == "missing_actor":
            f.owner.id = 42
        elif change == "missing_organization":
            organization.id = 42
        elif change == "missing_project":
            project.id = 42
    before = binding_snapshot(f)
    if operation == "assign":
        with pytest.raises(ValueError):
            f.service.organization.assign_project(f.owner, organization, project)
    else:
        assert f.service.organization.unassign_project(f.owner, organization, project) is None
    assert binding_snapshot(f) == before


def test_unbound_project_detachment_is_noop(binding_database):
    f = binding_database
    before = binding_snapshot(f)
    assert f.service.organization.unassign_project(f.owner, f.organizations[0], f.projects[0]) is None
    assert binding_snapshot(f) == before


@pytest.mark.parametrize("operation", ["assign", "unassign"])
def test_binding_mutation_rolls_back_after_database_write(binding_database, operation):
    f = binding_database
    if operation == "unassign":
        with f.engine.begin() as db:
            db.execute(
                update(Project).where(Project.id == f.projects[0].id).values(organization_id=f.organizations[0].id)
            )
    before = binding_snapshot(f)
    writes = []

    def fail_after_write(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("UPDATE"):
            writes.append(statement)
            raise RuntimeError("injected after native database write")

    event.listen(f.engine, "after_cursor_execute", fail_after_write)
    try:
        with pytest.raises(RuntimeError, match="injected after native database write"):
            getattr(f.service.organization, f"{operation}_project")(f.owner, f.organizations[0], f.projects[0])
    finally:
        event.remove(f.engine, "after_cursor_execute", fail_after_write)
    assert len(writes) == 1
    assert binding_snapshot(f) == before


@pytest.mark.parametrize("operation", ["assign", "unassign"])
def test_binding_mutation_joins_outer_rollback(binding_database, operation):
    f = binding_database
    if operation == "unassign":
        with f.engine.begin() as db:
            db.execute(
                update(Project).where(Project.id == f.projects[0].id).values(organization_id=f.organizations[0].id)
            )
    before = binding_snapshot(f)
    with pytest.raises(RuntimeError, match="outer rollback"):
        with DbSession.atomic():
            getattr(f.service.organization, f"{operation}_project")(f.owner, f.organizations[0], f.projects[0])
            raise RuntimeError("outer rollback")
    assert binding_snapshot(f) == before


@pytest.mark.parametrize("operation", ["assign", "unassign"])
@pytest.mark.parametrize("resource", ["actor", "organization", "project"])
def test_postgresql_binding_rechecks_locked_authority(binding_database, operation, resource):
    f = binding_database
    if f.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL required to prove native row-lock interleaving")
    if operation == "unassign":
        with f.engine.begin() as db:
            db.execute(
                update(Project).where(Project.id == f.projects[0].id).values(organization_id=f.organizations[0].id)
            )
    model, row_id, changes = {
        "actor": (User, f.owner.id, {"activated_at": None}),
        "organization": (Organization, f.organizations[0].id, {"owner_user_id": f.other.id}),
        "project": (Project, f.projects[0].id, {"owner_id": f.other.id}),
    }[resource]
    reached_lock = threading.Event()
    contender_pid = []

    def before_sql(connection, _cursor, statement, _parameters, _context, _many):
        if "FOR UPDATE" in statement and model.__tablename__ in statement:
            contender_pid.append(connection.connection.driver_connection.info.backend_pid)
            reached_lock.set()

    def contend():
        with closing(DomainService()) as service:
            if operation == "assign":
                with pytest.raises(ValueError):
                    service.organization.assign_project(f.owner, f.organizations[0], f.projects[0])
            else:
                assert service.organization.unassign_project(f.owner, f.organizations[0], f.projects[0]) is None

    event.listen(f.engine, "before_cursor_execute", before_sql)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with f.engine.begin() as holder:
                holder.execute(update(model).where(model.id == row_id).values(**changes))
                future = pool.submit(contend)
                assert reached_lock.wait(4), "Binding operation never attempted its authority row lock"
                deadline = time.monotonic() + 4
                while time.monotonic() < deadline:
                    with f.engine.connect() as db:
                        waiting = db.execute(
                            text("SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid"),
                            {"pid": contender_pid[0]},
                        ).scalar_one()
                    if waiting == "Lock":
                        break
                    threading.Event().wait(0.02)
                else:
                    pytest.fail("Binding operation did not wait for current PostgreSQL authority")
                assert not future.done()
            future.result(timeout=10)
    finally:
        event.remove(f.engine, "before_cursor_execute", before_sql)
    with f.engine.connect() as db:
        assert db.execute(select(Project.organization_id).where(Project.id == f.projects[0].id)).scalar_one() == (
            f.organizations[0].id if operation == "unassign" else None
        )
        assert db.execute(select(Project.organization_id).where(Project.id == f.projects[1].id)).scalar_one() is None
