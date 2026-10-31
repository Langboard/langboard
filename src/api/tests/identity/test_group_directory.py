"""Actual database proof for the bounded installation-wide native directory."""

import os
from types import SimpleNamespace
from uuid import uuid4
import pytest
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import ScimGroup, ScimGroupMember, SettingRole, User, UserGroup
from langboard_shared.domain.models.SettingRole import SettingRoleAction, SettingRoleCategory
from langboard_shared.domain.services import DomainService
from langboard_shared.Env import Env
from sqlalchemy import create_engine, event, select, text


@pytest.fixture(params=["sqlite", "postgresql"])
def directory_database(request, tmp_path, monkeypatch):
    admin = None
    schema = None
    if request.param == "postgresql":
        url = os.getenv("LANGBOARD_FILE_TEST_DATABASE_URL") or os.getenv("LANGBOARD_TEST_DATABASE_URL")
        if not url:
            pytest.skip("Requires an explicitly disposable PostgreSQL database")
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+psycopg://", 1)
        admin = create_engine(url)
        assert admin.dialect.name == "postgresql"
        schema = "group_directory_" + uuid4().hex
        with admin.begin() as db:
            db.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    else:
        engine = create_engine(f"sqlite:///{tmp_path / 'directory.sqlite'}")
    try:
        for model in (User, ScimGroup, ScimGroupMember, UserGroup):
            model.__table__.create(engine)
        monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
        monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
        monkeypatch.setattr(type(Env), "SCIM_ISSUER", property(lambda _: "https://current.example/"))
        groups = []
        users = []
        with DbSession.atomic() as db:
            for index, status in enumerate(("active", "inactive", "deleted")):
                user = User(
                    firstname="Member",
                    lastname=str(index),
                    username=f"member-{index}",
                    email=f"private{index}@example.invalid",
                    password="private-password",
                    activated_at=SafeDateTime.now() if status == "active" else None,
                    deleted_at=SafeDateTime.now() if status == "deleted" else None,
                )
                db.insert(user)
                users.append(user)
            for source, issuer, external in (
                ("local", "", None),
                ("legacy-unresolved", "", "historical"),
                ("scim", "https://current.example", "same"),
                ("scim", "https://other.example", "same"),
            ):
                group = ScimGroup(source=source, issuer=issuer, external_id=external, display_name="Same name")
                db.insert(group)
                groups.append(group)
                for user in users:
                    db.insert(ScimGroupMember(group_id=group.id, user_id=user.id))
            literal = ScimGroup(source="local", issuer="", display_name="100%_staff/path")
            db.insert(literal)
            db.insert(UserGroup(user_id=users[0].id, name="private-address-book"))
        service = DomainService()
        yield SimpleNamespace(engine=engine, groups=groups, literal=literal, users=users, service=service)
        service.close()
    finally:
        engine.dispose()
        if admin is not None:
            with admin.begin() as db:
                db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            admin.dispose()


def test_directory_all_sources_literal_search_and_bounded_reads(directory_database):
    f = directory_database
    statements = []

    def record(_connection, _cursor, statement, _params, _context, _many):
        statements.append(statement)

    event.listen(f.engine, "before_cursor_execute", record)
    try:
        result = f.service.group_directory.list_groups(limit=2, query="Same")
        assert result["total_records"] == 4
        assert len(result["records"]) == 2
        assert len(statements) == 2
        assert all(sql.lstrip().upper().startswith("SELECT") for sql in statements)
        assert "LIMIT" in statements[1].upper() and "OFFSET" in statements[1].upper()
        first = result["records"]
        second = f.service.group_directory.list_groups(page=2, limit=2, query="Same")["records"]
        assert len({row["uid"] for row in first + second}) == 4
        assert {row["source_status"] for row in first + second} == {
            "local",
            "legacy-unresolved",
            "current_authority",
            "other_authority",
        }
        assert all(
            row["read_only"] and not any(value for key, value in row["abilities"].items() if key != "read_members")
            for row in first + second
        )
        assert sum(row["managed"] for row in first + second) == 2
        for query in ("%", "_", "/path", "%_staff/", "100%_staff/path"):
            rows = f.service.group_directory.list_groups(query=query)
            assert rows["total_records"] == 1
            assert rows["records"][0]["uid"] == f.literal.get_uid()
        assert f.service.group_directory.list_groups(query="https://other.example")["total_records"] == 1
        assert f.service.group_directory.list_groups(query="private-address-book")["total_records"] == 0
        assert f.service.group_directory.list_groups(page=99)["records"] == []
        # Native reads leave the protocol's current-authority boundary intact.
        with DbSession.use(readonly=True) as db:
            protocol_groups = db.exec(
                SqlBuilder.select.table(ScimGroup).where(*ScimGroup.authority_filters(Env.SCIM_ISSUER))
            ).all()
        assert [g.id for g in protocol_groups] == [f.groups[2].id]
    finally:
        event.remove(f.engine, "before_cursor_execute", record)


