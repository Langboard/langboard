"""Native PostgreSQL assignment concurrency and failed-readback retry semantics."""

import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from threading import Barrier
from uuid import uuid4
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


os.environ.setdefault("PROJECT_NAME", "langboard")

from langboard_shared.core.db.DbEngine import DbEngine  # noqa: E402
from langboard_shared.core.exceptions import ScimProvisioningException  # noqa: E402
from langboard_shared.core.types import SafeDateTime  # noqa: E402
from langboard_shared.domain import models  # noqa: E402
from langboard_shared.domain.services import DomainService  # noqa: E402
from langboard_shared.Env import Env  # noqa: E402
from langboard_shared.infrastructure.repositories.factory.ProjectAssignedUserRepository import (  # noqa: E402
    ProjectAssignedUserRepository,
)
from langboard_shared.infrastructure.repositories.factory.ScimGroupRepository import ScimGroupRepository  # noqa: E402


DATABASE_URL = os.getenv("LANGBOARD_TEST_DATABASE_URL") or os.getenv("LANGBOARD_FILE_TEST_DATABASE_URL")
if DATABASE_URL and DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="requires LANGBOARD_TEST_DATABASE_URL")
ISSUER = "https://directory.example/scim"


@pytest.mark.parametrize("operation", ["replace", "delete"])
@pytest.mark.parametrize("bound", [False, True])
def test_failed_group_mutation_preserves_directory_and_entitlements(role_database, monkeypatch, operation, bound):
    engine, project, _, employee = role_database
    with closing(DomainService()) as service:
        provisioning = service.scim_provisioning
        group = provisioning.create_group(
            {
                "displayName": "Original",
                "externalId": f"project-role:{project.get_uid()}:contributor" if bound else "ordinary-original",
                "members": [{"value": employee.get_uid()}],
            }
        )
        before = snapshot(engine)
        if operation == "replace":
            repository = provisioning.repo.scim_group_member
            original = repository.replace_group_members

            def fail_after_write(*args, **kwargs):
                original(*args, **kwargs)
                raise RuntimeError("fixture mutation interrupted")

            monkeypatch.setattr(repository, "replace_group_members", fail_after_write)

            def mutate():
                provisioning.apply_group_mutations(group, {"displayName": "Changed", "members": []})
        else:
            repository = provisioning.repo.scim_group
            original = repository.delete

            def fail_after_write(*args, **kwargs):
                original(*args, **kwargs)
                raise RuntimeError("fixture mutation interrupted")

            monkeypatch.setattr(repository, "delete", fail_after_write)

            def mutate():
                provisioning.delete_group(group)

        with pytest.raises(RuntimeError, match="fixture mutation interrupted"):
            mutate()
        assert snapshot(engine) == before


@pytest.fixture
def role_database(monkeypatch):
    assert DATABASE_URL is not None
    admin = create_engine(DATABASE_URL)
    schema = "group_provenance_" + uuid4().hex
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(
        DATABASE_URL, connect_args={"options": f"-csearch_path={schema} -cstatement_timeout=10000 -clock_timeout=3000"}
    )
    try:
        required = {
            model.__table__
            for model in (
                models.Project,
                models.ProjectAssignedUser,
                models.ProjectRole,
                models.ProjectUserRelationship,
                models.UserIdentityLink,
                models.ScimGroup,
                models.ScimGroupMember,
            )
        }
        pending = list(required)
        while pending:
            for foreign in pending.pop().foreign_keys:
                if foreign.column.table not in required:
                    required.add(foreign.column.table)
                    pending.append(foreign.column.table)
        models.Project.metadata.create_all(engine, tables=list(required))
        monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
        monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
        monkeypatch.setattr(type(Env), "SCIM_ISSUER", property(lambda _: ISSUER))
        with Session(engine, expire_on_commit=False) as db:
            owner = models.User(
                id=10,
                firstname="Fixture",
                lastname="Owner",
                email="owner@example.invalid",
                password="test-only",
                activated_at=SafeDateTime.now(),
            )
            employee = models.User(
                id=20,
                firstname="Fixture",
                lastname="Employee",
                email="employee@example.invalid",
                password="test-only",
                activated_at=SafeDateTime.now(),
            )
            db.add_all([owner, employee])
            db.commit()
            project = models.Project(id=1, owner_id=owner.id, title="SCIM fixture")
            db.add(project)
            db.add(
                models.UserIdentityLink(
                    user_id=employee.id, provider=models.IdentityProvider.Scim, external_id="employee-20", issuer=ISSUER
                )
            )
            db.commit()
        yield engine, project, owner, employee
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def test_concurrent_member_ensure_creates_one_assignment(role_database) -> None:
    engine, project, _, user = role_database
    repository = ProjectAssignedUserRepository(lambda _type: None, lambda _name: None)
    start = Barrier(2)

    def ensure() -> bool:
        start.wait()
        return repository.ensure_assigned(project, user)[1]

    with ThreadPoolExecutor(max_workers=2) as executor:
        created = [result.result(timeout=10) for result in (executor.submit(ensure), executor.submit(ensure))]
    assert sorted(created) == [False, True]
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM project_assigned_user")).scalar_one() == 1


