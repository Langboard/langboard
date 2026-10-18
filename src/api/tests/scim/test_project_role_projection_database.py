"""Native PostgreSQL assignment concurrency and failed-readback retry semantics."""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4
import pytest
from sqlalchemy import create_engine, text
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


DATABASE_URL = os.getenv("LANGBOARD_TEST_DATABASE_URL") or os.getenv("LANGBOARD_FILE_TEST_DATABASE_URL")
if DATABASE_URL and DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="requires LANGBOARD_TEST_DATABASE_URL")
ISSUER = "https://directory.example/scim"


@pytest.fixture
def role_database(monkeypatch):
    assert DATABASE_URL is not None
    admin = create_engine(DATABASE_URL)
    schema = "scim_roles_" + uuid4().hex
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
