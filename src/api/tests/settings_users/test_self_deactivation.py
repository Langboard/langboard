"""Reject a forbidden account-state request before any part of the update is persisted."""

import json
import os
from http.cookies import SimpleCookie
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jwt import encode as jwt_encode
from langboard.middlewares.ApiAuthMiddleware import ApiAuthMiddleware
from langboard.middlewares.RoleMiddleware import RoleMiddleware
from langboard.routes.auth import AuthApi
from langboard.routes.auth.SocketAuthApi import authenticate_socket
from langboard.routes.settings import UserSettingsApi  # noqa: F401
from langboard_shared.core.caching import Cache
from langboard_shared.core.caching.InMemoryCache import InMemoryCache
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.routing import ApiException, AppRouter
from langboard_shared.core.security import AuthSecurity, KeyVault
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import (
    ApiKeySetting,
    NativeMcpBoardConsent,
    Organization,
    SettingRole,
    User,
    UserProfile,
)
from langboard_shared.domain.models.ApiKeySetting import ApiKeyProvider
from langboard_shared.Env import Env
from langboard_shared.publishers import AppSettingPublisher, UserPublisher
from langboard_shared.security import Auth
from sqlalchemy import create_engine, select
from starlette.datastructures import Headers
from starlette.requests import Request


@pytest.fixture(params=["sqlite", "postgresql"])
def self_deactivation_engine(request, tmp_path):
    database_url = os.environ.get("LANGBOARD_FILE_TEST_DATABASE_URL")
    if request.param == "postgresql" and not database_url:
        pytest.skip("LANGBOARD_FILE_TEST_DATABASE_URL unavailable")
    engine = create_engine(
        database_url if request.param == "postgresql" else f"sqlite:///{tmp_path / 'self-deactivation.sqlite'}"
    )
    tables = {
        model.__table__
        for model in (User, UserProfile, SettingRole, Organization, ApiKeySetting, NativeMcpBoardConsent)
    }
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
    monkeypatch.setattr(KeyVault, "get_key", lambda _: "fixture-secret")
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
        keys = [
            ApiKeySetting(
                user_id=owner.id,
                name="fixture",
                value="fixture-only",
                provider=ApiKeyProvider.OpenBao,
                activated_at=SafeDateTime.now(),
                ip_whitelist=["127.0.0.1"],
            )
            for owner in (actor, target)
        ]
        db.insert_all(keys)
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
            target_access, target_refresh = AuthSecurity.authenticate(target.id)
            legacy_payload = AuthSecurity.decode_access_token(target_access)
            legacy_payload.pop("auth_version", None)
            legacy_access = jwt_encode(legacy_payload, Env.JWT_SECRET_KEY, algorithm=Env.JWT_ALGORITHM)
            old_internal = AuthSecurity.create_bot_one_time_token(target.id)
            key_headers = Headers({AuthSecurity.API_KEY_HEADER: f"sk-{keys[1].get_uid()}.fixture-secret"})
            assert isinstance(Auth.validate_user_by_api_key(key_headers, "127.0.0.1"), tuple)
            assert isinstance(Auth.get_user_by_token(legacy_access), User)
            other = client.put(f"/settings/users/{target.get_uid()}", headers=headers, json={"activate": False})
            assert other.status_code == 200, other.text
            with engine.connect() as db:
                assert db.execute(select(User.activated_at).where(User.id == target.id)).scalar_one() is None
            UserPublisher.deactivated.assert_called_once()
            assert not isinstance(Auth.get_user_by_token(target_access), User)
            restored = client.put(f"/settings/users/{target.get_uid()}", headers=headers, json={"activate": True})
            assert restored.status_code == 200, restored.text
            assert Auth.validate_user_by_api_key(key_headers, "127.0.0.1") == 401, (
                "Reactivation must not revive API keys revoked with the account"
            )
            with engine.connect() as db:
                assert db.execute(select(ApiKeySetting.activated_at).where(ApiKeySetting.id == keys[0].id)).scalar_one()
                assert (
                    db.execute(select(ApiKeySetting.activated_at).where(ApiKeySetting.id == keys[1].id)).scalar_one()
                    is None
                )
            assert not isinstance(Auth.get_user_by_token(target_access), User), (
                "Reactivation must not revive old access tokens"
            )
            assert not isinstance(Auth.get_user_by_token(AuthSecurity.refresh(target_refresh)), User), (
                "Reactivation must not revive old refresh cookies"
            )
            assert not isinstance(Auth.get_user_by_token(legacy_access), User)
            with pytest.raises(ApiException.Unauthorized_401):
                authenticate_socket(
                    Request({"type": "http", "headers": [(b"authorization", f"Bearer {target_access}".encode())]})
                )
            assert Auth.validate_user_by_api_token(Headers({AuthSecurity.API_TOKEN_HEADER: old_internal})) == 401
            current = Auth.get_user_by_id(target.id)
            assert isinstance(current, User)
            assert current.auth_version == 1
            signed_in = AuthApi.sign_in(
                Request({"type": "http", "headers": []}),
                SimpleNamespace(email_token="fixture-email", sign_token="fixture-sign", password="original-password"),
                service=SimpleNamespace(
                    user=SimpleNamespace(get_by_token=lambda *_: (current, None), log_sign_in=Mock())
                ),
            )
            fresh_access = json.loads(signed_in.body)["access_token"]
            cookies = SimpleCookie()
            cookies.load(signed_in.headers["set-cookie"])
            fresh_refresh = cookies[Env.REFRESH_TOKEN_NAME].value
            assert isinstance(Auth.get_user_by_token(fresh_access), User)
            assert (
                authenticate_socket(
                    Request({"type": "http", "headers": [(b"authorization", f"Bearer {fresh_access}".encode())]})
                ).status_code
                == 200
            )
            assert isinstance(Auth.get_user_by_token(AuthSecurity.refresh(fresh_refresh)), User)
            assert AuthSecurity.compare_tokens(target_access, fresh_refresh) is False
            fresh_internal = AuthSecurity.create_bot_one_time_token(target.id, auth_version=current.auth_version)
            assert isinstance(
                Auth.validate_user_by_api_token(Headers({AuthSecurity.API_TOKEN_HEADER: fresh_internal})), User
            )
            assert (
                client.put(f"/settings/users/{target.get_uid()}", headers=headers, json={"activate": False}).status_code
                == 200
            )
            assert (
                client.put(f"/settings/users/{target.get_uid()}", headers=headers, json={"activate": False}).status_code
                == 200
            )
            assert (
                client.put(f"/settings/users/{target.get_uid()}", headers=headers, json={"activate": True}).status_code
                == 200
            )
            assert Auth.get_user_by_id(target.id).auth_version == 2
            assert not isinstance(Auth.get_user_by_token(fresh_access), User)
    finally:
        engine.dispose()