def snapshot(engine):
    fields = {
        "scim_group": "id, external_id, display_name",
        "scim_group_member": "group_id, user_id",
        "project_assigned_user": "project_id, user_id, starred",
        "project_role": "project_id, user_id, actions",
        "project_user_relationship": "project_id, user_id, related_user_id",
    }
    with engine.connect() as connection:
        return {
            table: sorted(connection.execute(text(f"SELECT {columns} FROM {table}")).all())
            for table, columns in fields.items()
        }


@pytest.mark.parametrize("role_binding", [False, True])
def test_concurrent_group_upsert_preserves_one_identity_and_members(role_database, monkeypatch, role_binding):
    engine, project, _, employee = role_database
    ready_to_insert = Barrier(2)
    insert = ScimGroupRepository.insert

    def concurrent_insert(repository, group):
        ready_to_insert.wait(timeout=5)
        return insert(repository, group)

    monkeypatch.setattr(ScimGroupRepository, "insert", concurrent_insert)
    payload = {
        "externalId": f"project-role:{project.get_uid()}:contributor" if role_binding else "directory-stable-group",
        "displayName": "Engineering",
        "members": [{"value": employee.get_uid()}],
    }

    def upsert():
        service = DomainService()
        try:
            return service.scim_provisioning.create_or_upsert_group(payload).id
        finally:
            service.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [future.result(timeout=10) for future in (executor.submit(upsert), executor.submit(upsert))]
    assert results[0] == results[1]
    with engine.connect() as connection:
        assert connection.execute(text("SELECT id, external_id FROM scim_group")).all() == [
            (results[0], payload["externalId"])
        ]
        assert connection.execute(text("SELECT group_id, user_id FROM scim_group_member")).all() == [
            (results[0], employee.id)
        ]
        assert connection.execute(text("SELECT count(*) FROM project_role")).scalar_one() == int(role_binding)
        assert connection.execute(text("SELECT count(*) FROM project_assigned_user")).scalar_one() == int(role_binding)
    if role_binding:
        service = DomainService()
        try:
            role = service.scim_provisioning.repo.role.project.get_one(
                user_id=employee.id, project_id=project.id, consistent=True
            )
            assert set(role.actions) == {"read", "card_write", "card_update"}
        finally:
            service.close()


def test_failed_group_member_write_does_not_leave_partial_group(role_database, monkeypatch):
    engine, project, _, employee = role_database
    service = DomainService()
    provisioning = service.scim_provisioning
    before = snapshot(engine)
    replace = provisioning.repo.scim_group_member.replace_group_members

    def fail_after_members(group, user_ids):
        replace(group, user_ids)
        raise RuntimeError("fixture member write failure")

    monkeypatch.setattr(provisioning.repo.scim_group_member, "replace_group_members", fail_after_members)
    try:
        with pytest.raises(RuntimeError, match="fixture member write failure"):
            provisioning.create_or_upsert_group(
                {
                    "externalId": provisioning._project_role_external_id(project, "contributor"),
                    "displayName": "Engineering",
                    "members": [{"value": employee.get_uid()}],
                }
            )
        assert snapshot(engine) == before
    finally:
        service.close()


