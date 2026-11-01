"""Account target resolution must not authorize mutations from replica state."""

from shutil import copyfile
from unittest.mock import Mock
import pytest
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
from langboard_shared.domain.models import ApiKeyRole, McpRole, SettingRole, User
from langboard_shared.Env import Env
from langboard_shared.publishers import UserPublisher
from sqlalchemy import create_engine, select, update


@pytest.mark.parametrize(
    "role_path,model", [("setting-role", SettingRole), ("api-key-role", ApiKeyRole), ("mcp-role", McpRole)]
)
@pytest.mark.parametrize("existing_role", [True, False])
def test_deleted_primary_target_rejects_stale_replica_role_mutation(
    tmp_path, monkeypatch, role_path, model, existing_role
):
    primary_path, replica_path = tmp_path / "primary.sqlite", tmp_path / "replica.sqlite"
    primary = create_engine(f"sqlite:///{primary_path}")
    replica = create_engine(f"sqlite:///{replica_path}")
    for table in (User, SettingRole, ApiKeyRole, McpRole):
        table.__table__.create(primary)
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: primary)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: replica)
    monkeypatch.setattr(Cache, "_cache", InMemoryCache())
    for name, value in {
        "CACHE_DIR": tmp_path / "cache",
        "FULL_ADMIN_ACCESS_EMAILS": [],
        "JWT_SECRET_KEY": "deleted-replica-role-native-secret-32",
    }.items():
        monkeypatch.setattr(type(Env), name, property(lambda _, v=value: v))
    for name in ("setting_roles_updated", "api_key_roles_updated", "mcp_roles_updated"):
        monkeypatch.setattr(UserPublisher, name, Mock())
    now = SafeDateTime.now()
    with DbSession.atomic() as db:
        actor = User(
            email="admin@example.invalid",
            password="test",
            firstname="Admin",
            lastname="Test",
            is_admin=True,
            activated_at=now,
        )
        target = User(
            email="target@example.invalid", password="test", firstname="Target", lastname="Test", activated_at=now
        )
        db.insert_all([actor, target])
        db.insert(SettingRole(user_id=actor.id, actions=["user_read", "user_update"]))
        if existing_role:
            db.insert(model(user_id=target.id, actions=[]))
    copyfile(primary_path, replica_path)
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(RoleMiddleware, routes=AppRouter.api.routes)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    access, refresh = AuthSecurity.authenticate(actor.id)
    headers = {"Authorization": f"Bearer {access}"}
    url = f"/settings/users/{target.get_uid()}/{role_path}"
    try:
        with TestClient(app) as client:
            client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
            normal = client.put(url, headers=headers, json={"actions": []})
            assert normal.status_code == 200, normal.text
            # Inactive is a valid account lifecycle state, distinct from deletion.
            with primary.begin() as db:
                db.execute(update(User).where(User.id == target.id).values(activated_at=None))
            inactive = client.put(url, headers=headers, json={"actions": []})
            assert inactive.status_code == 200, inactive.text
            with primary.connect() as db:
                # When first created by this request, the replica still lacks the grant.
                assert len(db.execute(select(model.id).where(model.user_id == target.id)).all()) == 1
            with primary.begin() as db:
                db.execute(update(User).where(User.id == target.id).values(deleted_at=now))
            with replica.connect() as db:
                assert db.execute(select(User.deleted_at).where(User.id == target.id)).scalar_one() is None
            rejected = client.put(url, headers=headers, json={"actions": [model.get_all_actions()[0].value]})
            with primary.connect() as db:
                assert db.execute(select(model.actions).where(model.user_id == target.id)).scalar_one() == []
            assert rejected.status_code == 404, rejected.text
            assert rejected.json()["code"] == "NF1004"
    finally:
        primary.dispose()
        replica.dispose()
