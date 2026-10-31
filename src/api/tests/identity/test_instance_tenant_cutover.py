"""Real SQLite/PostgreSQL reviewed maintenance and database arbitration proof."""

import importlib.util
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from uuid import uuid4
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from langboard.commands.InstanceTenantCutoverCommand import REVISION, apply, maintenance, plan, rollback, snapshot, uid
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.storage import FileModel
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain import models
from langboard_shared.domain.services import DomainService
from sqlalchemy import Column, MetaData, String, Table, create_engine, insert, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


@pytest.fixture(params=["sqlite", "postgresql"])
def database(request, tmp_path, monkeypatch):
    admin = None
    if request.param == "postgresql":
        url = os.getenv("LANGBOARD_FILE_TEST_DATABASE_URL")
        if not url:
            pytest.skip("Requires explicitly disposable PostgreSQL")
        admin = create_engine(url)
        schema = "tenant_cutover_" + uuid4().hex
        with admin.begin() as db:
            db.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_engine(url, connect_args={"options": f"-csearch_path={schema} -cstatement_timeout=15000"})
    else:
        engine = create_engine(f"sqlite:///{tmp_path / 'tenant.sqlite'}", connect_args={"timeout": 10})
    # All registered business tables, real foreign keys and unrelated data participate.
    models.Organization.metadata.create_all(engine)
    revision = Table("alembic_version", MetaData(), Column("version_num", String(32), primary_key=True))
    revision.create(engine)
    with engine.begin() as db:
        db.execute(insert(revision).values(version_num=REVISION))
    with Session(engine) as db:
        db.add_all(
            [
                models.User(
                    id=1,
                    firstname="Active",
                    lastname="Owner",
                    email="owner@example.invalid",
                    password="fixture",
                    activated_at=SafeDateTime.now(),
                ),
                models.User(
                    id=2,
                    firstname="Inactive",
                    lastname="Owner",
                    email="inactive@example.invalid",
                    password="fixture",
                    activated_at=None,
                ),
            ]
        )
        db.commit()
        db.add_all(
            [
                models.Project(id=30, title="Unbound board", owner_id=1),
                models.Project(id=31, title="Inactive owner board", owner_id=2),
                models.UserGroup(id=70, user_id=1, name="Address book"),
                models.ScimGroup(id=40, display_name="SCIM fixture", source="scim", issuer="https://idp.invalid"),
            ]
        )
        db.commit()
        db.add_all(
            [
                models.UserGroupAssignedEmail(id=80, group_id=70, email="contact@example.invalid"),
                models.ScimGroupMember(id=50, group_id=40, user_id=1),
                models.ProjectRole(id=81, project_id=30, user_id=1, actions=["read", "update"]),
                models.ProjectInvitation(id=82, project_id=30, email="invite@example.invalid", token="fixture-token"),
                models.ProjectAttachment(
                    id=83,
                    project_id=30,
                    user_id=1,
                    filename="preserved.pdf",
                    content_sha256="a" * 64,
                    file=FileModel(
                        storage_type="local",
                        storage_name="fixture",
                        original_filename="preserved.pdf",
                        filename="immutable.pdf",
                        path="/fixture/immutable.pdf",
                    ),
                ),
            ]
        )
        db.commit()
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    try:
        yield engine
    finally:
        engine.dispose()
        if admin is not None:
            with admin.begin() as db:
                db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            admin.dispose()


def business_rows(db):
    metadata = MetaData()
    metadata.reflect(db)
    result = {}
    for name, table in metadata.tables.items():
        if name == "organization":
            continue
        rows = [dict(row) for row in db.execute(select(table)).mappings()]
        if name == "project":
            for row in rows:
                row.pop("organization_id")
        result[name] = sorted(rows, key=str)
    return result


def request_for(db):
    before = snapshot(db)
    return {
        "snapshot_sha256": before["sha256"],
        "revision": before["revision"],
        "tenant": {"uid": uid(10), "owner_uid": uid(1), "name": "Reviewed tenant", "slug": "reviewed"},
        "projects": {project["uid"]: uid(10) for project in before["projects"]},
        "preserve_inactive_project_owners": [uid(2)],
    }


def test_obsolete_installed_revision_refuses_current_cutover(database):
    # A subsequent directory migration must not leave maintenance pinned to f025.
    with database.begin() as db:
        db.execute(text("UPDATE alembic_version SET version_num = 'f0254a74d298'"))
    with maintenance(database) as db:
        before = business_rows(db)
        with pytest.raises(ValueError, match="current migration head"):
            snapshot(db)
        assert business_rows(db) == before


