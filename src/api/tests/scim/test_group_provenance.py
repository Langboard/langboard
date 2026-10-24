"""Real database isolation and reviewed, data-preserving provenance migration."""

import importlib.util
import os
from pathlib import Path
from uuid import uuid4
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.exceptions import ScimProvisioningException
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import (
    EmployeeMembershipPolicy,
    IdentityProvider,
    ScimGroup,
    ScimGroupMember,
    User,
    UserIdentityLink,
)
from langboard_shared.domain.services import DomainService
from langboard_shared.Env import Env
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError


ISSUER = "https://directory.example"


@pytest.fixture(params=["sqlite", "postgres"])
def database(request, tmp_path, monkeypatch):
    if request.param == "sqlite":
        engine = create_engine(f"sqlite:///{tmp_path / 'provenance.db'}")
        admin = None
    else:
        url = os.getenv("LANGBOARD_FILE_TEST_DATABASE_URL") or os.getenv("LANGBOARD_TEST_DATABASE_URL")
        if url and url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+psycopg://", 1)
        if not url:
            pytest.skip("requires disposable LANGBOARD_FILE_TEST_DATABASE_URL or LANGBOARD_TEST_DATABASE_URL")
        admin = create_engine(url)
        schema = "group_provenance_" + uuid4().hex
        with admin.begin() as db:
            db.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    monkeypatch.setattr(type(Env), "SCIM_ISSUER", property(lambda _: ISSUER + "/"))
    monkeypatch.setattr(type(Env), "MCP_EMPLOYEE_GROUP_IDS", property(lambda _: ["staff"]))
    yield engine
    engine.dispose()
    if admin:
        with admin.begin() as db:
            db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def create_tables(engine):
    for model in (User, UserIdentityLink, ScimGroup, ScimGroupMember, EmployeeMembershipPolicy):
        model.__table__.create(engine)


def test_scim_reads_mutations_settings_and_deactivation_isolate_authority(database, monkeypatch):
    create_tables(database)
    service = DomainService().scim_provisioning
    with DbSession.atomic() as db:
        user = User(
            firstname="Staff",
            lastname="Test",
            email="staff@example.invalid",
            password="fixture",
            activated_at=SafeDateTime.now(),
        )
        db.insert(user)
        db.insert(
            UserIdentityLink(user_id=user.id, provider=IdentityProvider.Scim, external_id="employee", issuer=ISSUER)
        )
        groups = [
            ScimGroup(display_name="Staff", external_id="staff", source="scim", issuer=ISSUER),
            ScimGroup(display_name="Staff", external_id="staff", source="scim", issuer="https://other.example"),
            ScimGroup(display_name="Staff", external_id="staff"),
            ScimGroup(display_name="Staff", source="local"),
        ]
        for group in groups:
            db.insert(group)
            db.insert(ScimGroupMember(group_id=group.id, user_id=user.id))
    assert service.list_groups(1, 100, None)["totalResults"] == 1
    assert len(service.get_api_group_list()) == 1
    for selector in ('displayName eq "Staff"', 'externalId eq "staff"'):
        assert service.list_groups(1, 100, selector)["Resources"][0]["id"] == groups[0].get_uid()
    assert service.resolve_group("staff").id == groups[0].id
    assert service.resolve_group(groups[0].get_uid()).id == groups[0].id
    for group in groups[1:]:
        assert service.resolve_group(group.get_uid()) is None
        for action in (
            lambda: service.apply_group_mutations(group, {"displayName": "Changed"}),
            lambda: service.apply_group_patch(group, []),
            lambda: service.delete_group(group),
            lambda: service.build_scim_group(group),
        ):
            with pytest.raises(ScimProvisioningException.InvalidRequest):
                action()
        with pytest.raises(ValueError):
            service.save_employee_membership_settings([group.get_uid()])
    assert [g["uid"] for g in service.get_employee_membership_settings()["groups"]] == [groups[0].get_uid()]
    assert service.is_employee(user) is True
    assert service.list_employees()["items"][0]["uid"] == user.get_uid()
    service.repo.scim_group_member.delete_all_by_group(groups[0])
    assert service.is_employee(user) is False
    assert service.list_employees()["items"] == []
    assert service.classify_members([user.id])[user.get_uid()] == "external"
    # A persisted selection of local or unresolved group UIDs must also fail closed.
    with DbSession.atomic() as db:
        db.insert(EmployeeMembershipPolicy(issuer=ISSUER, group_uids=[g.get_uid() for g in groups[1:]]))
    assert service.is_employee(user) is False
    assert service.list_employees()["items"] == []
    assert service.classify_members([user.id])[user.get_uid()] == "external"
    from langboard_shared.domain.services.CardAudience import _internal_member

    monkeypatch.setattr(type(Env), "CARD_INTERNAL_ACCESS_MODE", property(lambda _: "scim"))
    with DbSession.use(readonly=False) as db:
        assert _internal_member(db, user) is False
    # Deactivation must only remove this authority's memberships.
    monkeypatch.setattr(service, "_bound_projects_for_user", lambda _: [])
    user.activated_at = None
    service.deactivate_user(user)
    with database.connect() as db:
        assert set(db.execute(select(ScimGroupMember.group_id)).scalars()) == {g.id for g in groups[1:]}


