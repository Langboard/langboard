"""SQLite/PostgreSQL mutation, native HTTP, concurrency, and migration evidence."""

from concurrent.futures import ThreadPoolExecutor
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from threading import Barrier, Event
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from langboard_shared.core.db import DbSession
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import (
    GroupDirectoryAudit,
    Organization,
    ScimGroup,
    ScimGroupMember,
    SettingRole,
    User,
    UserGroup,
)
from langboard_shared.domain.models.SettingRole import SettingRoleAction
from langboard_shared.domain.services.factory.GroupDirectoryService import GroupDirectoryConflict, GroupDirectoryDenied
from langboard_shared.Env import Env
from sqlalchemy import delete, event, inspect, select, text, update
from test_group_directory import directory_database as directory_database_fixture
from test_group_directory_http import directory_http as directory_http_fixture


# Re-export native fixture definitions for this coupled contract suite.
directory_database = directory_database_fixture
directory_http = directory_http_fixture


@pytest.fixture
def managed(directory_database, monkeypatch):
    f = directory_database
    for model in (Organization, SettingRole, GroupDirectoryAudit):
        model.__table__.create(f.engine)
    monkeypatch.setattr(type(Env), "FULL_ADMIN_ACCESS_EMAILS", property(lambda _: []))
    with DbSession.atomic() as db:
        actor = f.users[0]
        actor.is_admin = True
        db.update(actor)
        role = SettingRole(user_id=actor.id, actions=["group_read", "group_manage"])
        db.insert(role)
        org = Organization(name="Canonical", slug="canonical", owner_user_id=actor.id)
        db.insert(org)
    f.actor, f.role, f.org = actor, role, org
    f.directory = f.service.group_directory
    return f


def mutate(f, action, group=None, revision=1, **kw):
    return f.directory.mutate(
        f.actor.id, action, group_uid=(group or f.groups[0]).get_uid(), expected_revision=revision, **kw
    )


def snapshot(f):
    with f.engine.connect() as db:
        return {
            model.__name__: [tuple(row) for row in db.execute(select(model.__table__).order_by(model.id)).all()]
            for model in (User, SettingRole, ScimGroup, ScimGroupMember, UserGroup, GroupDirectoryAudit)
        }


def test_local_crud_deltas_durable_private_audit_and_no_grants(managed):
    f = managed
    before = snapshot(f)
    created = mutate(f, "create", display_name="Same name")["group"]
    uid = created["uid"]
    assert created["revision"] == 1 and not created["read_only"]
    assert created["source"] == "local" and created["external_id"] is None
    result = f.directory.mutate(f.actor.id, "rename", uid, 1, display_name="Renamed")["group"]
    assert result["revision"] == 2 and result["display_name"] == "Renamed"
    f.directory.mutate(f.actor.id, "add_members", uid, 2, user_uids=[f.actor.get_uid(), f.actor.get_uid()])
    assert f.directory.list_members(uid)["total_records"] == 1
    f.directory.mutate(f.actor.id, "remove_members", uid, 3, user_uids=[f.actor.get_uid()])
    assert f.directory.list_members(uid)["total_records"] == 0
    assert f.directory.mutate(f.actor.id, "delete", uid, 4) == {"deleted": True, "group_uid": uid}
    after = snapshot(f)
    assert all(after[k] == before[k] for k in ("User", "SettingRole", "ScimGroup", "ScimGroupMember", "UserGroup"))
    history = f.directory.history(f.actor.id, uid)
    assert [record["revision"] for record in history["records"]] == [5, 4, 3, 2, 1]
    assert {record["action"] for record in history["records"]} == {
        "create",
        "rename",
        "add_members",
        "remove_members",
        "delete",
    }
    assert all(record["actor_uid"] == f.actor.get_uid() for record in history["records"])
    assert "email" not in str(history) and "private-password" not in str(history)
    # History UIDs survive actual account deletion; no user/group foreign key.
    with f.engine.begin() as db:
        db.execute(delete(ScimGroupMember).where(ScimGroupMember.user_id == f.actor.id))
        db.execute(delete(SettingRole).where(SettingRole.user_id == f.actor.id))
        db.execute(delete(UserGroup).where(UserGroup.user_id == f.actor.id))
        db.execute(delete(Organization))
        db.execute(delete(User).where(User.id == f.actor.id))
        assert len(db.execute(select(GroupDirectoryAudit.id)).all()) == 5