@pytest.mark.parametrize("bootstrap", [True, False])
def test_exact_no_loss_apply_rollback(database, bootstrap):
    if not bootstrap:
        with Session(database) as db:
            db.add(models.Organization(id=10, name="Reviewed tenant", slug="reviewed", owner_user_id=1))
            db.commit()
            db.execute(update(models.Project).where(models.Project.id == 30).values(organization_id=10))
            db.commit()
    with maintenance(database) as db:
        before = snapshot(db)
        preserved = business_rows(db)
        document = plan(db, request_for(db))
    with maintenance(database) as db:
        receipt = apply(db, document, document["sha256"])
        assert business_rows(db) == preserved
        assert {row[0] for row in db.execute(select(models.Project.organization_id))} == {10}
        assert list(db.execute(select(models.Project.owner_id).order_by(models.Project.id)).scalars()) == [1, 2]
    with maintenance(database) as db:
        rollback(db, receipt, receipt["sha256"])
        assert snapshot(db) == before


@pytest.mark.parametrize("bad", ["partial", "owner", "inactive_review", "tenant_identity", "revision", "orphan"])
def test_invalid_review_has_no_writes(database, bad):
    with maintenance(database) as db:
        if bad == "orphan":
            # Native PostgreSQL FK blocks this fixture before maintenance.
            if db.dialect.name == "postgresql":
                pytest.skip("PostgreSQL foreign keys reject orphan owner")
            db.execute(update(models.Project).where(models.Project.id == 30).values(owner_id=999))
        before = snapshot(db)
        request = request_for(db)
        if bad == "partial":
            request["projects"].pop(uid(30))
        elif bad == "owner":
            request["tenant"]["owner_uid"] = uid(2)
        elif bad == "inactive_review":
            request["preserve_inactive_project_owners"] = []
        elif bad == "tenant_identity":
            request["tenant"]["uid"] = "bad"
        elif bad == "revision":
            request["revision"] = "old"
        with pytest.raises(ValueError):
            plan(db, request)
        assert snapshot(db) == before


def test_stale_plan_and_subsequent_business_change_refuse(database):
    with maintenance(database) as db:
        document = plan(db, request_for(db))
    with database.begin() as db:
        db.execute(update(models.Project).where(models.Project.id == 30).values(title="Changed"))
    with maintenance(database) as db:
        before = snapshot(db)
        with pytest.raises(ValueError, match="Stale"):
            apply(db, document, document["sha256"])
        assert snapshot(db) == before
        document = plan(db, request_for(db))
        receipt = apply(db, document, document["sha256"])
    with Session(database) as db:
        db.add(models.Project(id=90, organization_id=10, owner_id=1, title="Subsequent business reference"))
        db.commit()
    with maintenance(database) as db:
        before = snapshot(db)
        with pytest.raises(ValueError, match="Post-cutover"):
            rollback(db, receipt, receipt["sha256"])
        assert snapshot(db) == before


def test_legacy_multiple_including_inactive_refused(database):
    with Session(database) as db:
        db.add_all(
            [
                models.Organization(id=10, name="One", slug="one", owner_user_id=1),
                models.Organization(id=11, name="Two", slug="two", owner_user_id=1, is_active=False),
            ]
        )
        db.commit()
    with maintenance(database) as db:
        before = snapshot(db)
        with pytest.raises(ValueError, match="Multiple organizations"):
            plan(db, request_for(db))
        assert snapshot(db) == before
    with closing(DomainService()) as service:
        with pytest.raises(ValueError, match="tenant already exists"):
            service.organization.create(
                models.User(
                    id=1, firstname="Active", lastname="Owner", email="owner@example.invalid", password="fixture"
                ),
                "Third",
            )
        with pytest.raises(ValueError, match="multiple organizations"):
            service.organization.set_active(
                models.User(
                    id=1, firstname="Active", lastname="Owner", email="owner@example.invalid", password="fixture"
                ),
                11,
                True,
            )


