import json
import os
from uuid import uuid4
import pytest
from langboard.commands.AuditInstanceDirectoryCommand import audit_directory, main
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain import models
from langboard_shared.Env import Env
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session


@pytest.fixture(params=["sqlite", "postgresql"])
def directory_database(tmp_path, request):
    admin = None
    if request.param == "postgresql":
        url = os.getenv("LANGBOARD_TEST_DATABASE_URL") or os.getenv("LANGBOARD_FILE_TEST_DATABASE_URL")
        if not url:
            pytest.skip("requires LANGBOARD_TEST_DATABASE_URL")
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
        admin = create_engine(url)
        schema = "directory_audit_" + uuid4().hex
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_engine(url, connect_args={"options": f"-csearch_path={schema} -cstatement_timeout=10000"})
    else:
        engine = create_engine(f"sqlite:///{tmp_path / 'directory.sqlite'}")
    required = {
        model.__table__
        for model in (
            models.Organization,
            models.Project,
            models.ScimGroup,
            models.ScimGroupMember,
            models.User,
            models.UserGroup,
            models.UserGroupAssignedEmail,
            models.UserIdentityLink,
        )
    }
    pending = list(required)
    while pending:
        for foreign in pending.pop().foreign_keys:
            if foreign.column.table not in required:
                required.add(foreign.column.table)
                pending.append(foreign.column.table)
    try:
        models.Project.metadata.create_all(engine, tables=list(required))
        with Session(engine) as db:
            db.add(
                models.User(
                    id=1,
                    firstname="Private",
                    lastname="Name",
                    email="private@example.invalid",
                    password="secret",
                    activated_at=SafeDateTime.now(),
                )
            )
            db.commit()
        yield engine, required
    finally:
        engine.dispose()
        if admin is not None:
            with admin.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            admin.dispose()


def snapshot(engine, tables):
    with engine.connect() as connection:
        return {table.name: connection.execute(select(table).order_by(table.c.id)).all() for table in tables}


@pytest.mark.parametrize(
    "organization_count,active,expected",
    [
        (0, True, "tenant_bootstrap_requires_explicit_mapping"),
        (1, False, "sole_organization_is_inactive"),
        (2, True, "multiple_organizations_require_manual_review"),
    ],
)
def test_ambiguous_tenants_stop_without_merging_or_deleting(directory_database, organization_count, active, expected):
    engine, tables = directory_database
    with Session(engine) as db:
        for index in range(organization_count):
            db.add(
                models.Organization(
                    id=10 + index,
                    name="Private organization",
                    slug=f"private-{index}",
                    owner_user_id=1,
                    is_active=active if index == 0 else False,
                )
            )
        db.add(models.Project(id=30, title="Private board", owner_id=1))
        db.commit()
    before = snapshot(engine, tables)
    with engine.connect() as connection:
        report = audit_directory(connection, "https://directory.invalid/")
    assert expected in report["blockers"]
    assert "unbound_projects_require_explicit_mapping" in report["blockers"]
    assert report["counts"]["organizations"] == organization_count
    assert report["migration_authorized"] is False
    assert snapshot(engine, tables) == before


