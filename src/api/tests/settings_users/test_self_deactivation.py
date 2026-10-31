"""Reject a forbidden account-state request before any part of the update is persisted."""

import os
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
from langboard_shared.domain.models import Organization, SettingRole, User, UserProfile
from langboard_shared.Env import Env
from langboard_shared.publishers import AppSettingPublisher, UserPublisher
from sqlalchemy import create_engine, select


@pytest.fixture(params=["sqlite", "postgresql"])
def self_deactivation_engine(request, tmp_path):
    database_url = os.environ.get("LANGBOARD_FILE_TEST_DATABASE_URL")
    if request.param == "postgresql" and not database_url:
        pytest.skip("LANGBOARD_FILE_TEST_DATABASE_URL unavailable")
    engine = create_engine(
        database_url if request.param == "postgresql" else f"sqlite:///{tmp_path / 'self-deactivation.sqlite'}"
    )
    tables = {model.__table__ for model in (User, UserProfile, SettingRole, Organization)}
    pending = list(tables)
    while pending:
        for foreign_key in pending.pop().foreign_keys:
            referenced = foreign_key.column.table
            if referenced not in tables:
                tables.add(referenced)
                pending.append(referenced)
    metadata = User.__table__.metadata
    metadata.create_all(engine, tables=list(tables))
    try:
        yield engine
    finally:
        metadata.drop_all(engine, tables=list(tables))
        engine.dispose()


def test_self_deactivation_rejects_entire_update_and_preserves_normal_updates(
    tmp_path, monkeypatch, self_deactivation_engine
):
    engine = self_deactivation_engine
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: engine)
    monkeypatch.setattr(Cache, "_cache", InMemoryCache())
    for name, value in {
        "CACHE_DIR": tmp_path / "cache",
        "FULL_ADMIN_ACCESS_EMAILS": [],
        "JWT_SECRET_KEY": "self-deactivation-test-secret-32-bytes",
    }.items():
        monkeypatch.setattr(type(Env), name, property(lambda _, v=value: v))
    for publisher, names in (
        (UserPublisher, ("updated", "deactivated", "deleted")),
        (AppSettingPublisher, ("user_updated",)),
    ):
        for name in names:
            monkeypatch.setattr(publisher, name, Mock())
    with DbSession.atomic() as db:
        actor = User(
            email="admin@example.invalid",
            password="original-password",
            firstname="Original",
            lastname="Admin",
            activated_at=SafeDateTime.now(),
            is_admin=True,
        )
        target = User(
            email="target@example.invalid",
            password="original-password",
            firstname="Target",
            lastname="User",
            activated_at=SafeDateTime.now(),
        )
        db.insert_all([actor, target])
        db.insert(SettingRole(user_id=actor.id, actions=["user_read", "user_update"]))
    app = FastAPI()
    app.include_router(AppRouter.api)
    app.add_middleware(RoleMiddleware, routes=AppRouter.api.routes)
    app.add_middleware(ApiAuthMiddleware, routes=AppRouter.api.routes)
    access, refresh = AuthSecurity.authenticate(actor.id)
    headers = {"Authorization": f"Bearer {access}"}
    url = f"/settings/users/{actor.get_uid()}"
    try:
        with engine.connect() as db:
            original = db.execute(
                select(User.firstname, User.password, User.activated_at, User.is_admin).where(User.id == actor.id)
            ).one()
        with TestClient(app) as client:
            client.cookies.set(Env.REFRESH_TOKEN_NAME, refresh)
            for body in (
                {"activate": False},
                {"activate": False, "firstname": "Partially changed", "password": "replacement-password"},
                {"is_admin": False},
            ):
                rejected = client.put(url, headers=headers, json=body)
                assert rejected.status_code == 409, rejected.text
                assert rejected.json()["code"] == "OP1005"
                with engine.connect() as db:
                    assert (
                        db.execute(
                            select(User.firstname, User.password, User.activated_at, User.is_admin).where(
                                User.id == actor.id
                            )
                        ).one()
                        == original
                    )
                UserPublisher.updated.assert_not_called()
                UserPublisher.deactivated.assert_not_called()
                AppSettingPublisher.user_updated.assert_not_called()
            normal = client.put(url, headers=headers, json={"firstname": "Normal", "activate": True})
            assert normal.status_code == 200, normal.text
            with engine.connect() as db:
                firstname, activated_at = db.execute(
                    select(User.firstname, User.activated_at).where(User.id == actor.id)
                ).one()
                assert firstname == "Normal"
                assert activated_at == original.activated_at
            other = client.put(f"/settings/users/{target.get_uid()}", headers=headers, json={"activate": False})
            assert other.status_code == 200, other.text
            with engine.connect() as db:
                assert db.execute(select(User.activated_at).where(User.id == target.id)).scalar_one() is None
            UserPublisher.deactivated.assert_called_once()
    finally:
        engine.dispose()