def test_directory_members_minimal_status_pagination_and_no_mutations(directory_database):
    f = directory_database
    for group in f.groups:
        statements = []

        def record(_connection, _cursor, statement, _params, _context, _many):
            statements.append(statement)

        event.listen(f.engine, "before_cursor_execute", record)
        try:
            result = f.service.group_directory.list_members(group.get_uid(), limit=2)
        finally:
            event.remove(f.engine, "before_cursor_execute", record)
        assert result["total_records"] == 3
        assert len(result["records"]) == 2
        assert len(statements) == 3
        assert all(sql.lstrip().upper().startswith("SELECT") for sql in statements)
        assert "password" not in statements[-1].lower() and "email" not in statements[-1].lower()
        assert "LIMIT" in statements[-1].upper() and "OFFSET" in statements[-1].upper()
        last = f.service.group_directory.list_members(group.get_uid(), page=2, limit=2)["records"]
        records = result["records"] + last
        assert [r["account_status"] for r in records] == ["active", "inactive", "deleted"]
        assert {r["uid"] for r in records} == {u.get_uid() for u in f.users}
        assert all(set(row) == {"uid", "firstname", "lastname", "username", "account_status"} for row in records)
        assert last[0]["firstname"] == last[0]["lastname"] == last[0]["username"] == ""
        assert f.service.group_directory.list_members(group.get_uid(), query="member-1")["total_records"] == 1
        assert f.service.group_directory.list_members(group.get_uid(), query="%")["total_records"] == 0
    assert f.service.group_directory.list_members(f.literal.get_uid())["total_records"] == 0
    assert f.service.group_directory.list_members("00000000000") is None
    with f.engine.connect() as db:
        assert len(db.execute(select(ScimGroupMember.id)).all()) == 12
        assert len(db.execute(select(UserGroup.id)).all()) == 1


def test_deleted_member_identity_cannot_be_recovered_by_search(directory_database):
    f = directory_database
    group = f.groups[0]
    for query in (f.users[2].username, f.users[2].lastname):
        result = f.service.group_directory.list_members(group.get_uid(), query=query)
        assert result["total_records"] == 0
        assert result["records"] == []
    # Retain inactive-member discovery and the redacted deleted membership row.
    result = f.service.group_directory.list_members(group.get_uid(), query=f.users[1].username)
    assert result["total_records"] == 1
    assert result["records"][0]["account_status"] == "inactive"
    assert f.service.group_directory.list_members(group.get_uid())["total_records"] == 3


@pytest.mark.parametrize(
    "arguments", [{"page": 0}, {"page": 1000001}, {"limit": 0}, {"limit": 101}, {"query": "x" * 201}]
)
def test_directory_service_rejects_unbounded_requests(directory_database, arguments):
    with pytest.raises(ValueError):
        directory_database.service.group_directory.list_groups(**arguments)
    with pytest.raises(ValueError):
        directory_database.service.group_directory.list_members(directory_database.groups[0].get_uid(), **arguments)


def test_group_read_is_distinct_and_not_default():
    assert SettingRoleAction.GroupRead not in SettingRole.get_default_actions()
    assert SettingRoleAction.GroupRead in SettingRole.get_all_actions()
    role = SettingRole(user_id=1, actions=["user_read"])
    assert not role.has_category_permission(SettingRoleCategory.Group)
    assert not role.is_granted(SettingRoleAction.GroupRead)
    role.actions = ["group_read"]
    assert role.has_category_permission(SettingRoleCategory.Group)
    role.actions = ["*"]
    assert role.is_granted(SettingRoleAction.GroupRead)
