"""Real database mutations; socket and activity transports are controlled, never delivery proof."""

import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import pytest
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain import models as m
from langboard_shared.domain.services import DomainService
from langboard_shared.domain.services.factory.GroupDirectoryService import GroupDirectoryConflict, GroupDirectoryDenied
from langboard_shared.Env import Env
from langboard_shared.publishers import ProjectInvitationPublisher, ProjectPublisher
from langboard_shared.tasks.activities import ProjectActivityTask
from sqlalchemy import create_engine, select, update


@pytest.fixture(params=["sqlite", "postgresql"])
def native(tmp_path, monkeypatch, request):
    if request.param == "postgresql":
        url = os.getenv("LANGBOARD_GROUP_INVITE_TEST_DATABASE_URL")
        if not url:
            pytest.skip("Requires an explicitly disposable PostgreSQL database")
        engine = create_engine(url, connect_args={"client_encoding": "utf8"})
    else:
        engine = create_engine(f"sqlite:///{tmp_path / 'group.sqlite'}", connect_args={"timeout": 10})
    required = {
        model.__table__
        for model in (
            m.User,
            m.Organization,
            m.SettingRole,
            m.ScimGroup,
            m.ScimGroupMember,
            m.GroupDirectoryAudit,
            m.Project,
            m.ProjectRole,
            m.ProjectAssignedUser,
            m.ProjectInvitation,
            m.ProjectUserRelationship,
            m.UserEmail,
        )
    }
    pending = list(required)
    while pending:
        for foreign in pending.pop().foreign_keys:
            if foreign.column.table not in required:
                required.add(foreign.column.table)
                pending.append(foreign.column.table)
    m.User.metadata.create_all(engine, tables=list(required))
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    monkeypatch.setattr(type(Env), "FULL_ADMIN_ACCESS_EMAILS", property(lambda _: []))
    effects = []
    for cls, name in (
        (ProjectPublisher, "assigned_to_users"),
        (ProjectInvitationPublisher, "accepted"),
        (ProjectActivityTask, "project_invited_user_accepted"),
    ):
        monkeypatch.setattr(cls, name, lambda *args: effects.append(args))
    with DbSession.atomic() as db:
        users = []
        for i in range(6):
            user = m.User(
                firstname=f"Member{i}",
                lastname="Test",
                email=f"member{i}@example.invalid",
                password="test",
                is_admin=i == 0,
                activated_at=None if i == 3 else SafeDateTime.now(),
            )
            db.insert(user)
            users.append(user)
        org = m.Organization(name="Native", slug="native", owner_user_id=users[0].id)
        db.insert(org)
        role = m.SettingRole(user_id=users[0].id, actions=["group_read"])
        db.insert(role)
        project = m.Project(title="Native", owner_id=users[0].id, organization_id=org.id)
        db.insert(project)
        group = m.ScimGroup(source="local", display_name="Native group")
        db.insert(group)
        for user in users[1:5]:
            db.insert(m.ScimGroupMember(group_id=group.id, user_id=user.id))
        for user in (users[0], users[2], users[5]):
            db.insert(m.ProjectAssignedUser(project_id=project.id, user_id=user.id))
        db.insert(m.ProjectRole(project_id=project.id, user_id=users[2].id, actions=["read"]))
        for email in (users[4].email, "unrelated@example.invalid"):
            db.insert(m.ProjectInvitation(project_id=project.id, email=email, token=uuid4().hex))
    service = DomainService()

    def call(**kwargs):
        return service.project.group_invitation(users[0].id, project.get_uid(), group.get_uid(), **kwargs)

    yield engine, service, call, users, project, group, org, effects
    service.close()
    m.User.metadata.drop_all(engine, tables=list(required))
    engine.dispose()


def apply(call, preview, request_id=None):
    return call(
        snapshot=preview["snapshot"], confirmed_count=preview["target_count"], request_id=request_id or str(uuid4())
    )


def test_native_classification_additive_atomic_idempotent_and_private_audit(native):
    engine, service, call, users, project, group, org, effects = native
    preview = call()
    assert preview["counts"] == {"new": 1, "existing": 1, "inactive": 1, "pending": 1}
    assert preview["target_count"] == 1 and effects == []
    request_id = str(uuid4())
    result = apply(call, preview, request_id)
    assert result["changed_count"] == 1 and not result["replayed"] and len(effects) == 3
    assert apply(call, preview, request_id)["replayed"] and len(effects) == 3
    with engine.connect() as db:
        assert set(db.execute(select(m.ProjectAssignedUser.user_id)).scalars()) == {
            u.id for u in (users[0], users[1], users[2], users[5])
        }
        assert len(db.execute(select(m.ProjectInvitation)).all()) == 2
        roles = db.execute(select(m.ProjectRole.user_id, m.ProjectRole.actions)).all()
        assert dict(roles)[users[2].id] == ["read"]
        assert set(dict(roles)[users[1].id]) == {"read", "card_write", "card_update"}
        assert len(db.execute(select(m.GroupDirectoryAudit)).all()) == 1
    history = service.group_directory.history(users[0].id, group.get_uid())
    assert history["records"][0]["changes"] == {"target_count": 1}
    assert request_id not in str(history) and preview["snapshot"] not in str(history)
    with engine.begin() as db:
        db.execute(update(m.SettingRole).values(actions=[]))
    with pytest.raises(GroupDirectoryDenied):
        apply(call, preview, request_id)


