"""Native HTTP authentication and action gates for installation group reads."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
from langboard.middlewares.RoleMiddleware import RoleMiddleware
from langboard.routes.settings import GroupDirectoryApi  # noqa: F401
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.routing import AppRouter
from langboard_shared.core.security import AuthSecurity
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import ApiKeyRole, McpRole, ScimGroup, ScimGroupMember, SettingRole, User
from langboard_shared.Env import Env
from sqlalchemy import create_engine, update


@pytest.fixture
def directory_http(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'directory-http.sqlite'}")
    try:
        for model in (User, SettingRole, ApiKeyRole, McpRole, ScimGroup, ScimGroupMember):
            model.__table__.create(engine)
        monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
        monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
        monkeypatch.setattr(type(Env), "CACHE_DIR", property(lambda _: tmp_path / "cache"))
        monkeypatch.setattr(type(Env), "FULL_ADMIN_ACCESS_EMAILS", property(lambda _: []))
        monkeypatch.setattr(type(Env), "JWT_SECRET_KEY", property(lambda _: "directory-http-key-32-bytes-minimum"))
        with DbSession.atomic() as db:
            actor = User(
                firstname="Directory",
                lastname="Reader",
                email="directory@example.invalid",
                password="fixture",
                is_admin=True,
                activated_at=SafeDateTime.now(),
            )
            db.insert(actor)
            role = SettingRole(user_id=actor.id, actions=["group_read"])
            db.insert(role)
            group = ScimGroup(source="local", issuer="", display_name="HTTP group")
            db.insert(group)
            db.insert(ScimGroupMember(group_id=group.id, user_id=actor.id))
        app = FastAPI()
        app.include_router(AppRouter.api)
        app.add_middleware(RoleMiddleware, routes=AppRouter.api.routes)
        app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
        access, refresh = AuthSecurity.authenticate(actor.id)
        with TestClient(app) as client:
            yield client, engine, actor, role, group, {"Authorization": f"Bearer {access}"}, refresh
    finally:
        engine.dispose()


def test_native_directory_auth_role_and_membership_do_not_grant_rights(directory_http):
    client, engine, actor, role, group, headers, refresh = directory_http
    endpoints = ("/settings/groups", f"/settings/groups/{group.get_uid()}/members")
    for endpoint in endpoints:
        assert client.get(endpoint).status_code == 401
        assert client.get(endpoint, headers=headers).status_code == 401
    client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
    for endpoint in endpoints:
        response = client.get(endpoint, headers=headers, params={"limit": 1})
        assert response.status_code == 200, response.text
        assert response.json()["total_records"] == 1
    assert client.get(endpoints[1], headers=headers).json()["records"][0]["uid"] == actor.get_uid()
    with engine.begin() as db:
        db.execute(update(SettingRole).where(SettingRole.id == role.id).values(actions=["user_read"]))
    for endpoint in endpoints:
        assert client.get(endpoint, headers=headers).status_code == 403
    with engine.begin() as db:
        db.execute(update(SettingRole).where(SettingRole.id == role.id).values(actions=["group_read"]))
        db.execute(update(User).where(User.id == actor.id).values(is_admin=False))
    # The actor is an ordinary directory group member with the action; admin gate still applies.
    for endpoint in endpoints:
        assert client.get(endpoint, headers=headers).status_code == 403
    with engine.begin() as db:
        db.execute(update(User).where(User.id == actor.id).values(is_admin=True))
        db.execute(update(SettingRole).where(SettingRole.id == role.id).values(actions=["*"]))
    for endpoint in endpoints:
        assert client.get(endpoint, headers=headers).status_code == 200
    with engine.begin() as db:
        db.execute(update(SettingRole).where(SettingRole.id == role.id).values(actions=[]))
    # Preserve the repository's explicit full administrator override.
    with pytest.MonkeyPatch.context() as override:
        override.setattr(type(Env), "FULL_ADMIN_ACCESS_EMAILS", property(lambda _: [actor.email]))
        for endpoint in endpoints:
            assert client.get(endpoint, headers=headers).status_code == 200
    with engine.begin() as db:
        db.execute(update(SettingRole).where(SettingRole.id == role.id).values(actions=["group_read"]))
        db.execute(update(User).where(User.id == actor.id).values(activated_at=None))
    for endpoint in endpoints:
        assert client.get(endpoint, headers=headers).status_code == 401


def test_native_directory_http_bounds_unknown_uid_and_no_crud(directory_http):
    client, _, _, _, group, headers, refresh = directory_http
    client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
    for endpoint in ("/settings/groups", f"/settings/groups/{group.get_uid()}/members"):
        for params in ({"page": 0}, {"page": 1000001}, {"limit": 0}, {"limit": 101}, {"query": "x" * 201}):
            assert client.get(endpoint, headers=headers, params=params).status_code == 400
        assert client.post(endpoint, headers=headers, json={"name": "unsafe"}).status_code == 405
        assert client.put(endpoint, headers=headers, json={"name": "unsafe"}).status_code == 405
        assert client.delete(endpoint, headers=headers).status_code == 405
    assert client.get("/settings/groups/00000000000/members", headers=headers).status_code == 404
    for uid in ("invalid", "!!!!!!!!!!!", "abcdefghij_", "%25%25%25%25%25%25%25%25%25%25%25"):
        assert client.get(f"/settings/groups/{uid}/members", headers=headers).status_code == 400
