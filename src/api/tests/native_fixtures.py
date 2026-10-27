"""Shared native database fixtures, owned by the test tree."""

import importlib.util
import os
from pathlib import Path
from uuid import uuid4
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from langboard_shared.core.caching import Cache
from langboard_shared.core.caching.InMemoryCache import InMemoryCache
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.security import KeyVault
from langboard_shared.core.security.vault.LocalDevVaultProvider import LocalDevVaultProvider
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import (
    AppConnection,
    AppDefinition,
    AppGovernancePolicy,
    AppResourceBinding,
    BoardAppBinding,
    Organization,
    Project,
    ProjectAssignedUser,
    ProjectColumn,
    ProjectRole,
    User,
    WorkflowRule,
    WorkflowStageDefinition,
)
from langboard_shared.domain.services.factory.SecretReferenceService import SecretReferenceService
from langboard_shared.domain.services.factory.WorkflowStageService import WorkflowStageService
from langboard_shared.Env import Env
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool


# Precomputed bcrypt cost-12 hash for "test-only"; native password checks still apply.
TEST_PASSWORD_HASH = "$2b$12$2us.T312V4zQeyePxKUcHefHkiiZZIdFVb2pwe6QNyDT1hsGmchMi"


@pytest.fixture(params=["sqlite://", "postgresql-test"])
def board(monkeypatch, request, tmp_path):
    # Reused fixture IDs must not inherit identity snapshots from another test.
    # Exercise the native cache implementation against this test's own storage.
    monkeypatch.setattr(type(Env), "CACHE_DIR", property(lambda _: tmp_path / "cache"))
    monkeypatch.setattr(Cache, "_cache", InMemoryCache())
    url = request.param
    if url == "postgresql-test":
        url = os.environ.get("LANGBOARD_FILE_TEST_DATABASE_URL")
        if not url:
            pytest.skip("Set LANGBOARD_FILE_TEST_DATABASE_URL to a disposable PostgreSQL database")
    if url in ("sqlite-http", "sqlite://"):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    else:
        engine = create_engine(url)
    schema = None
    if engine.dialect.name == "postgresql":
        schema = "app_authority_test_" + uuid4().hex
        with engine.begin() as db:
            db.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine.dispose()
        engine = create_engine(
            url, connect_args={"options": f"-csearch_path={schema} -cstatement_timeout=10000 -clock_timeout=3000"}
        )
    with engine.begin() as connection:
        # SQLite legacy mode does not start a transaction for DDL.
        if engine.dialect.name == "sqlite":
            connection.exec_driver_sql("BEGIN")
        for model in (
            User,
            Organization,
            AppDefinition,
            AppGovernancePolicy,
            Project,
            ProjectAssignedUser,
            ProjectRole,
            ProjectColumn,
            WorkflowStageDefinition,
            WorkflowRule,
            BoardAppBinding,
            AppConnection,
            AppResourceBinding,
        ):
            model.__table__.create(connection)
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)

    def replica_forbidden():
        raise AssertionError("App preview must not read a stale replica")

    monkeypatch.setattr(DbEngine, "get_readonly_engine", replica_forbidden)
    with Session(engine, expire_on_commit=False) as db:
        user = User(
            id=1,
            firstname="Test",
            lastname="Reader",
            email="reader@example.invalid",
            password=TEST_PASSWORD_HASH,
            activated_at=SafeDateTime.now(),
        )
        owner = User(
            id=2,
            firstname="Test",
            lastname="Owner",
            email="owner@example.invalid",
            password=TEST_PASSWORD_HASH,
            activated_at=SafeDateTime.now(),
        )
        db.add_all([user, owner])
        db.commit()
        project = Project(id=10, owner_id=2, title="Current board")
        db.add_all([project, Project(id=11, owner_id=2, title="Foreign board")])
        db.commit()
        member = ProjectAssignedUser(id=20, project_id=10, user_id=1)
        role = ProjectRole(id=30, project_id=10, user_id=1, actions=["read"])
        columns = [
            ProjectColumn(id=40 + i, project_id=10, name="Localized", workflow_stage=key)
            for i, key in enumerate(("active", "review", "closed"))
        ]
        stages = [
            WorkflowStageDefinition(id=50 + i, key=key, name="Localized", is_builtin=True)
            for i, key in enumerate(("active", "review", "closed"))
        ]
        for row in (member, role, *columns, *stages):
            db.add(row)
        db.commit()
    service = WorkflowStageService(lambda _: None, lambda _: None, None)
    try:
        yield service, user, project, member, role, columns, stages
    finally:
        if schema:
            with engine.begin() as db:
                db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()


@pytest.fixture
def secrets(board, monkeypatch, tmp_path):
    engine = DbEngine.get_main_engine()
    if engine.dialect.name == "sqlite":
        Organization.__table__.create(engine, checkfirst=True)
    # Exercise the actual migration, not just ORM metadata creation.

    for filename in (
        "20261008061000-820ebc13da57.py",
        "20261008065000-932fcd24eb68.py",
        "20261008072000-a430de35fc79.py",
        "20261008114000-e87412793ab3.py",
        "20261009040000-7c98451eab03.py",
        "20261009100000-b51832cfe4a7.py",
        "20261010212000-e15b02634f97.py",
        "20261019030000-f17085d21943.py",
        "20261026010000-cdef901b2345.py",
    ):
        migration_path = Path(__file__).resolve().parents[3] / "src/api/langboard/migrations/versions" / filename
        spec = importlib.util.spec_from_file_location("secret_reference_migration", migration_path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        with engine.begin() as connection:
            monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
            migration.upgrade()
    monkeypatch.setattr(KeyVault, "provider", LocalDevVaultProvider(tmp_path))
    service = SecretReferenceService(
        lambda cls: board[0] if cls is WorkflowStageService else None, lambda _: None, None
    )
    return service, board, tmp_path


@pytest.fixture
def binding(board):
    with DbSession.use(readonly=False) as db:
        row = BoardAppBinding(project_id=10, app_key="github", state="enabled", granted_capabilities=["signals.read"])
        db.insert(row)
        board[4].actions = ["read", "update"]
        db.update(board[4])
    return row