def test_stale_and_source_boundaries_leave_no_partial_effects(managed):
    f = managed
    mutate(f, "rename", display_name="Revision two")
    before = snapshot(f)
    for action, arguments in (
        ("rename", {"display_name": "Stale"}),
        ("delete", {}),
        ("add_members", {"user_uids": [f.actor.get_uid()]}),
        ("remove_members", {"user_uids": [f.actor.get_uid()]}),
    ):
        with pytest.raises(GroupDirectoryConflict):
            mutate(f, action, **arguments)
    for group in f.groups[1:]:
        for action, arguments in (
            ("rename", {"display_name": "Unsafe"}),
            ("delete", {}),
            ("add_members", {"user_uids": [f.actor.get_uid()]}),
            ("remove_members", {"user_uids": [f.actor.get_uid()]}),
        ):
            with pytest.raises(GroupDirectoryDenied):
                mutate(f, action, group=group, **arguments)
    assert snapshot(f) == before
    # Explicit delta only touches requested member, retaining hidden inactive/deleted members.
    mutate(f, "remove_members", revision=2, user_uids=[f.actor.get_uid()])
    assert {row["uid"] for row in f.directory.list_members(f.groups[0].get_uid())["records"]} == {
        u.get_uid() for u in f.users[1:]
    }


@pytest.mark.parametrize(
    "change", ["role", "admin", "inactive", "deleted", "empty_tenant", "inactive_tenant", "suspended_tenant"]
)
def test_current_authority_rechecked_for_every_mutation_and_candidates(managed, change):
    f = managed
    assert f.directory.can_manage(f.actor.id)
    with f.engine.begin() as db:
        if change == "role":
            db.execute(update(SettingRole).values(actions=["group_read"]))
        elif change == "admin":
            db.execute(update(User).where(User.id == f.actor.id).values(is_admin=False))
        elif change in ("inactive", "deleted"):
            db.execute(
                update(User)
                .where(User.id == f.actor.id)
                .values(**({"activated_at": None} if change == "inactive" else {"deleted_at": SafeDateTime.now()}))
            )
        elif change == "empty_tenant":
            db.execute(delete(Organization))
        else:
            db.execute(
                update(Organization).values(
                    **({"is_active": False} if change == "inactive_tenant" else {"suspended_at": SafeDateTime.now()})
                )
            )
    before = snapshot(f)
    for action, arguments in (
        ("create", {"display_name": "Denied"}),
        ("rename", {"display_name": "Denied"}),
        ("delete", {}),
        ("add_members", {"user_uids": [f.actor.get_uid()]}),
        ("remove_members", {"user_uids": [f.actor.get_uid()]}),
    ):
        with pytest.raises(GroupDirectoryDenied):
            mutate(f, action, **arguments)
    with pytest.raises(GroupDirectoryDenied):
        f.directory.candidates(f.actor.id)
    assert snapshot(f) == before


