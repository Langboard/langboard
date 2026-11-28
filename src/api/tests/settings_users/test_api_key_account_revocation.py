"""Account revocation must persist across reactivation and concurrent key issuance."""

from datetime import timedelta
from unittest.mock import Mock
import pytest
from langboard_shared.core.caching import Cache
from langboard_shared.core.caching.InMemoryCache import InMemoryCache
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.security import AuthSecurity, KeyVault
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import ApiKeySetting, User
from langboard_shared.domain.models.ApiKeySetting import ApiKeyProvider
from langboard_shared.domain.services import DomainService
from langboard_shared.Env import Env
from langboard_shared.publishers import AppSettingPublisher, UserPublisher
from langboard_shared.security import Auth
from sqlalchemy import select, update
from starlette.datastructures import Headers
from test_role_concurrency import run_race
from test_self_deactivation import self_deactivation_engine  # noqa: F401


@pytest.fixture
def account_keys(self_deactivation_engine, monkeypatch):  # noqa: F811
    engine = self_deactivation_engine
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    monkeypatch.setattr(DbEngine, "get_readonly_engine", lambda: pytest.fail("Account key mutation used a replica"))
    monkeypatch.setattr(Cache, "_cache", InMemoryCache())
    monkeypatch.setattr(type(Env), "FULL_ADMIN_ACCESS_EMAILS", property(lambda _: []))
    monkeypatch.setattr(KeyVault, "name", lambda: "openbao")
    monkeypatch.setattr(KeyVault, "create_key", Mock(return_value="fixture-secret"))
    monkeypatch.setattr(KeyVault, "get_key", Mock(return_value="fixture-secret"))
    monkeypatch.setattr(KeyVault, "delete_key", Mock())
    for publisher, names in (
        (UserPublisher, ("updated", "deactivated")),
        (AppSettingPublisher, ("user_updated",)),
    ):
        for name in names:
            monkeypatch.setattr(publisher, name, Mock())
    with DbSession.atomic() as db:
        user = User(
            email="key-owner@example.invalid",
            password="test",
            firstname="Key",
            lastname="Owner",
            activated_at=SafeDateTime.now(),
        )
        db.insert(user)
        key = ApiKeySetting(
            user_id=user.id,
            name="existing",
            value="fixture-only",
            provider=ApiKeyProvider.OpenBao,
            ip_whitelist=["127.0.0.1"],
            activated_at=SafeDateTime.now(),
        )
        db.insert(key)
    return engine, user, key, DomainService()


@pytest.mark.parametrize("state", ["inactive", "deleted"])
def test_stale_active_owner_cannot_issue_or_activate_keys(account_keys, state):
    engine, user, key, service = account_keys
    with engine.begin() as db:
        db.execute(
            update(User)
            .where(User.id == user.id)
            .values(**({"activated_at": None} if state == "inactive" else {"deleted_at": SafeDateTime.now()}))
        )
        db.execute(update(ApiKeySetting).where(ApiKeySetting.id == key.id).values(activated_at=None))
    assert user.activated_at is not None and user.deleted_at is None
    assert service.api_key.create(user, "must reject") is None
    assert service.api_key.activate(key) is False
    KeyVault.create_key.assert_not_called()
    with engine.connect() as db:
        assert db.execute(select(ApiKeySetting.id)).all() == [(key.id,)]
        assert db.execute(select(ApiKeySetting.activated_at)).scalar_one() is None


def test_account_revokes_key_then_requires_explicit_activation(account_keys):
    engine, user, key, service = account_keys
    key_headers = Headers({AuthSecurity.API_KEY_HEADER: f"sk-{key.get_uid()}.fixture-secret"})
    assert isinstance(Auth.validate_user_by_api_key(key_headers, "127.0.0.1"), tuple)
    assert service.user.update(user, {"activated_at": None}, from_setting=True)
    assert service.user.update(user, {"activated_at": SafeDateTime.now()}, from_setting=True)
    assert Auth.validate_user_by_api_key(key_headers, "127.0.0.1") == 401
    assert service.api_key.activate(key)
    assert isinstance(
        Auth.validate_user_by_api_key(
            Headers({AuthSecurity.API_KEY_HEADER: f"sk-{key.get_uid()}.fixture-secret"}), "127.0.0.1"
        ),
        tuple,
    )
    created = service.api_key.create(user, "new", ["127.0.0.1"])
    assert created and created[0].user_id == user.id
    assert created[1] == f"sk-{created[0].get_uid()}.fixture-secret"
    KeyVault.delete_key.assert_not_called()


def test_failed_account_update_rolls_back_key_revocation(account_keys):
    engine, user, key, service = account_keys
    with engine.connect() as db:
        original_user = db.execute(select(User.activated_at, User.auth_version)).one()
        original_key = db.execute(select(ApiKeySetting.activated_at)).scalar_one()
    with pytest.raises(RuntimeError, match="rollback"):
        with DbSession.atomic():
            assert service.user.update(user, {"activated_at": None}, from_setting=True)
            raise RuntimeError("rollback")
    with engine.connect() as db:
        assert db.execute(select(User.activated_at, User.auth_version)).one() == original_user
        assert db.execute(select(ApiKeySetting.activated_at)).scalar_one() == original_key
    UserPublisher.deactivated.assert_not_called()
    assert user.auth_version == 0


@pytest.mark.parametrize("days", [-1, 1])
def test_activation_keeps_expiry_and_missing_key_guards(account_keys, days):
    engine, _, key, service = account_keys
    with engine.begin() as db:
        db.execute(
            update(ApiKeySetting)
            .where(ApiKeySetting.id == key.id)
            .values(expires_at=SafeDateTime.now() + timedelta(days=days), activated_at=None)
        )
    assert service.api_key.activate(key) is (days > 0)
    assert service.api_key.activate(None) is False
    assert service.api_key.activate(int(key.id) + 1) is False


@pytest.mark.parametrize("operation", ["create", "activate"])
@pytest.mark.parametrize("account_first", [True, False])
def test_account_lock_serializes_issuance_against_deactivation(account_keys, operation, account_first):
    engine, user, key, service = account_keys
    if engine.dialect.name != "postgresql":
        # SQLite does not implement SELECT FOR UPDATE. Sequential behavior is tested above.
        pytest.skip("SQLite does not support row locks; race cases run on PostgreSQL")

    def deactivate():
        return service.user.update(user, {"activated_at": None}, from_setting=True)

    def issue():
        return service.api_key.create(user, "racing") if operation == "create" else service.api_key.activate(key)

    first, second = run_race(
        engine, User, deactivate if account_first else issue, issue if account_first else deactivate
    )
    assert (first if account_first else second) is True
    if account_first:
        assert second is (None if operation == "create" else False)
    else:
        assert first
    with engine.connect() as db:
        assert db.execute(select(User.activated_at)).scalar_one() is None
        assert all(value is None for value in db.execute(select(ApiKeySetting.activated_at)).scalars())