def test_entitlement_integrity_error_is_not_retried_as_a_group_creation_race(role_database, monkeypatch):
    engine, _, _, employee = role_database
    service = DomainService()
    provisioning = service.scim_provisioning
    calls = []

    def fail_entitlements(*external_ids):
        calls.append(external_ids)
        raise IntegrityError("fixture entitlement failure", {}, RuntimeError("unrelated constraint"))

    monkeypatch.setattr(provisioning, "_reconcile_bound_external_ids", fail_entitlements)
    try:
        with pytest.raises(IntegrityError):
            provisioning.create_or_upsert_group(
                {
                    "externalId": "directory-entitlement-failure",
                    "displayName": "Engineering",
                    "members": [{"value": employee.get_uid()}],
                }
            )
        assert len(calls) == 1
        with engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM scim_group")).scalar_one() == 1
            assert connection.execute(text("SELECT count(*) FROM scim_group_member")).scalar_one() == 1
            assert connection.execute(text("SELECT count(*) FROM project_role")).scalar_one() == 0
    finally:
        service.close()


@pytest.mark.parametrize("operation", ["create", "upsert", "replace", "patch", "delete"])
def test_failed_readback_retains_desired_state_and_retry_does_not_duplicate(
    role_database, monkeypatch, operation
) -> None:
    engine, project, owner, employee = role_database
    service = DomainService()
    provisioning = service.scim_provisioning
    provisioning.repo.project_assigned_user.ensure_assigned(project, owner)
    payload = {
        "externalId": provisioning._project_role_external_id(project, "contributor"),
        "displayName": "Fixture contributors",
        "members": [{"value": employee.get_uid()}],
    }
    group = None
    if operation != "create":
        group = provisioning.create_group({**payload, "members": payload["members"] if operation == "delete" else []})
    verify = provisioning._verify_project_entitlements

    def fail_readback(*_args):
        raise ScimProvisioningException.Unavailable()

    def mutate():
        if operation in {"create", "upsert"}:
            return provisioning.create_or_upsert_group(payload)
        if operation == "replace":
            return provisioning.apply_group_mutations(group, payload)
        if operation == "patch":
            return provisioning.apply_group_patch(
                group, [{"op": "replace", "path": "members", "value": payload["members"]}]
            )
        return provisioning.delete_group(group)

    monkeypatch.setattr(provisioning, "_verify_project_entitlements", fail_readback)
    try:
        with pytest.raises(ScimProvisioningException.Unavailable):
            mutate()
        with engine.connect() as connection:
            groups = connection.execute(text("SELECT id FROM scim_group")).scalars().all()
            members = connection.execute(text("SELECT user_id FROM scim_group_member")).scalars().all()
            assigned = (
                connection.execute(text("SELECT user_id FROM project_assigned_user ORDER BY user_id")).scalars().all()
            )
            roles = connection.execute(text("SELECT user_id FROM project_role")).scalars().all()
        if operation == "delete":
            assert groups == members == roles == []
            assert assigned == [owner.id]
            assert provisioning.resolve_group(group.get_uid()) is None
        else:
            assert len(groups) == 1
            assert members == roles == [employee.id]
            assert assigned == [owner.id, employee.id]
        before_retry = snapshot(engine)
        monkeypatch.setattr(provisioning, "_verify_project_entitlements", verify)
        if operation == "delete":
            # The public DELETE already achieved absence. Do not recreate a group to retry it.
            provisioning._reconcile_bound_external_ids(payload["externalId"])
        else:
            mutate()
        assert snapshot(engine) == before_retry
        role = provisioning.repo.role.project.get_one(user_id=employee.id, project_id=project.id, consistent=True)
        if operation == "delete":
            assert role is None
        else:
            assert set(role.actions) == {"read", "card_write", "card_update"}
    finally:
        service.close()


@pytest.mark.parametrize("source,issuer", [("legacy-unresolved", ""), ("scim", "https://other.example")])
def test_foreign_role_groups_never_grant_or_override_current_authority(role_database, source, issuer):
    engine, project, owner, employee = role_database
    with Session(engine, expire_on_commit=False) as db:
        foreign = models.ScimGroup(
            source=source,
            issuer=issuer,
            display_name="Foreign owner",
            external_id=f"project-role:{project.get_uid()}:owner",
        )
        db.add(foreign)
        db.flush()
        db.add(models.ScimGroupMember(group_id=foreign.id, user_id=employee.id))
        db.commit()
    with closing(DomainService()) as service:
        provisioning = service.scim_provisioning
        assert provisioning._desired_project_roles(project) == {}
        assert provisioning._bound_projects_for_user(employee) == []
        provisioning.create_group(
            {
                "displayName": "Current viewer",
                "externalId": f"project-role:{project.get_uid()}:viewer",
                "members": [{"value": employee.get_uid()}],
            }
        )
        assert provisioning._desired_project_roles(project)[employee.id][1] == "viewer"
        assert project.owner_id == owner.id