def test_candidates_active_only_bounded_and_role_separate(managed):
    f = managed
    candidates = f.directory.candidates(f.actor.id, limit=1)
    assert candidates["total_records"] == 1
    assert candidates["records"][0] == {
        "uid": f.actor.get_uid(),
        "firstname": "Member",
        "lastname": "0",
        "username": "member-0",
        "account_status": "active",
    }
    assert f.directory.candidates(f.actor.id, query="%")["total_records"] == 0
    assert f.directory.candidates(f.actor.id, query=f.actor.get_uid())["records"] == candidates["records"]
    # Soft-deleted accounts may retain activation timestamps after deletion.
    with f.engine.begin() as db:
        db.execute(update(User).where(User.id == f.users[2].id).values(activated_at=SafeDateTime.now()))
    assert f.directory.candidates(f.actor.id)["total_records"] == 1
    for args in ({"page": 0}, {"page": 1000001}, {"limit": 101}, {"query": "x" * 201}):
        with pytest.raises(ValueError):
            f.directory.candidates(f.actor.id, **args)
    assert SettingRoleAction.GroupManage not in SettingRole.get_default_actions()
    assert SettingRoleAction.GroupManage in SettingRole.get_all_actions()
    for user in f.users[1:]:
        before = snapshot(f)
        with pytest.raises(ValueError):
            mutate(f, "add_members", user_uids=[f.actor.get_uid(), user.get_uid()])
        assert snapshot(f) == before
    rows = f.directory.list_groups(actor_id=f.actor.id)["records"]
    assert all(row["read_only"] == (row["source"] != "local") for row in rows)
    with f.engine.begin() as db:
        db.execute(update(SettingRole).values(actions=["group_read"]))
    assert all(row["read_only"] for row in f.directory.list_groups(actor_id=f.actor.id)["records"])
    with pytest.raises(GroupDirectoryDenied):
        f.directory.candidates(f.actor.id)


def test_atomic_audit_failure_rolls_back_members_revision_and_group(managed):
    f = managed
    before = snapshot(f)

    def fail(_connection, _cursor, statement, _params, _context, _many):
        if statement.lstrip().upper().startswith("INSERT INTO GROUP_DIRECTORY_AUDIT"):
            raise RuntimeError("Injected audit storage failure")

    event.listen(f.engine, "before_cursor_execute", fail)
    try:
        for action, kw in (
            ("create", {"display_name": "Rollback"}),
            ("rename", {"display_name": "Rollback"}),
            ("delete", {}),
            ("remove_members", {"user_uids": [f.actor.get_uid()]}),
        ):
            with pytest.raises(RuntimeError, match="Injected audit"):
                mutate(f, action, **kw)
    finally:
        event.remove(f.engine, "before_cursor_execute", fail)
    assert snapshot(f) == before


def test_concurrent_same_revision_has_one_winner(managed):
    f = managed
    barrier = Barrier(2)

    def rename(name):
        barrier.wait(timeout=5)
        try:
            mutate(f, "rename", display_name=name)
            return "done"
        except GroupDirectoryConflict:
            return "stale"

    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(rename, name) for name in ("First", "Second")]
        assert sorted(future.result(timeout=10) for future in futures) == ["done", "stale"]
    history = f.directory.history(f.actor.id, f.groups[0].get_uid())
    assert history["total_records"] == 1 and history["records"][0]["revision"] == 2


def test_native_http_management_revision_privacy_and_deleted_history(directory_http):
    client, engine, actor, role, group, headers, refresh = directory_http
    client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
    with engine.begin() as db:
        db.execute(update(SettingRole).where(SettingRole.id == role.id).values(actions=["group_read", "group_manage"]))
    result = client.post("/settings/groups", headers=headers, json={"display_name": "Native"})
    assert result.status_code == 200, result.text
    created = result.json()["group"]
    endpoint = f"/settings/groups/{created['uid']}"
    assert created["revision"] == 1 and not created["read_only"]
    result = client.put(endpoint, headers=headers, json={"display_name": "Renamed", "expected_revision": 1})
    assert result.status_code == 200 and result.json()["group"]["revision"] == 2
    assert client.request("DELETE", endpoint, headers=headers, json={"expected_revision": 1}).status_code == 409
    body = {"user_uids": [actor.get_uid()], "expected_revision": 2}
    assert client.post(endpoint + "/members", headers=headers, json=body).status_code == 200
    body["expected_revision"] = 3
    assert client.request("DELETE", endpoint + "/members", headers=headers, json=body).status_code == 200
    result = client.get("/settings/groups/candidates", headers=headers)
    assert result.status_code == 200 and "email" not in result.text
    assert client.request("DELETE", endpoint, headers=headers, json={"expected_revision": 4}).status_code == 200
    history = client.get(endpoint + "/history", headers=headers)
    assert history.status_code == 200 and history.json()["total_records"] == 5
    assert "email" not in history.text
    for body in ({"display_name": " "}, {"display_name": "x", "source": "scim"}, {"display_name": "x" * 201}):
        assert client.post("/settings/groups", headers=headers, json=body).status_code == 400
    for revision in (0, "1", True):
        assert (
            client.put(
                f"/settings/groups/{group.get_uid()}",
                headers=headers,
                json={"display_name": "Invalid", "expected_revision": revision},
            ).status_code
            == 400
        )