@pytest.mark.parametrize("change", ["group", "board", "membership", "account", "permission", "tenant"])
def test_changed_snapshot_or_acl_rejects_without_invitation_mutations(native, change):
    engine, service, call, users, project, group, org, effects = native
    preview = call()
    with engine.begin() as db:
        if change == "group":
            db.execute(update(m.ScimGroup).values(revision=2))
        elif change == "board":
            db.execute(update(m.Project).values(title="Changed"))
        elif change == "membership":
            db.execute(m.ProjectAssignedUser.__table__.delete().where(m.ProjectAssignedUser.user_id == users[5].id))
        elif change == "account":
            db.execute(update(m.User).where(m.User.id == users[1].id).values(activated_at=None))
        elif change == "permission":
            db.execute(update(m.SettingRole).values(actions=[]))
        elif change == "tenant":
            db.execute(update(m.Organization).values(is_active=False))
    with pytest.raises((GroupDirectoryConflict, GroupDirectoryDenied)):
        apply(call, preview)
    assert effects == []
    with engine.connect() as db:
        assert len(db.execute(select(m.GroupDirectoryAudit)).all()) == 0


def test_actual_partial_native_mutation_rolls_back_and_drops_callbacks(native, monkeypatch):
    engine, service, call, users, project, group, org, effects = native
    preview = call()
    real_insert = DbSession.insert

    def fail_receipt(db, obj):
        if isinstance(obj, m.GroupDirectoryAudit):
            raise RuntimeError("controlled receipt failure")
        return real_insert(db, obj)

    monkeypatch.setattr(DbSession, "insert", fail_receipt)
    with pytest.raises(RuntimeError):
        apply(call, preview)
    assert effects == []
    with engine.connect() as db:
        assert users[1].id not in set(db.execute(select(m.ProjectAssignedUser.user_id)).scalars())
        assert users[1].id not in set(db.execute(select(m.ProjectRole.user_id)).scalars())
        assert len(db.execute(select(m.ProjectInvitation)).all()) == 2


def test_duplicate_concurrent_request_applies_once_and_payload_collision_rejects(native):
    engine, service, call, users, project, group, org, effects = native
    preview, request_id = call(), str(uuid4())
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: apply(call, preview, request_id), range(2)))
    assert sorted(r["replayed"] for r in results) == [False, True]
    assert len(effects) == 3
    with pytest.raises(GroupDirectoryConflict):
        call(snapshot=preview["snapshot"], confirmed_count=9, request_id=request_id)


def test_ordinary_inviter_cannot_read_canonical_group_and_tenant_mapping_required(native):
    engine, service, call, users, project, group, org, effects = native
    with pytest.raises(GroupDirectoryDenied):
        service.project.group_invitation(users[2].id, project.get_uid(), group.get_uid())
    with engine.begin() as db:
        db.execute(update(m.Project).values(organization_id=None))
    with pytest.raises(GroupDirectoryDenied):
        call()


def test_native_http_authenticated_preview_explicit_confirmation_and_apply(native, monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
    from langboard.middlewares.RoleMiddleware import RoleMiddleware
    from langboard.routes.board import BoardGroupInvitationApi  # noqa: F401
    from langboard_shared.core.routing import AppRouter
    from langboard_shared.core.security import AuthSecurity

    engine, service, call, users, project, group, org, effects = native
    for model in (m.ApiKeyRole, m.McpRole):
        model.__table__.create(engine)
    monkeypatch.setattr(type(Env), "CACHE_DIR", property(lambda _: tmp_path / "cache"))
    monkeypatch.setattr(type(Env), "JWT_SECRET_KEY", property(lambda _: "group-http-key-32-bytes-minimum-value"))
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(RoleMiddleware, routes=AppRouter.api.routes)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    access, refresh = AuthSecurity.authenticate(users[0].id)
    headers = {"Authorization": f"Bearer {access}"}
    url = f"/board/{project.get_uid()}/members/"
    try:
        with TestClient(app) as client:
            assert client.get(url + "group-preview", params={"group_uid": group.get_uid()}).status_code == 401
            client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
            response = client.get(url + "group-preview", params={"group_uid": group.get_uid()}, headers=headers)
            assert response.status_code == 200, response.text
            preview = response.json()
            assert response.headers["cache-control"] == "no-store"
            body = {
                "group_uid": group.get_uid(),
                "snapshot": preview["snapshot"],
                "confirmed_count": 1,
                "request_id": str(uuid4()),
                "confirmed": False,
            }
            assert client.post(url + "group-add", headers=headers, json=body).status_code == 400
            body["confirmed"] = True
            response = client.post(url + "group-add", headers=headers, json=body)
            assert response.status_code == 200, response.text
            assert response.json()["changed_count"] == 1
            assert client.post(url + "group-add", headers=headers, json=body).json()["replayed"]
            with engine.begin() as db:
                db.execute(update(m.SettingRole).values(actions=[]))
            assert client.post(url + "group-add", headers=headers, json=body).status_code == 403
    finally:
        for model in (m.ApiKeyRole, m.McpRole):
            model.__table__.drop(engine)
