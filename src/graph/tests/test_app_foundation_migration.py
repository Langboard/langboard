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
from sqlalchemy import MetaData, create_engine, inspect, text, update
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
    assert ScriptDirectory(str(migration_root)).get_heads() == ["b6d3e71f9042"]
    spec = importlib.util.spec_from_file_location(
        "app_foundation_migration", migration_root / "versions/20261012060000-a7c9e31b6204.py"
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.down_revision == "0cc6abc4b020"
    try:
        User.__table__.create(engine)
        project_table = Project.__table__.to_metadata(MetaData())
        for constraint in list(project_table.constraints):
            if "organization_id" in constraint.columns:
                project_table.constraints.remove(constraint)
        for index in list(project_table.indexes):
            if "organization_id" in index.columns:
                project_table.indexes.remove(index)
        project_table._columns.remove(project_table.c.organization_id)
        User.__table__.to_metadata(project_table.metadata)
        project_table.create(engine)
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
            db.execute(
                text(
                    "INSERT INTO project (id, owner_id, title, project_type, archive_visible_days) VALUES (10, 1, 'Board', 'Other', 3)"
                )
            )
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
            assert db.execute(text("SELECT id FROM project WHERE id=10")).scalar() == 10
        with engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        assert tables <= set(inspect(engine).get_table_names())
        spec = importlib.util.spec_from_file_location(
            "project_org_migration", migration_root / "versions/20261012063000-b6d3e71f9042.py"
        )
        project_migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(project_migration)
        assert project_migration.down_revision == migration.revision
        with engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
            project_migration.upgrade()
            assert connection.execute(text("SELECT organization_id FROM project WHERE id=10")).scalar() is None
        with Session(engine) as db:
            db.add(Organization(id=20, name="Example", slug="example", owner_user_id=1))
            db.commit()
            db.execute(update(Project).where(Project.id == 10).values(organization_id=20))
            db.commit()
            assert db.get(Project, 10).organization_id == 20
        with Session(engine) as db, pytest.raises(IntegrityError):
            db.execute(text("DELETE FROM organization WHERE id=20"))
            db.commit()
        with engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
            project_migration.downgrade()
            assert connection.execute(text("SELECT title FROM project WHERE id=10")).scalar() == "Board"
            project_migration.upgrade()
            assert connection.execute(text("SELECT organization_id FROM project WHERE id=10")).scalar() is None
    finally:
        engine.dispose()
        with admin.begin() as db:
            db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
