"""Real native HTTP + ephemeral DB coverage for optional membership filters."""

from datetime import timedelta
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
from langboard.middlewares.RoleMiddleware import RoleMiddleware
from langboard.routes.settings import GroupDirectoryApi, UserSettingsApi  # noqa: F401
from langboard_shared.core.caching import Cache
from langboard_shared.core.caching.InMemoryCache import InMemoryCache
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.routing import AppRouter
from langboard_shared.core.security import AuthSecurity
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import (
    ApiKeyRole,
    McpRole,
    Project,
    ProjectActivity,
    ProjectAssignedUser,
    ProjectRole,
    ProjectWikiActivity,
    ScimGroup,
    ScimGroupMember,
    SettingRole,
    User,
    UserIdentityLink,
    UserProfile,
    UserSignInHistory,
)
from langboard_shared.Env import Env
from sqlalchemy import create_engine, delete, update


@pytest.fixture(params=["sqlite", "postgresql"])
def membership_db(request, tmp_path, monkeypatch):
    import os

    url = os.environ.get("LANGBOARD_FILE_TEST_DATABASE_URL")
    if request.param == "postgresql" and not url:
        pytest.skip("Disposable PostgreSQL URL unavailable")
    engine = create_engine(url if request.param == "postgresql" else f"sqlite:///{tmp_path / 'membership.sqlite'}")
    models = [
        User,
        SettingRole,
        ScimGroup,
        ScimGroupMember,
        Project,
        ProjectAssignedUser,
        ProjectRole,
        UserProfile,
        UserSignInHistory,
        UserIdentityLink,
        ApiKeyRole,
        McpRole,
        ProjectActivity,
        ProjectWikiActivity,
    ]
    tables = {model.__table__ for model in models}
    if request.param == "postgresql":
        pending = list(tables)
        while pending:
            for fk in pending.pop().foreign_keys:
                if fk.column.table not in tables:
                    tables.add(fk.column.table)
                    pending.append(fk.column.table)
    User.metadata.create_all(engine, tables=list(tables))
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    monkeypatch.setattr(Cache, "_cache", InMemoryCache())
    for name, value in {
        "CACHE_DIR": tmp_path / "cache",
        "FULL_ADMIN_ACCESS_EMAILS": [],
        "JWT_SECRET_KEY": "membership-filter-test-only-secret",
        "CARD_INTERNAL_ACCESS_MODE": "project_members",
    }.items():
        monkeypatch.setattr(type(Env), name, property(lambda _, v=value: v))
    yield engine
    User.metadata.drop_all(engine, tables=list(tables))
    engine.dispose()


