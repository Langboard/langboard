"""App foundation migration and database authority constraints on PostgreSQL."""

import importlib.util
import os
from pathlib import Path
from uuid import uuid4
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import (
    AppConnection,
    AppDefinition,
    AppGovernancePolicy,
    BoardAppBinding,
    Organization,
    Project,
    User,
)
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


def test_app_foundation_postgres_upgrade_constraints_and_downgrade():
    url = os.environ.get("LANGBOARD_FILE_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requires disposable PostgreSQL")
    schema = "app_foundation_" + uuid4().hex
    admin = create_engine(url)
    with admin.begin() as db:
        db.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    migration_root = Path(__file__).parents[2] / "api/langboard/migrations"
    assert ScriptDirectory(str(migration_root)).get_heads() == ["a7c9e31b6204"]
    spec = importlib.util.spec_from_file_location(
        "app_foundation_migration", migration_root / "versions/20261012060000-a7c9e31b6204.py"
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.down_revision == "0cc6abc4b020"
    try:
        User.__table__.create(engine)
        Project.__table__.create(engine)
        with Session(engine) as db:
            db.add(
                User(
                    id=1,
                    firstname="Fixture",
                    lastname="Owner",
                    email="owner@example.invalid",
                    password="test-only",
                    activated_at=SafeDateTime.now(),
                )
            )
            db.commit()
            db.add(Project(id=10, owner_id=1, title="Board"))
            db.commit()
        with engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        tables = {
            "organization",
            "app_definition",
            "app_connection",
            "board_app_binding",
            "app_governance_policy",
            "app_composition",
            "app_composition_member",
        }
        assert tables <= set(inspect(engine).get_table_names())
        for table in tables:
            indexes = inspect(engine).get_indexes(table)
            columns = [tuple(index["column_names"]) for index in indexes]
            assert len(columns) == len(set(columns)), f"Duplicate indexes on {table}"
        with Session(engine) as db:
            db.add(Organization(id=20, name="Example", slug="example", owner_user_id=1))
            db.add(Organization(id=21, name="Other", slug="other", owner_user_id=1))
            db.commit()
            definition = AppDefinition(
                id=30,
                key="example.app",
                declaration={"name": "Example"},
                approved_by=1,
                package={"storage": "private-path", "version": "1.0"},
            )
            db.add(definition)
            db.add(BoardAppBinding(id=40, project_id=10, app_key="example.app"))
            db.add(AppConnection(id=50, owner_id=1, app_key="example.app"))
            db.add(
                AppConnection(id=51, ownership="organization", organization_id=20, owner_id=1, app_key="example.app")
            )
            db.add(AppGovernancePolicy(id=60, scope_key="global"))
            db.add(AppGovernancePolicy(id=61, scope_key="organization:20", organization_id=20, mode=None))
            db.commit()
            assert db.get(BoardAppBinding, 40).state == "disabled"
            assert db.get(AppConnection, 50).state == "pending"
            assert "storage" not in definition.registry_response()["installation"]
            db.delete(db.get(AppGovernancePolicy, 60))
            db.commit()
        invalid = [
            BoardAppBinding(id=41, project_id=10, app_key="example.app"),
            BoardAppBinding(id=42, project_id=10, app_key="other.app", state="invented"),
            AppConnection(id=52, owner_id=1, app_key="example.app", ownership="organization"),
            AppConnection(id=53, owner_id=1, app_key="example.app", organization_id=20),
            AppConnection(id=54, owner_id=1, app_key="example.app", state="invented"),
            AppGovernancePolicy(id=62, scope_key="organization:999", organization_id=21),
            AppGovernancePolicy(id=63, scope_key="global", mode=None),
            AppGovernancePolicy(id=64, scope_key="global", mode="invented"),
        ]
        for row in invalid:
            with Session(engine) as db, pytest.raises(IntegrityError):
                db.add(row)
                db.commit()
        with engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
        assert not tables.intersection(inspect(engine).get_table_names())
        with Session(engine) as db:
            assert db.get(User, 1) is not None
            assert db.get(Project, 10) is not None
        with engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        assert tables <= set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
        with admin.begin() as db:
            db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
