"""OIDC metadata remains verifiable when its optional cache is unavailable."""

from time import time
from unittest.mock import Mock
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt import InvalidAudienceError, InvalidSignatureError, encode
from jwt.algorithms import RSAAlgorithm
from langboard_shared.core.caching import Cache
from langboard_shared.core.security import OidcClient
from langboard_shared.Env import Env


@pytest.mark.parametrize("cache_read_fails", [False, True])
def test_signed_access_token_validates_without_metadata_cache(monkeypatch, cache_read_fails):
    monkeypatch.setitem(Env._Env__envs, "OIDC_BEARER_ENABLED", "true")
    monkeypatch.setitem(Env._Env__envs, "OIDC_RESOURCE_AUDIENCE", "langboard-api")
    monkeypatch.setitem(Env._Env__envs, "OIDC_DISCOVERY_URL", "https://issuer.example/discovery")
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
    jwk["kid"] = "test-key"
    discovery = {"issuer": "https://issuer.example", "jwks_uri": "https://issuer.example/jwks"}
    metadata = {"https://issuer.example/discovery": discovery, "https://issuer.example/jwks": {"keys": [jwk]}}
    fetch = Mock(side_effect=lambda method, url: metadata[url])
    monkeypatch.setattr(OidcClient, "_request_json", fetch)
    monkeypatch.setattr(
        Cache,
        "get",
        Mock(side_effect=ConnectionError("cache unavailable")) if cache_read_fails else Mock(return_value=None),
    )
    monkeypatch.setattr(Cache, "set", Mock(side_effect=ConnectionError("cache unavailable")))
    claims = {
        "iss": discovery["issuer"],
        "sub": "test-user",
        "aud": "langboard-api",
        "iat": int(time()),
        "exp": int(time()) + 60,
    }
    token = encode(claims, private_key, algorithm="RS256", headers={"kid": "test-key"})

    assert OidcClient.validate_access_token(token)["sub"] == "test-user"
    assert {call.args[1] for call in fetch.call_args_list} == set(metadata)
    with pytest.raises(InvalidAudienceError):
        OidcClient.validate_access_token(encode({**claims, "aud": "another-api"}, private_key, algorithm="RS256"))
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(InvalidSignatureError):
        OidcClient.validate_access_token(encode(claims, other_key, algorithm="RS256"))
    fetch.side_effect = RuntimeError("identity provider unavailable")
    with pytest.raises(RuntimeError, match="identity provider unavailable"):
        OidcClient.validate_access_token(token)