def test_membership_filter_native_http(membership_db, monkeypatch, tmp_path, request):
    now = SafeDateTime.now()
    with DbSession.atomic() as db:
        users = [
            User(
                email=f"person{i}@example.invalid",
                password="test",
                firstname="Person",
                lastname=str(i),
                activated_at=now if i != 2 else None,
                is_admin=i == 0,
            )
            for i in range(5)
        ]
        db.insert_all(users)
        admin, owner, inactive, outside, deleted = users
        role = SettingRole(user_id=admin.id, actions=["user_read", "group_read"])
        db.insert(role)
        group = ScimGroup(display_name="Raw <group> % name", source="local")
        other_group = ScimGroup(display_name="Other", source="local")
        db.insert_all([group, other_group])
        project = Project(title="Readable board", owner_id=owner.id)
        hidden = Project(title="Hidden board", owner_id=outside.id)
        db.insert_all([project, hidden])
        db.insert_all(
            [
                ProjectAssignedUser(project_id=project.id, user_id=admin.id),
                ProjectAssignedUser(project_id=project.id, user_id=inactive.id),
                ProjectAssignedUser(project_id=project.id, user_id=inactive.id),
                ProjectAssignedUser(project_id=project.id, user_id=deleted.id),
            ]
        )
        db.insert_all([ScimGroupMember(group_id=group.id, user_id=u.id) for u in [owner, inactive, outside, deleted]])
        db.insert(ScimGroupMember(group_id=other_group.id, user_id=outside.id))
        db.exec(update(User).where(User.id == deleted.id).values(deleted_at=now))
    if membership_db.dialect.name == "sqlite":
        # Freeze a replica snapshot before revocations; filtered reads must use primary.
        import sqlite3

        replica_path = tmp_path / "stale-replica.sqlite"
        with sqlite3.connect(membership_db.url.database) as primary, sqlite3.connect(replica_path) as replica:
            primary.backup(replica)
        replica_engine = create_engine(f"sqlite:///{replica_path}")
        monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: replica_engine)
        request.addfinalizer(replica_engine.dispose)
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(RoleMiddleware, routes=AppRouter.api.routes)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    with TestClient(app) as client:
        access, refresh = AuthSecurity.authenticate(admin.id)
        client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
        headers = {"Authorization": f"Bearer {access}"}
        url = "/settings/users/page"
        assert client.get(url).status_code == 401

        def get(**params):
            result = client.get(url, headers=headers, params=params)
            assert result.status_code == 200, result.text
            data = result.json()
            assert (
                data["total_records"]
                == data["account_counts"][params.get("account_status", "total").replace("all", "total")]
            )
            return data

        assert get()["total_records"] == 4  # Existing unfiltered behavior.
        both = dict(group_uid=group.get_uid(), project_uid=project.get_uid())
        data = get(**both)
        assert {row["uid"] for row in data["records"]} == {owner.get_uid(), inactive.get_uid()}
        assert data["account_counts"] == {"total": 2, "active": 1, "inactive": 1, "no_recent_sign_in": 2}
        assert get(**both, limit=1, page=2)["total_records"] == 2
        assert len(get(**both, limit=1, page=2)["records"]) == 1
        assert get(**both, account_status="inactive")["records"][0]["uid"] == inactive.get_uid()
        assert (
            get(**both, query="person", firstname="Person", lastname="1", email="person1", admin_role="non_admin")[
                "total_records"
            ]
            == 1
        )
        assert get(**both, identity_provider="oidc")["total_records"] == 0
        assert get(**both, last_signed_in_from=(now - timedelta(days=1)).isoformat())["total_records"] == 0
        assert get(**both, last_business_activity_before=now.isoformat())["total_records"] == 0
        assert get(group_uid=other_group.get_uid(), project_uid=project.get_uid())["total_records"] == 0
        assert client.get(url, headers=headers, params={"project_uid": hidden.get_uid()}).status_code == 403
        assert client.get(url, headers=headers, params={"group_uid": "bad"}).status_code == 400
        from langboard_shared.domain.models.ProjectActivity import ProjectActivityType
        from langboard_shared.domain.models.UserIdentityLink import IdentityProvider

        with DbSession.atomic() as db:
            db.insert(
                UserIdentityLink(
                    user_id=owner.id,
                    provider=IdentityProvider.Oidc,
                    issuer="https://issuer.example.invalid",
                    external_id="owner",
                )
            )
            db.insert(UserSignInHistory(user_id=owner.id, is_success=True, signed_in_at=now))
            db.insert(
                ProjectActivity(
                    project_id=project.id,
                    user_id=owner.id,
                    activity_type=ProjectActivityType.ProjectUpdated,
                    activity_history={},
                    created_at=now,
                )
            )
        combined = get(
            **both,
            query="Person",
            email="person1",
            firstname="Person",
            lastname="1",
            account_status="active",
            identity_provider="oidc",
            admin_role="non_admin",
            exclude_user_uid=admin.get_uid(),
            last_signed_in_from=(now - timedelta(days=1)).isoformat(),
            last_signed_in_before=(now + timedelta(days=1)).isoformat(),
            last_business_activity_from=(now - timedelta(days=1)).isoformat(),
            last_business_activity_before=(now + timedelta(days=1)).isoformat(),
        )
        assert combined["total_records"] == len(combined["records"]) == 1
        assert combined["account_counts"] == {"total": 1, "active": 1, "inactive": 0, "no_recent_sign_in": 0}
        with DbSession.atomic() as db:
            db.insert_all([Project(title=f"Picker {i:02}", owner_id=admin.id) for i in range(16)])
        picker = client.get(
            "/settings/users/filter-projects", headers=headers, params={"query": "Picker", "page": 2, "limit": 15}
        )
        assert picker.status_code == 200
        assert picker.json()["total_records"] == 16
        assert [row["display_name"] for row in picker.json()["records"]] == ["Picker 15"]
        candidates = client.get("/settings/users/filter-projects", headers=headers, params={"query": "Readable"})
        assert candidates.status_code == 200, candidates.text
        assert [r["uid"] for r in candidates.json()["records"]] == [project.get_uid()]
        with DbSession.atomic() as db:
            db.exec(
                delete(ScimGroupMember).where(
                    ScimGroupMember.group_id == group.id, ScimGroupMember.user_id == inactive.id
                )
            )
        assert get(**both)["total_records"] == 1
        assert [row["uid"] for row in get(**both)["records"]] == [owner.get_uid()]
        with DbSession.atomic() as db:
            db.exec(update(SettingRole).where(SettingRole.id == role.id).values(actions=["user_read"]))
        assert client.get(url, headers=headers, params=both).status_code == 403
        assert client.get("/settings/groups", headers=headers).status_code == 403
        assert get(project_uid=project.get_uid())["total_records"] == 3
        with DbSession.atomic() as db:
            db.exec(
                delete(ProjectAssignedUser).where(
                    ProjectAssignedUser.project_id == project.id, ProjectAssignedUser.user_id == admin.id
                )
            )
        assert client.get(url, headers=headers, params={"project_uid": project.get_uid()}).status_code == 403
        with DbSession.atomic() as db:
            db.insert(ProjectAssignedUser(project_id=project.id, user_id=admin.id))

        with DbSession.atomic() as db:
            db.exec(update(SettingRole).where(SettingRole.id == role.id).values(actions=["user_read", "group_read"]))
            db.exec(delete(ScimGroupMember).where(ScimGroupMember.group_id == group.id))
            db.exec(delete(ScimGroup).where(ScimGroup.id == group.id))
        assert client.get(url, headers=headers, params=both).status_code == 404
        with DbSession.atomic() as db:
            db.exec(update(Project).where(Project.id == project.id).values(deleted_at=now))
        assert client.get(url, headers=headers, params={"project_uid": project.get_uid()}).status_code == 403
        with DbSession.atomic() as db:
            db.exec(update(SettingRole).where(SettingRole.id == role.id).values(actions=[]))
        assert client.get(url, headers=headers).status_code == 403