@pytest.mark.parametrize(
    "source,issuer,external_id",
    [
        ("scim", "", "staff"),
        ("scim", ISSUER + "/", "staff"),
        ("scim", " " + ISSUER, "staff"),
        ("scim", "\t" + ISSUER, "staff"),
        ("local", ISSUER, None),
        ("local", "", "staff"),
        ("legacy-unresolved", ISSUER, "staff"),
        ("unknown", "", None),
    ],
)
def test_invalid_provenance_is_rejected_by_database(database, source, issuer, external_id):
    ScimGroup.__table__.create(database)
    with pytest.raises(IntegrityError):
        with database.begin() as db:
            db.execute(
                ScimGroup.__table__.insert().values(
                    id=1, display_name="Invalid", source=source, issuer=issuer, external_id=external_id
                )
            )


def load_migration():
    path = Path(__file__).parents[2] / "langboard/migrations/versions/20261024010000-e9143f63c187.py"
    spec = importlib.util.spec_from_file_location("group_provenance_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("legacy_unique", ["constraint", "index", "both"])
def test_reviewed_migration_roundtrip_preserves_group_and_member_identity(database, legacy_unique):
    migration = load_migration()
    with database.begin() as db:
        unique = ", CONSTRAINT uq_scim_group_external_id UNIQUE (external_id)" if legacy_unique != "index" else ""
        db.execute(
            text(
                "CREATE TABLE scim_group (id BIGINT PRIMARY KEY, external_id VARCHAR, display_name VARCHAR NOT NULL"
                + unique
                + ")"
            )
        )
        if legacy_unique != "constraint":
            db.execute(text("CREATE UNIQUE INDEX ix_scim_group_external_id ON scim_group (external_id)"))
        db.execute(
            text(
                "CREATE TABLE scim_group_member (id BIGINT PRIMARY KEY, group_id BIGINT REFERENCES scim_group(id), user_id BIGINT)"
            )
        )
        db.execute(text("INSERT INTO scim_group VALUES (1, 'staff', 'Staff'), (2, NULL, 'Unresolved')"))
        db.execute(text("INSERT INTO scim_group_member VALUES (3, 1, 4), (5, 2, 4)"))
        with Operations.context(MigrationContext.configure(db)):
            with pytest.raises(RuntimeError, match="complete reviewed mapping"):
                migration.upgrade()
        assert "source" not in {col["name"] for col in __import__("sqlalchemy").inspect(db).get_columns("scim_group")}
        db.info["reviewed_group_provenance"] = {
            "1": {"source": "scim", "issuer": ISSUER},
            "2": {"source": "legacy-unresolved", "issuer": ""},
        }
        with Operations.context(MigrationContext.configure(db)):
            migration.upgrade()
        assert db.execute(text("SELECT id, source, issuer FROM scim_group ORDER BY id")).all() == [
            (1, "scim", ISSUER),
            (2, "legacy-unresolved", ""),
        ]
        assert db.execute(text("SELECT * FROM scim_group_member ORDER BY id")).all() == [(3, 1, 4), (5, 2, 4)]
        with Operations.context(MigrationContext.configure(db)):
            with pytest.raises(RuntimeError, match="archive provenance"):
                migration.downgrade()
            db.info["reviewed_group_provenance_downgrade"] = True
            migration.downgrade()
        assert db.execute(text("SELECT * FROM scim_group ORDER BY id")).all() == [
            (1, "staff", "Staff"),
            (2, None, "Unresolved"),
        ]
        assert db.execute(text("SELECT * FROM scim_group_member ORDER BY id")).all() == [(3, 1, 4), (5, 2, 4)]


def test_empty_migration_and_collision_downgrade_preflight(database):
    migration = load_migration()
    with database.begin() as db:
        db.execute(
            text(
                "CREATE TABLE scim_group (id BIGINT PRIMARY KEY, external_id VARCHAR, display_name VARCHAR NOT NULL, CONSTRAINT uq_scim_group_external_id UNIQUE (external_id))"
            )
        )
        with Operations.context(MigrationContext.configure(db)):
            migration.upgrade()
        db.execute(
            text(
                "INSERT INTO scim_group (id, external_id, display_name, source, issuer) VALUES (1, 'same', 'One', 'scim', 'https://one.example'), (2, 'same', 'Two', 'scim', 'https://two.example')"
            )
        )
        db.info["reviewed_group_provenance_downgrade"] = True
        with Operations.context(MigrationContext.configure(db)):
            with pytest.raises(RuntimeError, match="collisions require review"):
                migration.downgrade()
        assert db.execute(text("SELECT count(*) FROM scim_group")).scalar() == 2
        db.execute(text("DELETE FROM scim_group"))
        with Operations.context(MigrationContext.configure(db)):
            migration.downgrade()


def test_sqlite_foreign_key_batch_preflight_preserves_memberships(tmp_path):
    migration = load_migration()
    engine = create_engine(f"sqlite:///{tmp_path / 'foreign_keys.db'}")
    with engine.begin() as db:
        db.exec_driver_sql("PRAGMA foreign_keys=ON")
        db.execute(
            text("CREATE TABLE scim_group (id BIGINT PRIMARY KEY, external_id VARCHAR, display_name VARCHAR NOT NULL)")
        )
        db.execute(
            text(
                "CREATE TABLE scim_group_member (id BIGINT PRIMARY KEY, group_id BIGINT REFERENCES scim_group(id) ON DELETE CASCADE)"
            )
        )
        db.execute(text("INSERT INTO scim_group VALUES (1, 'staff', 'Staff')"))
        db.execute(text("INSERT INTO scim_group_member VALUES (2, 1)"))
        db.info["reviewed_group_provenance"] = {"1": {"source": "scim", "issuer": ISSUER}}
        with Operations.context(MigrationContext.configure(db)):
            with pytest.raises(RuntimeError, match="foreign keys disabled"):
                migration.upgrade()
        assert db.execute(text("SELECT * FROM scim_group_member")).all() == [(2, 1)]
        assert db.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    engine.dispose()
