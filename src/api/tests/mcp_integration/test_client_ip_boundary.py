"""Exercise client-IP resolution at the native credential and Uvicorn boundaries."""

import asyncio
from contextlib import contextmanager
from types import SimpleNamespace
import pytest
from langboard_shared import FastAPIRunner
from langboard_shared.core.db import DbSession
from langboard_shared.core.security import AuthSecurity, KeyVault
from langboard_shared.core.types import SnowflakeID
from langboard_shared.core.utils.IpAddress import resolve_client_ip
from langboard_shared.domain.models import ApiKeySetting, Bot, User
from langboard_shared.domain.models.BaseBotModel import BotPlatform, BotPlatformRunningType
from langboard_shared.Env import Env
from langboard_shared.helpers.MiddlewareHelper import MiddlewareHelper
from langboard_shared.security import Auth


@pytest.mark.parametrize(
    "peer,forwarded,trusted,expected",
    [
        ("198.51.100.20", "203.0.113.7", [], "198.51.100.20"),
        ("10.0.0.2", "203.0.113.7", ["10.0.0.0/24"], "203.0.113.7"),
        ("10.0.0.2", "203.0.113.7, 10.0.0.3", ["10.0.0.0/24"], "203.0.113.7"),
        ("10.0.0.2", "203.0.113.7, 198.51.100.20", ["10.0.0.0/24"], "198.51.100.20"),
        (None, "203.0.113.7", ["10.0.0.0/24"], None),
        ("10.0.0.2", "", ["10.0.0.0/24"], None),
        ("invalid-peer", "203.0.113.7", [], None),
        ("10.0.0.2", "203.0.113.7, invalid-hop", ["10.0.0.0/24"], None),
        ("10.0.0.2", "203.0.113.7,", ["10.0.0.0/24"], None),
        ("2001:db8::2", "2001:db8:1::7", ["2001:db8::/64"], "2001:db8:1::7"),
    ],
)
def test_resolver(peer, forwarded, trusted, expected):
    assert resolve_client_ip(peer, forwarded, trusted) == expected


@pytest.mark.parametrize("credential", ["api_key", "bot"])
@pytest.mark.parametrize(
    "peer,valid,allowed",
    [("198.51.100.20", True, False), ("203.0.113.7", True, True), ("203.0.113.7", False, False), (None, True, False)],
)
def test_native_credentials_ignore_forged_headers(monkeypatch, credential, peer, valid, allowed):
    monkeypatch.setattr(type(Env), "ENVIRONMENT", property(lambda _: "production"))
    monkeypatch.setattr(type(Env), "TRUSTED_PROXY_IPS", property(lambda _: []))
    user = User.model_construct(id=SnowflakeID(701))
    key = ApiKeySetting.model_construct(
        id=SnowflakeID(702), user_id=user.id, value="fixture-ref", ip_whitelist=["203.0.113.7"]
    )
    bot = Bot.model_construct(
        id=SnowflakeID(703),
        platform=BotPlatform.Langflow,
        platform_running_type=BotPlatformRunningType.Default,
        ip_whitelist=["203.0.113.7"],
    )

    @contextmanager
    def database(**_):
        yield SimpleNamespace(exec=lambda _: SimpleNamespace(first=lambda: key))

    monkeypatch.setattr(DbSession, "use", database)
    monkeypatch.setattr(KeyVault, "get_key", lambda _: "fixture-secret")
    monkeypatch.setattr(Auth, "get_user_by_id", lambda _: user)
    monkeypatch.setattr(Auth, "get_bot_by_api_token", lambda token: bot if token == "fixture-secret" else None)
    monkeypatch.setattr(Auth, "validate_user_by_api_token", lambda _: 401)
    secret = "fixture-secret" if valid else "wrong-secret"
    credential_header = (
        (b"x-api-key", f"sk-{key.get_uid()}.{secret}".encode())
        if credential == "api_key"
        else (b"x-api-token", secret.encode())
    )
    scope = {
        "type": "http",
        "headers": [credential_header, (b"x-real-ip", b"203.0.113.7"), (b"x-forwarded-for", b"203.0.113.7")],
        "client": (peer, 1234) if peer else None,
    }
    result = MiddlewareHelper.validate_auth(scope)
    assert (result is (user if credential == "api_key" else bot)) is allowed
    if not allowed:
        assert result == 401


@pytest.mark.parametrize("trusted,expected", [([], "198.51.100.20"), (["198.51.100.20"], "203.0.113.7")])
def test_native_runner_does_not_inherit_uvicorn_wildcard_proxy_trust(monkeypatch, trusted, expected):
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "*")
    monkeypatch.setattr(type(Env), "TRUSTED_PROXY_IPS", property(lambda _: trusted))
    options = SimpleNamespace(
        host="127.0.0.1",
        port=0,
        uds=None,
        lifespan="off",
        ssl_options={},
        workers=1,
        healthcheck_interval=30,
        watch=False,
    )
    monkeypatch.setattr(FastAPIRunner, "FastAPIAppConfig", lambda _: SimpleNamespace(load=lambda: options))
    captured = []
    monkeypatch.setattr(FastAPIRunner, "run_server", captured.append)
    observed = []

    async def app(scope, receive, send):
        observed.append(AuthSecurity.resolve_client_ip(scope["client"][0], {"x-forwarded-for": "203.0.113.7"}))

    FastAPIRunner.run(app, "unused-config", "/private/tmp")
    assert len(captured) == 1
    config = captured[0]
    config.load()
    scope = {
        "type": "http",
        "client": ("198.51.100.20", 1234),
        "headers": [(b"x-forwarded-for", b"203.0.113.7")],
        "scheme": "http",
    }
    asyncio.run(config.loaded_app(scope, None, None))
    assert observed == [expected]


@pytest.mark.parametrize("setting", ["*", "proxy.internal", "10.0.0.999", "10.0.0.0/99"])
def test_trusted_proxy_setting_rejects_non_ip_trust(monkeypatch, setting):
    monkeypatch.setattr(type(Env), "_Env__get_from_cache", lambda self, name, default=None: setting)
    with pytest.raises(ValueError):
        _ = Env.TRUSTED_PROXY_IPS


@pytest.mark.parametrize("setting,expected", [("", []), (" 10.0.0.2, 2001:db8::/64 ", ["10.0.0.2", "2001:db8::/64"])])
def test_trusted_proxy_setting_accepts_explicit_ip_boundaries(monkeypatch, setting, expected):
    monkeypatch.setattr(type(Env), "_Env__get_from_cache", lambda self, name, default=None: setting)
    assert Env.TRUSTED_PROXY_IPS == expected
