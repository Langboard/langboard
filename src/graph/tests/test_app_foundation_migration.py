"""App foundation migration and database authority constraints on PostgreSQL."""

import importlib.util
import os
from pathlib import Path
from uuid import uuid4
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import (
    AppConnection,
    AppConnectionCredential,
    AppDefinition,
    AppGovernancePolicy,
    AppMcpConsent,
    AppResourceBinding,
    BoardAppBinding,
    GlobalCardRelationshipType,
    Organization,
    Project,
    User,
)
from langboard_shared.domain.services.DomainService import DomainService
from langboard_shared.domain.services.RelationshipTypeBootstrap import bootstrap_relationship_types
from langboard_shared.domain.services.WorkflowStageBootstrap import bootstrap_workflow_stages
from sqlalchemy import MetaData, create_engine, inspect, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


def test_app_foundation_postgres_upgrade_constraints_and_downgrade(monkeypatch):
    url = os.environ.get("LANGBOARD_FILE_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requires disposable PostgreSQL")
    schema = "app_foundation_" + uuid4().hex
    admin = create_engine(url)
    with admin.begin() as db:
        db.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    migration_root = Path(__file__).parents[2] / "api/langboard/migrations"
    assert ScriptDirectory(str(migration_root)).get_heads() == ["d6ab195f843c"]
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
            db.execute(
                text(
                    "INSERT INTO board_app_binding (id,project_id,app_key,state,workflow_mapping,granted_capabilities,stage_transitions_enabled,archive_sync_types,archive_sync_revision) VALUES (40,10,'example.app','disabled','{}','[]',false,'[]',1)"
                )
            )
            db.add(AppConnection(id=50, owner_id=1, app_key="example.app"))
            db.add(
                AppConnection(id=51, ownership="organization", organization_id=20, owner_id=1, app_key="example.app")
            )
            db.add(AppGovernancePolicy(id=60, scope_key="global"))
            db.add(AppGovernancePolicy(id=61, scope_key="organization:20", organization_id=20, mode=None))
            db.commit()
            assert db.execute(text("SELECT state FROM board_app_binding WHERE id=40")).scalar() == "disabled"
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
                if isinstance(row, BoardAppBinding):
                    db.execute(
                        text(
                            "INSERT INTO board_app_binding (id,project_id,app_key,state,workflow_mapping,granted_capabilities,stage_transitions_enabled,archive_sync_types,archive_sync_revision) VALUES (:id,:project,:key,:state,'{}','[]',false,'[]',1)"
                        ),
                        {"id": int(row.id), "project": int(row.project_id), "key": row.app_key, "state": row.state},
                    )
                else:
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
        spec = importlib.util.spec_from_file_location(
            "app_auth_migration", migration_root / "versions/20261012070000-c8f4a62d190e.py"
        )
        auth_migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(auth_migration)
        assert auth_migration.down_revision == project_migration.revision
        with engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
            auth_migration.upgrade()
        with Session(engine) as db:
            db.add(AppConnection(id=90, app_key="example", owner_id=1, state="connected"))
            db.commit()
            db.add(
                AppConnectionCredential(
                    id=91,
                    connection_id=90,
                    issued_by=1,
                    token_hash="hashed",
                    identity_hash="identity",
                    expires_at=SafeDateTime.now(),
                )
            )
            db.add(AppMcpConsent(id=92, connection_id=90, identity_hash="identity", scope_tools={}))
            db.commit()
        with Session(engine) as db, pytest.raises(IntegrityError):
            db.execute(update(AppMcpConsent).values(revision=0))
            db.commit()
        with Session(engine) as db, pytest.raises(IntegrityError):
            db.add(AppMcpConsent(id=93, connection_id=999, identity_hash="identity", scope_tools={}))
            db.commit()
        with engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
            auth_migration.downgrade()
            assert connection.execute(text("SELECT owner_id FROM app_connection WHERE id=90")).scalar() == 1
            auth_migration.upgrade()
        assert {"app_connection_credential", "app_mcp_consent"} <= set(inspect(engine).get_table_names())
        spec = importlib.util.spec_from_file_location(
            "resource_migration", migration_root / "versions/20261012080000-d9a5b73e201f.py"
        )
        resource_migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(resource_migration)
        assert resource_migration.down_revision == auth_migration.revision
        with engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
            resource_migration.upgrade()
        spec = importlib.util.spec_from_file_location(
            "identity_migration", migration_root / "versions/20261012083000-e1b6c84f312a.py"
        )
        identity_migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(identity_migration)
        assert identity_migration.down_revision == resource_migration.revision
        with engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
            identity_migration.upgrade()
        with Session(engine) as db:
            db.add(BoardAppBinding(id=100, project_id=10, app_key="example"))
            db.commit()
            db.add(
                AppResourceBinding(
                    id=101, board_binding_id=100, connection_id=90, resource_type="issue", external_resource_id="one"
                )
            )
            db.add(
                AppResourceBinding(
                    id=102, board_binding_id=100, connection_id=90, resource_type="issue", external_resource_id="two"
                )
            )
            db.commit()
            assert db.query(AppResourceBinding).count() == 2
        for values in (
            {"external_resource_id": "one"},
            {"external_resource_id": "three", "access_revision": -1},
            {"external_resource_id": "three", "access_state": "invented"},
            {"external_resource_id": "three", "connection_id": 999},
        ):
            with Session(engine) as db, pytest.raises(IntegrityError):
                params = {"id": 103, "board_binding_id": 100, "connection_id": 90, "resource_type": "issue", **values}
                db.add(AppResourceBinding(**params))
                db.commit()
        with engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
            identity_migration.downgrade()
            connection.execute(
                text("UPDATE app_resource_binding SET is_selected=true, access_state='granted' WHERE id=101")
            )
            identity_migration.upgrade()
            assert (
                connection.execute(text("SELECT is_selected FROM app_resource_binding WHERE id=101")).scalar() is False
            )
            assert (
                connection.execute(text("SELECT access_state FROM app_resource_binding WHERE id=101")).scalar()
                == "revoked"
            )
            identity_migration.downgrade()
            resource_migration.downgrade()
            assert connection.execute(text("SELECT owner_id FROM app_connection WHERE id=90")).scalar() == 1
            resource_migration.upgrade()
            identity_migration.upgrade()
        with engine.begin() as connection:
            connection.execute(
                text(
                    'CREATE TABLE project_column (id BIGINT PRIMARY KEY, created_at TIMESTAMPTZ DEFAULT now() NOT NULL, updated_at TIMESTAMPTZ DEFAULT now() NOT NULL, deleted_at TIMESTAMPTZ, project_id BIGINT REFERENCES project(id) NOT NULL, name VARCHAR NOT NULL, "order" INTEGER NOT NULL DEFAULT 0, is_archive BOOLEAN NOT NULL DEFAULT false)'
                )
            )
            connection.execute(
                text("INSERT INTO project_column (id,project_id,name) VALUES (120,10,'Custom existing column')")
            )
        spec = importlib.util.spec_from_file_location(
            "workflow_migration", migration_root / "versions/20261012090000-f2c7d95a423b.py"
        )
        workflow_migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(workflow_migration)
        assert workflow_migration.down_revision == identity_migration.revision
        with engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
            workflow_migration.upgrade()
            assert connection.execute(text("SELECT workflow_stage FROM project_column WHERE id=120")).scalar() is None
            assert connection.execute(text("SELECT count(*) FROM workflow_stage_definition")).scalar() == 0
        monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
        assert bootstrap_workflow_stages()["inserted"] == 6
        assert bootstrap_workflow_stages()["inserted"] == 0
        with engine.begin() as connection:
            connection.execute(text("UPDATE workflow_stage_definition SET name='User custom name' WHERE key='active'"))
        assert bootstrap_workflow_stages()["inserted"] == 0
        with engine.begin() as connection:
            assert (
                connection.execute(text("SELECT name FROM workflow_stage_definition WHERE key='active'")).scalar()
                == "User custom name"
            )
            assert connection.execute(text("SELECT count(*) FROM workflow_stage_definition")).scalar() == 6
            translations = connection.execute(
                text("SELECT translations FROM workflow_stage_definition WHERE key='active'")
            ).scalar()
            assert set(translations) == {"en", "ko", "ja", "zh"}
            connection.execute(text("UPDATE project_column SET workflow_stage='active' WHERE id=120"))
        with engine.begin() as connection, pytest.raises(IntegrityError):
            connection.execute(text("UPDATE project_column SET workflow_stage='provider-specific' WHERE id=120"))
        with engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
            workflow_migration.downgrade()
            assert (
                connection.execute(text("SELECT name FROM project_column WHERE id=120")).scalar()
                == "Custom existing column"
            )
            workflow_migration.upgrade()
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE card (id BIGINT PRIMARY KEY)"))
            connection.execute(text("INSERT INTO card (id) VALUES (130)"))
        spec = importlib.util.spec_from_file_location(
            "ownership_migration", migration_root / "versions/20261012110000-a3d8e62c510f.py"
        )
        ownership_migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ownership_migration)
        assert ownership_migration.down_revision == workflow_migration.revision
        with engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
            ownership_migration.upgrade()
            connection.execute(
                text("INSERT INTO card_app_ownership (id,card_id,app_key,revision) VALUES (140,130,'example',1)")
            )
            connection.execute(
                text(
                    "INSERT INTO card_app_ownership_audit (id,card_id,actor_id,app_key,revision) "
                    "VALUES (141,130,1,'example',1)"
                )
            )
        with engine.begin() as connection, pytest.raises(IntegrityError):
            connection.execute(text("INSERT INTO card_app_ownership (id,card_id,revision) VALUES (142,130,1)"))
        with engine.begin() as connection, pytest.raises(IntegrityError):
            connection.execute(text("UPDATE card_app_ownership SET revision=0 WHERE id=140"))
        with engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
            connection.execute(text("DELETE FROM card WHERE id=130"))
            assert connection.execute(text("SELECT count(*) FROM card_app_ownership")).scalar() == 0
            assert connection.execute(text("SELECT count(*) FROM card_app_ownership_audit")).scalar() == 1
            ownership_migration.downgrade()
            assert (
                connection.execute(text("SELECT name FROM project_column WHERE id=120")).scalar()
                == "Custom existing column"
            )
            ownership_migration.upgrade()
        spec = importlib.util.spec_from_file_location(
            "card_resources_migration", migration_root / "versions/20261012120000-b4e9f73d621a.py"
        )
        card_resources_migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(card_resources_migration)
        assert card_resources_migration.down_revision == ownership_migration.revision
        with engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
            card_resources_migration.upgrade()
            connection.execute(text("INSERT INTO card (id) VALUES (150)"))
            connection.execute(
                text(
                    "INSERT INTO card_app_resource_selection "
                    "(id,card_id,app_key,connection_id,resource_uids,identity_hash,ownership_revision,revision) "
                    "VALUES (151,150,'example',90,'[]','',1,1)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO card_app_resource_selection_audit "
                    "(id,card_id,actor_id,app_key,connection_id,resource_uids,identity_hash,ownership_revision,revision) "
                    "VALUES (152,150,1,'example',90,'[]','',1,1)"
                )
            )
        with engine.begin() as connection, pytest.raises(IntegrityError):
            connection.execute(
                text(
                    "INSERT INTO card_app_resource_selection "
                    "(id,card_id,app_key,connection_id,resource_uids,identity_hash,ownership_revision,revision) "
                    "VALUES (153,150,'example',90,'[]','',1,1)"
                )
            )
        with engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
            connection.execute(text("DELETE FROM card WHERE id=150"))
            assert connection.execute(text("SELECT count(*) FROM card_app_resource_selection")).scalar() == 0
            assert connection.execute(text("SELECT count(*) FROM card_app_resource_selection_audit")).scalar() == 1
            card_resources_migration.downgrade()
            card_resources_migration.upgrade()
        spec = importlib.util.spec_from_file_location(
            "execution_intent_migration", migration_root / "versions/20261012130000-c5fa084e732b.py"
        )
        execution_intent_migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(execution_intent_migration)
        assert execution_intent_migration.down_revision == card_resources_migration.revision
        with engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
            execution_intent_migration.upgrade()
            connection.execute(text("INSERT INTO card (id) VALUES (160)"))
            connection.execute(
                text("INSERT INTO card_execution_generation (id,card_id,execution_generation) VALUES (161,160,1)")
            )
            connection.execute(
                text(
                    "INSERT INTO card_execution_intent "
                    "(id,card_id,actor_id,idempotency_key,execution_generation,app_key,connection_id,snapshot) "
                    "VALUES (162,160,1,'request-0000000001',1,'example',90,'{}')"
                )
            )
        with engine.begin() as connection, pytest.raises(IntegrityError):
            connection.execute(text("UPDATE card_execution_generation SET execution_generation=-1 WHERE card_id=160"))
        with engine.begin() as connection, pytest.raises(IntegrityError):
            connection.execute(
                text(
                    "INSERT INTO card_execution_intent "
                    "(id,card_id,actor_id,idempotency_key,execution_generation,app_key,connection_id,snapshot) "
                    "VALUES (163,160,1,'request-0000000001',2,'example',90,'{}')"
                )
            )
        with engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
            connection.execute(text("DELETE FROM card WHERE id=160"))
            assert connection.execute(text("SELECT count(*) FROM card_execution_generation")).scalar() == 0
            assert connection.execute(text("SELECT count(*) FROM card_execution_intent")).scalar() == 1
            execution_intent_migration.downgrade()
            execution_intent_migration.upgrade()
    finally:
        engine.dispose()
        with admin.begin() as db:
            db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def test_relationship_semantics_migration_preserves_legacy_and_default_identity(monkeypatch):
    url = os.environ.get("LANGBOARD_FILE_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requires disposable PostgreSQL")
    schema = "relationship_semantics_" + uuid4().hex
    admin = create_engine(url)
    with admin.begin() as db:
        db.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    root = Path(__file__).parents[2] / "api/langboard/migrations/versions"
    spec = importlib.util.spec_from_file_location("relationship_migration", root / "20261012140000-d6ab195f843c.py")
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.down_revision == "c5fa084e732b"
    try:
        table = GlobalCardRelationshipType.__table__.to_metadata(MetaData())
        for name in ("machine_semantic", "is_system_default", "is_active", "affects_readiness"):
            table._columns.remove(table.c[name])
        table.create(engine)
        with engine.begin() as db:
            db.execute(
                text(
                    "INSERT INTO global_card_relationship_type (id,parent_name,child_name,description) "
                    "VALUES (1,'Blocks','Depends on','Legacy relationship')"
                )
            )
        with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
            migration.upgrade()
        assert bootstrap_relationship_types() == {"inserted": 3}
        assert bootstrap_relationship_types() == {"inserted": 0}
        with Session(engine) as db:
            legacy = db.get(GlobalCardRelationshipType, 1)
            assert legacy.machine_semantic is None and not legacy.affects_readiness
            assert not legacy.is_system_default and legacy.is_active
            defaults = db.query(GlobalCardRelationshipType).filter_by(is_system_default=True).all()
            assert {r.machine_semantic: r.affects_readiness for r in defaults} == {
                "contains": False,
                "blocks": True,
                "references": False,
            }
            original_ids = {r.machine_semantic: r.id for r in defaults}
            blocker = next(r for r in defaults if r.machine_semantic == "blocks")
            db.execute(
                update(GlobalCardRelationshipType)
                .where(GlobalCardRelationshipType.id == blocker.id)
                .values(parent_name="Custom prerequisite label", is_active=False)
            )
            db.commit()
        assert bootstrap_relationship_types() == {"inserted": 0}
        with Session(engine) as db:
            defaults = db.query(GlobalCardRelationshipType).filter_by(is_system_default=True).all()
            assert {r.machine_semantic: r.id for r in defaults} == original_ids
            blocker = db.get(GlobalCardRelationshipType, original_ids["blocks"])
            assert blocker.parent_name == "Custom prerequisite label" and not blocker.is_active
            assert blocker.api_response()["machine_semantic"] == "blocks"
            assert blocker.api_response()["affects_readiness"] is True
        service = DomainService()
        assert service.app_setting.delete_global_relationship(original_ids["blocks"]) is False
        assert service.app_setting.delete_selected_global_relationships([1, original_ids["contains"]]) is False
        with Session(engine) as db:
            assert db.get(GlobalCardRelationshipType, 1) is not None
            assert db.get(GlobalCardRelationshipType, original_ids["contains"]) is not None
        with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
            migration.downgrade()
            assert db.execute(text("SELECT count(*) FROM global_card_relationship_type")).scalar() == 4
            assert (
                db.execute(text("SELECT description FROM global_card_relationship_type WHERE id=1")).scalar()
                == "Legacy relationship"
            )
            migration.upgrade()
    finally:
        engine.dispose()
        with admin.begin() as db:
            db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
