"""Reuse ciphertext verification without caching time or account authority."""

import importlib
import json
from time import time
from unittest.mock import Mock
import pytest
from jwt import ExpiredSignatureError
from langboard_shared.core.security.AuthSecurity import AuthSecurity
from langboard_shared.core.utils.Encryptor import Encryptor
from langboard_shared.Env import Env


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch):
    monkeypatch.setitem(Env._Env__envs, "JWT_SECRET_KEY", "synthetic-refresh-cost-test")
    AuthSecurity._AuthSecurity__decode_refresh_payload.cache_clear()
    yield
    AuthSecurity._AuthSecurity__decode_refresh_payload.cache_clear()


def test_verified_cookie_reuse_isolated_from_caller_mutation(monkeypatch):
    token = AuthSecurity.create_refresh_token(1)
    decrypt = Mock(wraps=Encryptor.decrypt)
    monkeypatch.setattr(Encryptor, "decrypt", decrypt)
    first = AuthSecurity.decode_refresh_token(token)
    first["sub"] = "other-user"
    assert AuthSecurity.decode_refresh_token(token)["sub"] == "1"
    assert decrypt.call_count == 1


def test_cached_cookie_still_expires(monkeypatch):
    token = AuthSecurity.create_refresh_token(1)
    payload = AuthSecurity.decode_refresh_token(token)
    module = importlib.import_module(AuthSecurity.__module__)
    monkeypatch.setattr(module, "timegm", lambda _: payload["exp"] + 1)
    with pytest.raises(ExpiredSignatureError):
        AuthSecurity.decode_refresh_token(token)


def test_rotated_key_and_modified_ciphertext_require_verification(monkeypatch):
    token = AuthSecurity.create_refresh_token(1)
    AuthSecurity.decode_refresh_token(token)
    with pytest.raises(Exception):
        AuthSecurity.decode_refresh_token("!" + token[1:])
    monkeypatch.setitem(Env._Env__envs, "JWT_SECRET_KEY", "different-synthetic-key")
    with pytest.raises(Exception):
        AuthSecurity.decode_refresh_token(token)


def test_invalid_payload_not_cached_and_capacity_bounded(monkeypatch):
    decrypt = Mock(return_value="{}")
    monkeypatch.setattr(Encryptor, "decrypt", decrypt)
    for _ in range(2):
        with pytest.raises(Exception):
            AuthSecurity.decode_refresh_token("invalid")
    assert decrypt.call_count == 2
    decrypt.return_value = json.dumps({"sub": "1", "iss": Env.PROJECT_NAME, "exp": int(time()) + 60})
    for i in range(130):
        AuthSecurity.decode_refresh_token(f"synthetic-{i}")
    assert AuthSecurity._AuthSecurity__decode_refresh_payload.cache_info().currsize == 128