def test_real_concurrent_singleton_creation(database):
    barrier = threading.Barrier(2)

    def create(owner):
        barrier.wait(timeout=5)
        try:
            with database.begin() as db:
                db.execute(
                    models.Organization.__table__.insert().values(
                        id=10 + owner,
                        name="Concurrent",
                        slug=f"concurrent-{owner}",
                        owner_user_id=owner,
                        is_active=True,
                        created_at=SafeDateTime.now(),
                        updated_at=SafeDateTime.now(),
                    )
                )
            return "inserted"
        except IntegrityError:
            return "singleton_rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(create, [1, 2]))
    assert sorted(results) == ["inserted", "singleton_rejected"]
    with database.connect() as db:
        assert len(db.execute(select(models.Organization)).all()) == 1


def test_migration_empty_install_and_legacy_refusal(database):
    path = Path(__file__).parents[2] / "langboard/migrations/versions/20261024020000-f0254a74d298.py"
    spec = importlib.util.spec_from_file_location("singleton_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with maintenance(database) as db:
        with Operations.context(MigrationContext.configure(db)):
            migration.downgrade()
            migration.upgrade()
            assert db.execute(select(models.Organization)).first() is None
            migration.downgrade()
        # Simulate pre-index legacy multi-organization state.
        for index in (10, 11):
            db.execute(
                models.Organization.__table__.insert().values(
                    id=index,
                    name="Legacy",
                    slug=f"legacy-{index}",
                    owner_user_id=1,
                    is_active=True,
                    created_at=SafeDateTime.now(),
                    updated_at=SafeDateTime.now(),
                )
            )
        before = snapshot(db)
        with Operations.context(MigrationContext.configure(db)):
            with pytest.raises(RuntimeError, match="Multiple organizations"):
                migration.upgrade()
        assert snapshot(db) == before


def test_ordinary_creation_and_reactivation(database):
    with closing(DomainService()) as service:
        organization = service.organization.create(
            models.User(id=1, firstname="Active", lastname="Owner", email="owner@example.invalid", password="fixture"),
            "Explicit first tenant",
        )
        service.organization.set_active(
            models.User(id=1, firstname="Active", lastname="Owner", email="owner@example.invalid", password="fixture"),
            organization,
            False,
        )
        with pytest.raises(ValueError, match="tenant already exists"):
            service.organization.create(
                models.User(
                    id=1, firstname="Active", lastname="Owner", email="owner@example.invalid", password="fixture"
                ),
                "Replacement tenant",
            )
        assert (
            service.organization.set_active(
                models.User(
                    id=1, firstname="Active", lastname="Owner", email="owner@example.invalid", password="fixture"
                ),
                organization,
                True,
            ).id
            == organization.id
        )


def test_document_review_digest_and_atomic_failure(database):
    with maintenance(database) as db:
        before = snapshot(db)
        document = plan(db, request_for(db))
        with pytest.raises(ValueError, match="sha256 review"):
            apply(db, document, "wrong")
        assert snapshot(db) == before
    # Failure after mutations must roll back the entire maintenance transaction.
    with pytest.raises(RuntimeError, match="injected failure"):
        with maintenance(database) as db:
            apply(db, document, document["sha256"])
            raise RuntimeError("injected failure")
    with maintenance(database) as db:
        assert snapshot(db) == before


def test_cli_reviewed_roundtrip(database, tmp_path):
    import json
    from langboard.commands.InstanceTenantCutoverCommand import main

    snapshot_path = tmp_path / "snapshot.json"
    request_path = tmp_path / "request.json"
    plan_path = tmp_path / "plan.json"
    receipt_path = tmp_path / "receipt.json"
    rollback_path = tmp_path / "rollback.json"
    assert main(["snapshot", "--output", str(snapshot_path)]) == 0
    with maintenance(database) as db:
        request_path.write_text(json.dumps(request_for(db)))
    assert main(["plan", "--input", str(request_path), "--output", str(plan_path)]) == 0
    reviewed_plan = json.loads(plan_path.read_text())
    assert (
        main(
            [
                "apply",
                "--input",
                str(plan_path),
                "--reviewed-sha256",
                reviewed_plan["sha256"],
                "--output",
                str(receipt_path),
            ]
        )
        == 0
    )
    receipt = json.loads(receipt_path.read_text())
    assert (
        main(
            [
                "rollback",
                "--input",
                str(receipt_path),
                "--reviewed-sha256",
                receipt["sha256"],
                "--output",
                str(rollback_path),
            ]
        )
        == 0
    )
    assert json.loads(rollback_path.read_text())["restored_snapshot"] == json.loads(snapshot_path.read_text())
