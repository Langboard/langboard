"""Bot credentials and restrictions must observe current primary database state."""

from unittest.mock import Mock
import pytest
from langboard_shared.core.caching import Cache
from langboard_shared.core.db import DbSession
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.security import AuthSecurity
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import Bot
from langboard_shared.domain.models.BaseBotModel import BotPlatform, BotPlatformRunningType
from langboard_shared.Env import Env
from langboard_shared.security import Auth
from sqlalchemy import delete, update
from starlette.datastructures import Headers


@pytest.fixture
def bot_principal(board, monkeypatch):
    engine = DbEngine.get_main_engine()
    Bot.__table__.create(engine)
    monkeypatch.setattr(type(Env), "ENVIRONMENT", property(lambda _: "production"))
    with DbSession.atomic() as db:
        bot = Bot(
            name="Fixture bot",
            bot_uname="revocation",
            app_api_token="fixture-bot-token",
            platform=BotPlatform.Langflow,
            platform_running_type=BotPlatformRunningType.Endpoint,
            ip_whitelist=["127.0.0.1"],
        )
        db.insert(bot)
    Cache.set(f"auth-bot-{bot.app_api_token}", bot, 300)
    assert Cache.get(f"auth-bot-{bot.app_api_token}", Bot.model_validate).id == bot.id
    return engine, bot


def bot_headers(bot):
    return Headers({AuthSecurity.API_TOKEN_HEADER: bot.app_api_token})


@pytest.mark.parametrize("board", ["sqlite://"], indirect=True)
@pytest.mark.parametrize("state", ["active", "rotated", "deleted", "removed"])
def test_stale_cache_cannot_authorize_revoked_bot(bot_principal, state):
    engine, bot = bot_principal
    with engine.begin() as db:
        where = Bot.__table__.c.id == bot.id
        if state == "rotated":
            db.execute(update(Bot.__table__).where(where).values(app_api_token="fixture-rotated-token"))
        elif state == "deleted":
            db.execute(update(Bot.__table__).where(where).values(deleted_at=SafeDateTime.now()))
        elif state == "removed":
            db.execute(delete(Bot.__table__).where(where))
    result = Auth.validate_bot(bot_headers(bot), "127.0.0.1")
    if state == "active":
        assert isinstance(result, Bot) and result.id == bot.id
    else:
        assert result == 401
        assert Auth.get_bot_by_api_token(bot.app_api_token) is None
    if state == "rotated":
        current = Auth.get_bot_by_api_token("fixture-rotated-token")
        assert isinstance(current, Bot) and current.id == bot.id


@pytest.mark.parametrize("board", ["sqlite://"], indirect=True)
@pytest.mark.parametrize("client_ip, expected", [("127.0.0.1", 401), ("192.0.2.1", "bot"), (None, 401)])
def test_current_ip_whitelist_controls_authentication(bot_principal, client_ip, expected):
    engine, bot = bot_principal
    with engine.begin() as db:
        db.execute(update(Bot.__table__).where(Bot.__table__.c.id == bot.id).values(ip_whitelist=["192.0.2.1"]))
    result = Auth.validate_bot(bot_headers(bot), client_ip)
    assert (isinstance(result, Bot) and result.id == bot.id) if expected == "bot" else result == expected


@pytest.mark.parametrize("board", ["sqlite://"], indirect=True)
@pytest.mark.parametrize("restricted", [True, False])
def test_current_platform_controls_all_ip_allowance(bot_principal, restricted):
    engine, bot = bot_principal
    # Seed an unrestricted cached principal, then independently change the primary row.
    bot.platform = BotPlatform.Default
    bot.platform_running_type = BotPlatformRunningType.Default
    Cache.set(f"auth-bot-{bot.app_api_token}", bot, 300)
    with engine.begin() as db:
        db.execute(
            update(Bot.__table__)
            .where(Bot.__table__.c.id == bot.id)
            .values(
                platform=BotPlatform.Langflow if restricted else BotPlatform.Default,
                platform_running_type=BotPlatformRunningType.Endpoint if restricted else BotPlatformRunningType.Default,
            )
        )
    result = Auth.validate_bot(bot_headers(bot), None)
    assert result == 401 if restricted else isinstance(result, Bot)


@pytest.mark.parametrize("board", ["sqlite://"], indirect=True)
def test_development_keeps_existing_all_ip_allowance(bot_principal, monkeypatch):
    _, bot = bot_principal
    monkeypatch.setattr(type(Env), "ENVIRONMENT", property(lambda _: "development"))
    assert isinstance(Auth.validate_bot(bot_headers(bot), None), Bot)


@pytest.mark.parametrize("board", ["sqlite://"], indirect=True)
def test_database_failure_denies_despite_cached_bot(bot_principal):
    engine, bot = bot_principal
    Bot.__table__.drop(engine)
    assert Auth.get_bot_by_api_token(bot.app_api_token) is None
    assert Auth.validate_bot(bot_headers(bot), "127.0.0.1") == 401


@pytest.mark.parametrize("board", ["sqlite://"], indirect=True)
def test_actual_database_auth_survives_cache_outage(bot_principal, monkeypatch):
    _, bot = bot_principal
    monkeypatch.setattr(Cache, "get", Mock(side_effect=ConnectionError("cache unavailable")))
    monkeypatch.setattr(Cache, "set", Mock(side_effect=ConnectionError("cache unavailable")))
    result = Auth.validate_bot(bot_headers(bot), "127.0.0.1")
    assert isinstance(result, Bot) and result.id == bot.id