def test_migration_roundtrip_preserves_existing_rows(directory_database):
    f = directory_database
    path = Path(__file__).resolve().parents[2] / "langboard/migrations/versions/20261024030000-15b6f7c83a90.py"
    spec = spec_from_file_location("directory_revision_migration", path)
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.down_revision == "f0254a74d298"
    with f.engine.begin() as db:
        old = [
            tuple(row)
            for row in db.execute(
                text("SELECT id, source, issuer, external_id, display_name FROM scim_group ORDER BY id")
            )
        ]
        db.execute(text("ALTER TABLE scim_group DROP COLUMN revision"))
        context = MigrationContext.configure(db)
        with Operations.context(context):
            migration.upgrade()
        assert all(row[0] == 1 for row in db.execute(text("SELECT revision FROM scim_group")))
        assert "group_directory_audit" in inspect(db).get_table_names()
        assert [
            tuple(row)
            for row in db.execute(
                text("SELECT id, source, issuer, external_id, display_name FROM scim_group ORDER BY id")
            )
        ] == old
        with Operations.context(context):
            migration.downgrade()
        assert "group_directory_audit" not in inspect(db).get_table_names()
        assert "revision" not in {col["name"] for col in inspect(db).get_columns("scim_group")}
        assert [
            tuple(row)
            for row in db.execute(
                text("SELECT id, source, issuer, external_id, display_name FROM scim_group ORDER BY id")
            )
        ] == old


@pytest.mark.parametrize("target", ["role", "account"])
def test_concurrent_committed_revocation_precedes_transaction_authority(managed, target):
    f = managed
    started = Event()
    with f.engine.connect() as connection:
        transaction = connection.begin()
        if target == "role":
            connection.execute(update(SettingRole).where(SettingRole.id == f.role.id).values(actions=["group_read"]))
        else:
            connection.execute(update(User).where(User.id == f.actor.id).values(is_admin=False))

        def rename():
            started.set()
            with pytest.raises(GroupDirectoryDenied):
                mutate(f, "rename", display_name="Revoked")

        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(rename)
            assert started.wait(timeout=2)
            # Concurrent revocation is still uncommitted; release its native DB lock.
            transaction.commit()
            future.result(timeout=10)
    with f.engine.connect() as db:
        assert db.execute(select(ScimGroup.revision).where(ScimGroup.id == f.groups[0].id)).scalar_one() == 1
        assert db.execute(select(GroupDirectoryAudit.id)).all() == []


def test_directory_transaction_blocks_competing_permission_writer(managed):
    f = managed
    entered, release, writer_started = Event(), Event(), Event()

    def pause(_connection, _cursor, statement, _params, _context, _many):
        if statement.lstrip().upper().startswith("INSERT INTO GROUP_DIRECTORY_AUDIT"):
            entered.set()
            assert release.wait(timeout=5)

    def revoke():
        writer_started.set()
        with f.engine.begin() as db:
            db.execute(update(SettingRole).where(SettingRole.id == f.role.id).values(actions=["group_read"]))

    event.listen(f.engine, "before_cursor_execute", pause)
    try:
        with ThreadPoolExecutor(2) as pool:
            mutation = pool.submit(mutate, f, "rename", display_name="Authorized")
            assert entered.wait(timeout=3)
            writer = pool.submit(revoke)
            assert writer_started.wait(timeout=2)
            assert not writer.done()
            release.set()
            mutation.result(timeout=10)
            writer.result(timeout=10)
    finally:
        release.set()
        event.remove(f.engine, "before_cursor_execute", pause)
    with pytest.raises(GroupDirectoryDenied):
        mutate(f, "rename", revision=2, display_name="Revoked")
    assert f.directory.history(f.actor.id, f.groups[0].get_uid())["total_records"] == 1


