"""Disposable cross-process refresh probe using the real encrypted FileTreeStore."""

import asyncio
import json
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
import anyio
import httpx
from fastmcp.server.auth.oauth_proxy.proxy import JTIMapping, RefreshTokenMetadata, UpstreamTokenSet, _hash_token
from fastmcp.server.auth.oidc_proxy import OIDCConfiguration, OIDCProxy
from langboard.mcp_integration.OAuth import LangboardOIDCProxy
from langboard_shared.core.db.DbEngine import DbEngine
from mcp.server.auth.provider import TokenError
from mcp.shared.auth import OAuthClientInformationFull
from sqlalchemy import create_engine


def provider(*, storage=None):
    configuration = OIDCConfiguration(
        issuer="https://fixture.invalid",
        authorization_endpoint="https://fixture.invalid/authorize",
        token_endpoint="https://fixture.invalid/token",
        jwks_uri="https://fixture.invalid/jwks",
        response_types_supported=["code"],
        subject_types_supported=["public"],
        id_token_signing_alg_values_supported=["RS256"],
    )
    OIDCProxy.get_oidc_configuration = lambda *args, **kwargs: configuration
    proxy = LangboardOIDCProxy(
        config_url="https://fixture.invalid/discovery",
        client_id="fixture",
        client_secret="fixture-secret",
        base_url="https://fixture.invalid",
        jwt_signing_key="isolated-refresh-fixture-key-not-production",
        verify_id_token=False,
        client_storage=storage,
    )
    proxy.set_mcp_path("/mcp")
    return proxy


async def seed(proxy):
    now = time.time()
    token = proxy.jwt_issuer.issue_refresh_token(client_id="fixture", scopes=["openid"], jti="refresh", expires_in=120)
    await proxy._refresh_token_store.put(
        key=_hash_token(token),
        value=RefreshTokenMetadata(client_id="fixture", scopes=["openid"], expires_at=int(now) + 120, created_at=now),
        ttl=120,
    )
    for jti in ("refresh", "access"):
        await proxy._jti_mapping_store.put(
            key=jti, value=JTIMapping(jti=jti, upstream_token_id="upstream", created_at=now), ttl=120
        )
    await proxy._upstream_token_store.put(
        key="upstream",
        value=UpstreamTokenSet(
            upstream_token_id="upstream",
            access_token="isolated-expired-access-marker",
            refresh_token="isolated-refresh-marker",
            refresh_token_expires_at=now + 120,
            expires_at=now - 1,
            token_type="Bearer",
            scope="openid",
            client_id="fixture",
            created_at=now,
            raw_token_data={},
        ),
        ttl=120,
    )
    await proxy._client_storage.put("fixture-refresh", {"token": token}, collection="fixture")
    return token


async def main():
    mode, database, directory, endpoint, identity = sys.argv[1:]
    root = Path(directory)
    engine = create_engine(database)
    DbEngine.get_main_engine = lambda: engine
    proxy = provider()
    client = OAuthClientInformationFull(client_id="fixture", redirect_uris=["https://fixture.invalid/callback"])
    try:
        if mode == "seed":
            await seed(proxy)
            print(json.dumps({"seeded": True}))
            return
        # Deterministic JWT input; no credential transfer outside encrypted storage.
        token = (await proxy._client_storage.get("fixture-refresh", collection="fixture"))["token"]
        loaded = await proxy.load_refresh_token(client, token)
        assert loaded is not None
        original = proxy._try_refresh_fence

        def observed_try():
            connection = original()
            if connection is None:
                (root / f"waiting-{identity}").touch()
            return connection

        proxy._try_refresh_fence = observed_try
        if mode == "baseline":
            # Previous contract: independent process-local locks cannot cooperate.
            @asynccontextmanager
            async def local_fence():
                async with anyio.Lock():
                    yield

            proxy._refresh_fence = local_fence

        async def refresh_token(**kwargs):
            async with httpx.AsyncClient() as session:
                response = await session.post(endpoint, json={"scope": kwargs["scope"]})
                response.raise_for_status()
                return response.json()

        @asynccontextmanager
        async def oauth_client():
            yield SimpleNamespace(refresh_token=refresh_token)

        proxy._upstream_oauth_client = oauth_client
        (root / f"ready-{identity}").touch()
        while not (root / "go").exists():
            await asyncio.sleep(0.01)
        try:
            result = await proxy.exchange_refresh_token(client, loaded, ["openid"])
            assert result.scope == "openid"
            print(json.dumps({"status": "rotated"}))
        except TokenError as error:
            print(json.dumps({"status": error.error}))
    finally:
        engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
