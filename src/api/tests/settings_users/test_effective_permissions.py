"""Native JWT, middleware and DB proof for the bounded board read inspection."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
from langboard.middlewares.RoleMiddleware import RoleMiddleware
from langboard.routes.settings import UserSettingsApi  # noqa: F401
from langboard_shared.core.caching import Cache
from langboard_shared.core.caching.InMemoryCache import InMemoryCache
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.routing import AppRouter
from langboard_shared.core.security import AuthSecurity
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import Project, ProjectAssignedUser, ProjectRole, SettingRole, User
from langboard_shared.Env import Env
from sqlalchemy import delete, update
from test_business_activity import business_activity_engine  # noqa: F401


def test_effective_permissions_native_http(tmp_path, monkeypatch, business_activity_engine):  # noqa: F811
    engine = business_activity_engine
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    monkeypatch.setattr(Cache, "_cache", InMemoryCache())
    for name, value in {
        "CACHE_DIR": tmp_path / "cache",
        "FULL_ADMIN_ACCESS_EMAILS": [],
        "JWT_SECRET_KEY": "effective-permissions-native-secret-32",
        "CARD_INTERNAL_ACCESS_MODE": "project_members",
    }.items():
        monkeypatch.setattr(type(Env), name, property(lambda _, v=value: v))
    now = SafeDateTime.now()
    with DbSession.atomic() as db:
        admin, target, denied, regular = [
            User(
                email=f"{name}@example.invalid",
                password="test",
                firstname=name,
                lastname="Test",
                is_admin=name in {"admin", "denied"},
                activated_at=now,
            )
            for name in ("admin", "target", "denied", "regular")
        ]
        db.insert_all([admin, target, denied, regular])
        db.insert_all(
            [
                SettingRole(user_id=admin.id, actions=["user_read"]),
                SettingRole(user_id=regular.id, actions=["user_read"]),
            ]
        )
        project = Project(owner_id=regular.id, title="Visible")
        unrelated = Project(owner_id=target.id, title="Unrelated tenant resource")
        db.insert_all([project, unrelated])
        db.insert_all([ProjectAssignedUser(project_id=project.id, user_id=u.id) for u in (admin, target)])
        role = ProjectRole(project_id=project.id, user_id=target.id, actions=["read"])
        db.insert(role)
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(RoleMiddleware, routes=AppRouter.api.routes)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    url = f"/settings/users/{target.get_uid()}/effective-permissions"
    with TestClient(app) as client:

        def login(user):
            access, refresh = AuthSecurity.authenticate(user.id)
            client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
            return {"Authorization": f"Bearer {access}"}

        params = {"project_uid": project.get_uid()}
        assert client.get(url, params=params).status_code == 401
        assert client.get(url, params=params, headers=login(denied)).status_code == 403
        assert client.get(url, params=params, headers=login(regular)).status_code == 403
        headers = login(admin)

        def inspect():
            response = client.get(url, params=params, headers=headers)
            assert response.status_code == 200, response.text
            assert response.headers["cache-control"] == "no-store"
            assert "password" not in response.text and "email" not in response.text
            return response.json()["inspection"]

        inspection = inspect()
        assert inspection["allowed"] is True and inspection["action"] == "read"
        assert inspection["sources"] == [{"type": "direct_project_role", "uid": role.get_uid()}]
        assert client.get(url, params={"project_uid": unrelated.get_uid()}, headers=headers).status_code == 404
        assert client.get(url, params={"project_uid": "00000000000"}, headers=headers).status_code == 404
        # Current role revocation and membership revocation both affect the next request.
        with engine.begin() as db:
            db.execute(update(ProjectRole).where(ProjectRole.id == role.id).values(actions=[]))
        assert inspect()["allowed"] is False and inspect()["sources"] == []
        with engine.begin() as db:
            db.execute(update(ProjectRole).where(ProjectRole.id == role.id).values(actions=["*"]))
        assert inspect()["allowed"] is True
        with engine.begin() as db:
            db.execute(delete(ProjectAssignedUser).where(ProjectAssignedUser.user_id == target.id))
        assert inspect()["allowed"] is False
        with engine.begin() as db:
            db.execute(update(Project).where(Project.id == project.id).values(owner_id=target.id))
        assert {"type": "project_owner"} in inspect()["sources"]
        with engine.begin() as db:
            db.execute(update(User).where(User.id == target.id).values(activated_at=None))
        assert inspect()["allowed"] is False
        with engine.begin() as db:
            db.execute(update(User).where(User.id == target.id).values(activated_at=now, is_admin=True))
            db.execute(update(Project).where(Project.id == project.id).values(owner_id=regular.id))
        with DbSession.atomic() as db:
            db.insert(ProjectAssignedUser(project_id=project.id, user_id=target.id))
        assert {"type": "administrator_project_membership"} in inspect()["sources"]
        with engine.begin() as db:
            db.execute(update(User).where(User.id == target.id).values(deleted_at=now))
        assert client.get(url, params=params, headers=headers).status_code == 404
        with engine.begin() as db:
            db.execute(update(User).where(User.id == target.id).values(deleted_at=None))
            db.execute(update(Project).where(Project.id == project.id).values(deleted_at=now))
        assert client.get(url, params=params, headers=headers).status_code == 404
        with engine.begin() as db:
            db.execute(update(Project).where(Project.id == project.id).values(deleted_at=None))
        assert inspect()["allowed"] is True
        with engine.begin() as db:
            db.execute(delete(ProjectAssignedUser).where(ProjectAssignedUser.user_id == admin.id))
        assert client.get(url, params=params, headers=headers).status_code == 404
        with engine.begin() as db:
            db.execute(delete(SettingRole).where(SettingRole.user_id == admin.id))
        assert client.get(url, params=params, headers=headers).status_code == 403
