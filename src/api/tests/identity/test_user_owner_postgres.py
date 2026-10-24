"""Real PostgreSQL interleaving of organization creation and owner lifecycle changes."""

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4
import pytest
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import Organization, User, UserProfile
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.factory.UserService import UserOwnershipError
from langboard_shared.Env import Env
from langboard_shared.publishers import AppSettingPublisher, UserPublisher
from sqlalchemy import create_engine, event, func, select, text


@pytest.fixture
def owner_database(monkeypatch, tmp_path):
    url = os.getenv("LANGBOARD_FILE_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requires an explicitly disposable PostgreSQL database")
    admin = create_engine(url)
    assert admin.dialect.name == "postgresql", "SQLite does not prove row-lock behavior"
    schema = "iam_owner_" + uuid4().hex
    with admin.begin() as db:
        db.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(
        url,
        connect_args={"options": f"-csearch_path={schema} -cstatement_timeout=10000 -clock_timeout=5000"},
    )
    try:
        for model in (User, UserProfile, Organization):
            model.__table__.create(engine)
        monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)

        def no_replica():
            raise AssertionError("Owner lifecycle checks must use the primary transaction")

        monkeypatch.setattr(DbEngine, "get_readonly_engine", no_replica)
        monkeypatch.setattr(type(Env), "CACHE_DIR", property(lambda _: tmp_path / "cache"))
        for publisher, names in (
            (UserPublisher, ("updated", "deactivated", "deleted")),
            (AppSettingPublisher, ("user_updated", "selected_users_deleted")),
        ):
            for name in names:
                monkeypatch.setattr(publisher, name, Mock())
        with DbSession.atomic() as db:
            owner = User(
                firstname="Lock",
                lastname="Owner",
                username="lock-owner",
                email="lock-owner@example.invalid",
                password="fixture-only",
                activated_at=SafeDateTime.now(),
            )
            db.insert(owner)
        yield SimpleNamespace(engine=engine, owner=owner)
    finally:
        engine.dispose()
        with admin.begin() as db:
            db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def change_lifecycle(service, owner, operation):
    if operation == "deactivate":
        return service.user.update(owner, {"activated_at": None}, from_setting=True)
    if operation == "delete":
        return service.user.delete(owner)
    return service.user.delete_selected([owner.get_uid()])


def wait_for_database_lock(engine, pid):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        with engine.connect() as db:
            waiting = db.execute(
                text("SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid"), {"pid": pid}
            ).scalar_one()
        if waiting == "Lock":
            return
        threading.Event().wait(0.02)
    pytest.fail("The second native operation never waited for the PostgreSQL owner row lock")


@pytest.mark.parametrize("operation", ["deactivate", "delete", "bulk_delete"])
@pytest.mark.parametrize("first", ["create", "lifecycle"])
def test_owner_creation_and_lifecycle_serialize(owner_database, operation, first):
    f = owner_database
    held, release, contender_started = threading.Event(), threading.Event(), threading.Event()
    worker = threading.local()
    contender_pid = []

    def before_sql(connection, _cursor, statement, _parameters, _context, _many):
        if getattr(worker, "contender", False) and "FOR UPDATE" in statement:
            contender_pid.append(connection.connection.driver_connection.info.backend_pid)
            contender_started.set()

    event.listen(f.engine, "before_cursor_execute", before_sql)

    def execute(action, contender=False):
        worker.contender = contender
        with closing(DomainService()) as service:
            if contender:
                if action == "create":
                    with pytest.raises(ValueError, match="owner must be an active account"):
                        service.organization.create(f.owner, "Concurrent organization", slug="concurrent")
                else:
                    with pytest.raises(UserOwnershipError):
                        change_lifecycle(service, f.owner, operation)
                return
            with DbSession.atomic():
                if action == "create":
                    service.organization.create(f.owner, "Concurrent organization", slug="concurrent")
                else:
                    change_lifecycle(service, f.owner, operation)
                held.set()
                assert release.wait(6), "The lock holder was not released"

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            holder = pool.submit(execute, first)
            try:
                assert held.wait(4), "The first native operation never acquired its lock"
                # Native publishers must wait for the surrounding transaction to commit.
                assert UserPublisher.deleted.call_count == 0
                assert UserPublisher.deactivated.call_count == 0
                contender = pool.submit(execute, "lifecycle" if first == "create" else "create", True)
                assert contender_started.wait(4), "The contender never reached the native lock statement"
                wait_for_database_lock(f.engine, contender_pid[0])
            finally:
                release.set()
            holder.result(timeout=10)
            contender.result(timeout=10)
    finally:
        release.set()
        event.remove(f.engine, "before_cursor_execute", before_sql)

    with f.engine.connect() as db:
        user = db.execute(select(User.activated_at, User.deleted_at).where(User.id == f.owner.id)).one()
        organizations = db.execute(select(func.count()).select_from(Organization)).scalar_one()
    if first == "create":
        assert organizations == 1
        assert user.activated_at is not None and user.deleted_at is None
        assert UserPublisher.deleted.call_count == UserPublisher.deactivated.call_count == 0
    else:
        assert organizations == 0
        if operation == "deactivate":
            assert user.activated_at is None and user.deleted_at is None
            assert UserPublisher.deactivated.call_count == 1
        else:
            assert user.deleted_at is not None
            assert UserPublisher.deleted.call_count == 1
