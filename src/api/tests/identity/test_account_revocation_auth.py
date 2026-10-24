"""Previously issued credentials must observe current primary account state."""

from unittest.mock import Mock
import pytest
from jwt import InvalidTokenError
from langboard.routes.auth.AuthApi import refresh
from langboard_shared.core.caching import Cache
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.security import AuthSecurity, KeyVault
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import ApiKeySetting, User, UserEmail
from langboard_shared.domain.models.ApiKeySetting import ApiKeyProvider
from langboard_shared.Env import Env
from langboard_shared.infrastructure.repositories.factory.UserRepository import UserRepository
from langboard_shared.security import Auth
from sqlalchemy import create_engine, update
from starlette.datastructures import Headers
from starlette.requests import Request


@pytest.fixture
def principal(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'accounts.sqlite'}")
    for model in (User, ApiKeySetting, UserEmail):
        model.__table__.create(engine)
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: pytest.fail("Authentication used a lagging replica"))
    with DbSession.atomic() as db:
        user = User(
            firstname="Fixture",
            lastname="Principal",
            username="revocation",
            email="fixture@example.invalid",
            password="fixture-only",
            activated_at=SafeDateTime.now(),
        )
        db.insert(user)
        key = ApiKeySetting(
            user_id=user.id,
            name="fixture",
            value="fixture-ref",
            provider=ApiKeyProvider.OpenBao,
            activated_at=SafeDateTime.now(),
            ip_whitelist=["127.0.0.1"],
        )
        db.insert(key)
    monkeypatch.setattr(Cache, "get", Mock(return_value=user))
    monkeypatch.setattr(Cache, "set", Mock())
    monkeypatch.setattr(AuthSecurity, "decode_access_token", lambda _: {"sub": str(user.id), "internal": True})
    monkeypatch.setattr(KeyVault, "get_key", lambda _: "fixture-secret")
    try:
        yield engine, user, key
    finally:
        engine.dispose()


@pytest.mark.parametrize("state", ["active", "inactive", "deleted"])
@pytest.mark.parametrize("credential", ["id", "access", "headers", "internal", "api_key"])
def test_existing_credentials_recheck_primary_despite_cached_active_user(principal, state, credential):
    engine, user, key = principal
    if state != "active":
        with engine.begin() as db:
            db.execute(
                update(User.__table__)
                .where(User.__table__.c.id == user.id)
                .values(
                    activated_at=None if state == "inactive" else user.activated_at,
                    deleted_at=SafeDateTime.now() if state == "deleted" else None,
                )
            )
    if credential == "id":
        result = Auth.get_user_by_id(user.id)
    elif credential == "access":
        result = Auth.get_user_by_token("fixture-access")
    elif credential == "headers":
        result = Auth.validate({AuthSecurity.AUTHORIZATION_HEADER: "Bearer fixture-access"})
    elif credential == "internal":
        result = Auth.validate_user_by_api_token(Headers({AuthSecurity.API_TOKEN_HEADER: "fixture-access"}))
    else:
        result = Auth.validate_user_by_api_key(
            Headers({AuthSecurity.API_KEY_HEADER: f"sk-{key.get_uid()}.fixture-secret"}), "127.0.0.1"
        )
    if state == "active":
        accepted = result[0] if isinstance(result, tuple) else result
        assert isinstance(accepted, User) and accepted.id == user.id
    else:
        assert result == 401 or isinstance(result, InvalidTokenError) or result is None


@pytest.mark.parametrize("identity", [None, InvalidTokenError("revoked")])
def test_refresh_rejects_non_user_authentication_result(monkeypatch, identity):
    monkeypatch.setattr(AuthSecurity, "refresh", lambda _: "fixture-new-access")
    monkeypatch.setattr(Auth, "get_user_by_token", lambda _: identity)
    request = Request({"type": "http", "headers": [(b"cookie", f"{Env.REFRESH_TOKEN_NAME}=fixture-refresh".encode())]})
    with pytest.raises(Exception) as caught:
        refresh(request)
    assert caught.value.status_code == 401


@pytest.mark.parametrize("active", [True, False])
def test_refresh_observes_account_revocation_with_existing_cookie(principal, monkeypatch, active):
    engine, user, _ = principal
    if not active:
        with engine.begin() as db:
            db.execute(update(User.__table__).where(User.__table__.c.id == user.id).values(activated_at=None))
    monkeypatch.setattr(AuthSecurity, "refresh", lambda _: "fixture-new-access")
    request = Request({"type": "http", "headers": [(b"cookie", f"{Env.REFRESH_TOKEN_NAME}=fixture-refresh".encode())]})
    if active:
        assert refresh(request).status_code == 200
    else:
        with pytest.raises(Exception) as caught:
            refresh(request)
        assert caught.value.status_code == 401


def test_api_key_revocation_is_read_from_primary(principal):
    engine, _, key = principal
    with engine.begin() as db:
        db.execute(
            update(ApiKeySetting.__table__).where(ApiKeySetting.__table__.c.id == key.id).values(activated_at=None)
        )
    assert (
        Auth.validate_user_by_api_key(
            Headers({AuthSecurity.API_KEY_HEADER: f"sk-{key.get_uid()}.fixture-secret"}), "127.0.0.1"
        )
        == 401
    )


@pytest.mark.parametrize("state", ["active", "inactive", "deleted"])
@pytest.mark.parametrize("alias", [False, True])
def test_email_authentication_uses_current_primary_account(principal, state, alias):
    engine, user, _ = principal
    email = "alias@example.invalid" if alias else user.email
    with DbSession.atomic() as db:
        if alias:
            db.insert(UserEmail(user_id=user.id, email=email, verified_at=SafeDateTime.now()))
    if state != "active":
        with engine.begin() as db:
            db.execute(
                update(User.__table__)
                .where(User.__table__.c.id == user.id)
                .values(
                    activated_at=None if state == "inactive" else user.activated_at,
                    deleted_at=SafeDateTime.now() if state == "deleted" else None,
                )
            )
    found, subemail = UserRepository(None, None).get_by_email(email)
    if state == "deleted":
        assert found is None and subemail is None
    else:
        assert found.id == user.id
        assert (found.activated_at is not None) is (state == "active")
        assert (subemail is not None) is alias


def test_deleted_alias_cannot_resolve_account(principal):
    _, user, _ = principal
    with DbSession.atomic() as db:
        db.insert(UserEmail(user_id=user.id, email="removed@example.invalid", deleted_at=SafeDateTime.now()))
    assert UserRepository(None, None).get_by_email("removed@example.invalid") == (None, None)
