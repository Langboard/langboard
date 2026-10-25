"""Native HTTP authentication and action gates for installation group reads."""

import os
from uuid import uuid4
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
from langboard_shared.domain.models import (
    ApiKeyRole,
    GroupDirectoryAudit,
    McpRole,
    Organization,
    ScimGroup,
    ScimGroupMember,
    SettingRole,
    User,
)
from langboard_shared.Env import Env
from sqlalchemy import create_engine, text, update


@pytest.fixture(params=["sqlite", "postgresql"])
def directory_http(request, tmp_path, monkeypatch):
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
        schema = "group_directory_http_" + uuid4().hex
        with admin.begin() as db:
            db.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    else:
        engine = create_engine(f"sqlite:///{tmp_path / 'directory-http.sqlite'}")
    try:
        for model in (
            User,
            SettingRole,
            ApiKeyRole,
            McpRole,
            ScimGroup,
            ScimGroupMember,
            Organization,
            GroupDirectoryAudit,
        ):
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
            db.insert(Organization(name="Canonical", slug="canonical", owner_user_id=actor.id))
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
        if admin is not None:
            with admin.begin() as db:
                db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            admin.dispose()


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


def test_native_directory_http_bounds_unknown_uid_and_denied_writes(directory_http):
    client, _, _, _, group, headers, refresh = directory_http
    client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
    for endpoint in ("/settings/groups", f"/settings/groups/{group.get_uid()}/members"):
        for params in ({"page": 0}, {"page": 1000001}, {"limit": 0}, {"limit": 101}, {"query": "x" * 201}):
            assert client.get(endpoint, headers=headers, params=params).status_code == 400

    assert client.get("/settings/groups/00000000000/members", headers=headers).status_code == 404
    for uid in ("invalid", "!!!!!!!!!!!", "abcdefghij_", "%25%25%25%25%25%25%25%25%25%25%25"):
        assert client.get(f"/settings/groups/{uid}/members", headers=headers).status_code == 400


def test_group_read_does_not_authorize_write_or_candidates(directory_http):
    client, _, _, _, group, headers, refresh = directory_http
    client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
    for method, endpoint, body in (
        ("POST", "/settings/groups", {"display_name": "Denied"}),
        ("PUT", f"/settings/groups/{group.get_uid()}", {"display_name": "Denied", "expected_revision": 1}),
        ("DELETE", f"/settings/groups/{group.get_uid()}", {"expected_revision": 1}),
        (
            "POST",
            f"/settings/groups/{group.get_uid()}/members",
            {"user_uids": [group.get_uid()], "expected_revision": 1},
        ),
        (
            "DELETE",
            f"/settings/groups/{group.get_uid()}/members",
            {"user_uids": [group.get_uid()], "expected_revision": 1},
        ),
    ):
        assert client.request(method, endpoint, headers=headers, json=body).status_code == 403
    assert client.get("/settings/groups/candidates", headers=headers).status_code == 403


def test_deleted_member_search_does_not_disclose_redacted_identity(directory_http):
    client, engine, _, _, group, headers, refresh = directory_http
    client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
    with DbSession.atomic() as db:
        deleted = User(
            firstname="ErasedFirstname",
            lastname="ErasedLastname",
            username="erased-username",
            email="erased@example.invalid",
            password="fixture",
            deleted_at=SafeDateTime.now(),
        )
        db.insert(deleted)
        db.insert(ScimGroupMember(group_id=group.id, user_id=deleted.id))
    endpoint = f"/settings/groups/{group.get_uid()}/members"
    response = client.get(endpoint, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["total_records"] == 2
    row = next(row for row in response.json()["records"] if row["uid"] == deleted.get_uid())
    assert row["firstname"] == row["lastname"] == row["username"] == ""
    for query in ("ErasedFirstname", "ErasedLastname", "erased-username", "Erased"):
        response = client.get(endpoint, headers=headers, params={"query": query})
        assert response.status_code == 200, response.text
        assert response.json()["total_records"] == 0
        assert response.json()["records"] == []
    response = client.get(endpoint, headers=headers, params={"query": "Directory"})
    assert response.json()["total_records"] == 1
    # Searching never removes the persisted membership or retained account.
    with engine.connect() as db:
        assert db.execute(text("SELECT count(*) FROM scim_group_member")).scalar_one() == 2