def test_no_mutation_writes_access_or_personal_addressbook_tables(managed):
    f = managed
    writes = []

    def record(_connection, _cursor, statement, _params, _context, _many):
        if statement.lstrip().upper().startswith(("INSERT ", "UPDATE ", "DELETE ")):
            writes.append(statement.lower())

    event.listen(f.engine, "before_cursor_execute", record)
    try:
        mutate(f, "rename", display_name="Entitlement unchanged")
        mutate(f, "remove_members", revision=2, user_uids=[f.actor.get_uid()])
        mutate(f, "add_members", revision=3, user_uids=[f.actor.get_uid()])
        mutate(f, "delete", revision=4)
    finally:
        event.remove(f.engine, "before_cursor_execute", record)
    assert writes
    assert all(
        statement.split()[1 if statement.startswith("update ") else 2].strip('"')
        in {"scim_group", "scim_group_member", "group_directory_audit"}
        for statement in writes
    )


def test_migration_refuses_to_discard_durable_audit(managed):
    f = managed
    mutate(f, "rename", display_name="Retained")
    path = Path(__file__).resolve().parents[2] / "langboard/migrations/versions/20261024030000-15b6f7c83a90.py"
    spec = spec_from_file_location("directory_retention_migration", path)
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    with f.engine.begin() as db:
        with Operations.context(MigrationContext.configure(db)):
            with pytest.raises(RuntimeError, match="retention review"):
                migration.downgrade()
        assert db.execute(select(GroupDirectoryAudit.id)).first()
        assert "revision" in {column["name"] for column in inspect(db).get_columns("scim_group")}


def test_http_manage_is_independent_of_read_and_full_admin_override(directory_http, monkeypatch):
    client, engine, actor, role, group, headers, refresh = directory_http
    client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
    with engine.begin() as db:
        db.execute(update(SettingRole).where(SettingRole.id == role.id).values(actions=["group_manage"]))
    assert client.get("/settings/groups", headers=headers).status_code == 403
    assert client.get(f"/settings/groups/{group.get_uid()}/history", headers=headers).status_code == 403
    assert client.get("/settings/groups/candidates", headers=headers).status_code == 200
    assert client.post("/settings/groups", headers=headers, json={"display_name": "Manage only"}).status_code == 200
    with engine.begin() as db:
        db.execute(update(SettingRole).where(SettingRole.id == role.id).values(actions=[]))
    assert client.post("/settings/groups", headers=headers, json={"display_name": "Revoked"}).status_code == 403
    monkeypatch.setattr(type(Env), "FULL_ADMIN_ACCESS_EMAILS", property(lambda _: [actor.email]))
    assert client.post("/settings/groups", headers=headers, json={"display_name": "Full admin"}).status_code == 200
    with engine.begin() as db:
        db.execute(update(User).where(User.id == actor.id).values(is_admin=False))
    assert client.post("/settings/groups", headers=headers, json={"display_name": "Not admin"}).status_code == 403


def test_native_lock_contention_is_bounded_and_has_no_partial_writes(managed):
    from time import monotonic

    f = managed
    before = snapshot(f)
    with f.engine.connect() as blocker:
        transaction = blocker.begin()
        blocker.execute(update(SettingRole).where(SettingRole.id == f.role.id).values(actions=["group_read"]))
        started = monotonic()
        try:
            with pytest.raises(GroupDirectoryConflict):
                mutate(f, "rename", display_name="Timed out")
            assert monotonic() - started < 9
        finally:
            transaction.rollback()
    assert snapshot(f) == before
