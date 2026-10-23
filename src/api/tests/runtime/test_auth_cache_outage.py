from unittest.mock import Mock
import pytest
from jwt import InvalidTokenError
from langboard_shared.core.caching import Cache
from langboard_shared.core.types import SafeDateTime
from langboard_shared.domain.models import Bot, User
from langboard_shared.security.Auth import Auth


@pytest.mark.parametrize("kind", ["user", "bot"])
def test_valid_identity_survives_cache_outage(monkeypatch, kind):
    identity = (
        User.model_construct(id=1, activated_at=SafeDateTime.now()) if kind == "user" else Bot.model_construct(id=1)
    )
    lookup = Mock(return_value=identity)
    monkeypatch.setattr(Auth, "_Auth__get_model_by_columns", lookup)
    monkeypatch.setattr(Cache, "get", Mock(side_effect=ConnectionError("cache unavailable")))
    monkeypatch.setattr(Cache, "set", Mock(side_effect=ConnectionError("cache unavailable")))

    result = Auth.get_user_by_id(1) if kind == "user" else Auth.get_bot_by_api_token("test-token")

    assert result is identity
    lookup.assert_called_once()


@pytest.mark.parametrize("kind", ["user", "bot"])
@pytest.mark.parametrize("database_error", [False, True])
def test_cache_outage_does_not_authorize_missing_identity(monkeypatch, kind, database_error):
    lookup = Mock(side_effect=RuntimeError("database unavailable")) if database_error else Mock(return_value=None)
    monkeypatch.setattr(Auth, "_Auth__get_model_by_columns", lookup)
    monkeypatch.setattr(Cache, "get", Mock(side_effect=ConnectionError("cache unavailable")))
    cache_write = Mock()
    monkeypatch.setattr(Cache, "set", cache_write)

    result = Auth.get_user_by_id(1) if kind == "user" else Auth.get_bot_by_api_token("test-token")

    if kind == "user" and not database_error:
        assert isinstance(result, InvalidTokenError)
    else:
        assert result is None
    cache_write.assert_not_called()