def test_group_provenance_and_personal_address_books_remain_separate(directory_database, monkeypatch, capsys):
    engine, tables = directory_database
    with Session(engine) as db:
        db.add(models.Organization(id=10, name="Private organization", slug="private", owner_user_id=1))
        db.commit()
        db.add(models.Project(id=30, title="Private board", owner_id=1, organization_id=10))
        db.add(models.ScimGroup(id=40, display_name="Private group", external_id="private-external-id"))
        db.commit()
        db.add(models.ScimGroupMember(id=50, group_id=40, user_id=1))
        if engine.dialect.name == "sqlite":
            # A legacy broken reference fixture; PostgreSQL keeps its foreign keys enabled.
            db.add(models.ScimGroupMember(id=51, group_id=999, user_id=1))
        db.add(
            models.UserIdentityLink(
                id=60,
                user_id=1,
                provider=models.IdentityProvider.Scim,
                issuer="https://old-directory.invalid",
                external_id="private-external-user",
            )
        )
        db.add(models.UserGroup(id=70, user_id=1, name="Private address book"))
        db.commit()
        db.add(models.UserGroupAssignedEmail(id=80, group_id=70, email="private-contact@example.invalid"))
        db.commit()
    before = snapshot(engine, tables)
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    monkeypatch.setattr(type(Env), "SCIM_ISSUER", property(lambda _: "https://current-directory.invalid"))

    def audit_with_transaction_checks(connection, issuer):
        if engine.dialect.name == "postgresql":
            assert connection.exec_driver_sql("SHOW transaction_read_only").scalar() == "on"
            assert connection.exec_driver_sql("SHOW transaction_isolation").scalar() == "repeatable read"
        else:
            assert connection.exec_driver_sql("PRAGMA query_only").scalar() == 1
        return audit_directory(connection, issuer)

    monkeypatch.setattr(
        "langboard.commands.AuditInstanceDirectoryCommand.audit_directory", audit_with_transaction_checks
    )
    assert main() == 2
    output = capsys.readouterr().out
    report = json.loads(output)
    assert report["counts"]["personal_address_groups"] == 1
    assert report["counts"]["personal_address_entries"] == 1
    assert report["counts"]["orphan_scim_memberships"] == (1 if engine.dialect.name == "sqlite" else 0)
    assert report["counts"]["unresolved_scim_identity_links"] == 1
    assert "legacy_group_source_and_issuer_require_provenance" in report["blockers"]
    assert ("orphan_scim_memberships" in report["blockers"]) == (engine.dialect.name == "sqlite")
    assert "private" not in output.lower()
    assert "secret" not in output
    assert snapshot(engine, tables) == before
    with engine.begin() as connection:
        if engine.dialect.name == "sqlite":
            assert connection.exec_driver_sql("PRAGMA query_only").scalar() == 0
        else:
            assert connection.exec_driver_sql("SHOW transaction_read_only").scalar() == "off"


def test_single_active_tenant_empty_directory_does_not_authorize_migration(directory_database):
    engine, _ = directory_database
    with Session(engine) as db:
        db.add(models.Organization(id=10, name="Single", slug="single", owner_user_id=1))
        db.commit()
        db.add(models.Project(id=30, title="Board", owner_id=1, organization_id=10))
        db.commit()
    with engine.connect() as connection:
        report = audit_directory(connection, "")
    assert report["blockers"] == []
    assert report["migration_authorized"] is False


def test_missing_schema_fails_without_a_clean_report_and_restores_connection(directory_database, monkeypatch, capsys):
    engine, _ = directory_database
    models.ScimGroupMember.__table__.drop(engine)
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    with pytest.raises(DBAPIError):
        main()
    assert capsys.readouterr().out == ""
    with engine.connect() as connection:
        if engine.dialect.name == "sqlite":
            assert connection.exec_driver_sql("PRAGMA query_only").scalar() == 0
        else:
            assert connection.exec_driver_sql("SHOW transaction_read_only").scalar() == "off"


@pytest.mark.parametrize("target", ["organization", "project"])
@pytest.mark.parametrize("state", ["inactive", "deleted", "missing"])
def test_unavailable_owners_block_mapping_without_rewriting_data(directory_database, target, state):
    engine, tables = directory_database
    if state == "missing" and engine.dialect.name == "postgresql":
        pytest.skip("PostgreSQL foreign keys reject missing owners before the audit")
    with Session(engine) as db:
        db.add(
            models.User(
                id=2,
                firstname="Unavailable",
                lastname="Owner",
                email="unavailable@example.invalid",
                password="secret",
                activated_at=None if state == "inactive" else SafeDateTime.now(),
                deleted_at=SafeDateTime.now() if state == "deleted" else None,
            )
        )
        db.commit()
        owner_id = 999 if state == "missing" else 2
        db.add(
            models.Organization(
                id=10, name="Single", slug="single", owner_user_id=owner_id if target == "organization" else 1
            )
        )
        db.commit()
        db.add(
            models.Project(id=30, title="Board", organization_id=10, owner_id=owner_id if target == "project" else 1)
        )
        db.commit()
    before = snapshot(engine, tables)
    with engine.connect() as connection:
        report = audit_directory(connection, "")
    assert report["counts"][f"unavailable_{target}_owners"] == 1
    assert f"unavailable_{target}_owners" in report["blockers"]
    assert report["migration_authorized"] is False
    assert snapshot(engine, tables) == before
