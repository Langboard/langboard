"""Exercise overlapping refresh exchanges against native FastMCP rotation."""

import asyncio
import threading
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
import anyio
import pytest
from fastmcp.server.auth.oauth_proxy.proxy import JTIMapping, RefreshTokenMetadata, UpstreamTokenSet, _hash_token
from fastmcp.server.auth.oidc_proxy import OIDCConfiguration, OIDCProxy
from key_value.aio.stores.memory import MemoryStore
from langboard.mcp_integration.OAuth import LangboardOIDCProxy
from langboard_shared.core.db.DbEngine import DbEngine
from mcp.server.auth.provider import AccessToken, TokenError
from mcp.shared.auth import OAuthClientInformationFull
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from src.api.tests.mcp_integration.fixtures.native_oauth_refresh_worker import provider, seed
from src.api.tests.mcp_integration.test_native_oauth_durable_storage import refresh_postgres as refresh_postgres


@pytest.fixture(autouse=True, params=["sqlite", "sqlite-memory", "postgresql"])
def isolated_refresh_database(monkeypatch, tmp_path, request):
    url = (
        request.getfixturevalue("refresh_postgres")
        if request.param == "postgresql"
        else f"sqlite:///{tmp_path / 'refresh.sqlite'}"
    )
    engine = (
        create_engine("sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False})
        if request.param == "sqlite-memory"
        else create_engine(url)
    )
    monkeypatch.setattr(DbEngine, "get_main_engine", lambda: engine)
    yield engine
    engine.dispose()


@pytest.mark.parametrize("scope_case", ["unchanged", "broadened", "narrowed", "client_expansion"])
@pytest.mark.parametrize("upstream_failure", [False, True])
async def test_concurrent_exchange_consumes_refresh_once(monkeypatch, upstream_failure, scope_case):
    """Two requests may load first; only one can exchange the loaded refresh."""
    discovery = OIDCConfiguration(
        issuer="https://invalid.example",
        authorization_endpoint="https://invalid.example/authorize",
        token_endpoint="https://invalid.example/token",
        jwks_uri="https://invalid.example/jwks",
        response_types_supported=["code"],
        subject_types_supported=["public"],
        id_token_signing_alg_values_supported=["RS256"],
    )
    monkeypatch.setattr(OIDCProxy, "get_oidc_configuration", lambda *args: discovery)
    storage = MemoryStore()

    def make_proxy():
        return LangboardOIDCProxy(
            config_url="https://invalid.example/discovery",
            client_id="isolated",
            client_secret="isolated-secret",
            base_url="https://invalid.example",
            jwt_signing_key="isolated-test-signing-material-not-production",
            client_storage=storage,
            verify_id_token=False,
        )

    proxy = make_proxy()
    other = make_proxy()
    other.set_mcp_path("/mcp")
    proxy.set_mcp_path("/mcp")
    client = OAuthClientInformationFull(client_id="isolated", redirect_uris=["https://invalid.example/callback"])
    now = time.time()
    approved = ["openid", "profile"] if scope_case == "narrowed" else ["openid"]
    requested = ["openid"]
    upstream_scopes = "openid profile mcp:access" if scope_case != "unchanged" else "openid"
    token = proxy.jwt_issuer.issue_refresh_token(
        client_id="isolated", scopes=approved, jti="refresh-jti", expires_in=120
    )
    await proxy._refresh_token_store.put(
        key=_hash_token(token),
        value=RefreshTokenMetadata(client_id="isolated", scopes=approved, expires_at=int(now) + 120, created_at=now),
        ttl=120,
    )
    await proxy._jti_mapping_store.put(
        key="refresh-jti", value=JTIMapping(jti="refresh-jti", upstream_token_id="upstream", created_at=now), ttl=120
    )
    await proxy._upstream_token_store.put(
        key="upstream",
        value=UpstreamTokenSet(
            upstream_token_id="upstream",
            access_token="isolated-access",
            refresh_token="isolated-upstream-refresh",
            refresh_token_expires_at=now + 120,
            expires_at=now + 60,
            token_type="Bearer",
            scope=" ".join(approved),
            client_id="isolated",
            created_at=now,
            raw_token_data={},
        ),
        ttl=120,
    )
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def refresh_token(**kwargs):
        nonlocal calls
        assert kwargs["scope"] == "openid"
        calls += 1
        if upstream_failure and calls == 1:
            raise RuntimeError("Isolated upstream failure")
        entered.set()
        await release.wait()
        return {
            "access_token": "isolated-new-access",
            "refresh_token": "isolated-upstream-refresh",
            "expires_in": 60,
            "scope": upstream_scopes,
            "refresh_expires_in": 120,
        }

    @asynccontextmanager
    async def oauth_client():
        yield SimpleNamespace(refresh_token=refresh_token)

    monkeypatch.setattr(proxy, "_upstream_oauth_client", oauth_client)
    monkeypatch.setattr(other, "_upstream_oauth_client", oauth_client)
    first, second = await asyncio.gather(
        proxy.load_refresh_token(client, token), other.load_refresh_token(client, token)
    )
    assert first is not None and second is not None
    if scope_case == "client_expansion":
        with pytest.raises(TokenError) as denied:
            await proxy.exchange_refresh_token(client, first, ["openid", "mcp:access"])
        assert denied.value.error == "invalid_scope"
        assert calls == 0
        assert await proxy.load_refresh_token(client, token) is not None
        return
    if upstream_failure:
        with pytest.raises(TokenError, match="Upstream refresh failed"):
            await proxy.exchange_refresh_token(client, first, requested)
        assert await proxy.load_refresh_token(client, token) is not None
        assert proxy._translate_scopes_from_idp(["mcp:access"]) == ["mcp:access"]
    task = asyncio.create_task(proxy.exchange_refresh_token(client, first, requested))
    await asyncio.wait_for(entered.wait(), timeout=2)
    # Another authorization task must not inherit the in-flight refresh limit.
    assert proxy._translate_scopes_from_idp(["openid", "mcp:access"]) == ["openid", "mcp:access"]
    attempted = asyncio.Event()
    original_try = other._try_refresh_fence

    def observed_try():
        connection = original_try()
        if connection is None:
            loop.call_soon_threadsafe(attempted.set)
        return connection

    loop = asyncio.get_running_loop()
    monkeypatch.setattr(other, "_try_refresh_fence", observed_try)
    concurrent = asyncio.create_task(other.exchange_refresh_token(client, second, requested))
    await asyncio.wait_for(attempted.wait(), timeout=2)
    assert calls == (2 if upstream_failure else 1)
    release.set()
    results = await asyncio.gather(task, concurrent, return_exceptions=True)
    successes = [value for value in results if not isinstance(value, BaseException)]
    failures = [value for value in results if isinstance(value, TokenError)]
    counts = (len(successes), len(failures))
    assert counts == (1, 1)
    assert failures[0].error == "invalid_grant"
    assert calls == (2 if upstream_failure else 1)
    issued = successes[0]
    assert issued.scope == "openid"
    assert proxy.jwt_issuer.verify_token(issued.access_token)["scope"] == "openid"
    assert proxy.jwt_issuer.verify_token(issued.refresh_token, expected_token_use="refresh")["scope"] == "openid"
    assert (await proxy.load_refresh_token(client, issued.refresh_token)).scopes == requested
    assert await proxy.load_refresh_token(client, token) is None
    assert await proxy.load_refresh_token(client, successes[0].refresh_token) is not None

    assert proxy._translate_scopes_from_idp(["openid", "profile", "mcp:access"]) == ["openid", "profile", "mcp:access"]


@pytest.fixture
def refresh_pair(monkeypatch):
    monkeypatch.setattr(OIDCProxy, "get_oidc_configuration", OIDCProxy.get_oidc_configuration)
    storage = MemoryStore()
    return provider(storage=storage), provider(storage=storage)


@pytest.mark.parametrize("contender", ["explicit", "transparent"])
async def test_explicit_and_transparent_refresh_cooperate(refresh_pair, monkeypatch, contender):
    first, second = refresh_pair
    token = await seed(first)
    client = OAuthClientInformationFull(client_id="fixture", redirect_uris=["https://fixture.invalid/callback"])
    loaded = await second.load_refresh_token(client, token)
    access = first.jwt_issuer.issue_access_token(client_id="fixture", scopes=["openid"], jti="access", expires_in=120)
    entered = asyncio.Event()
    release = asyncio.Event()
    waiting = asyncio.Event()
    loop = asyncio.get_running_loop()
    calls = []
    original = second._try_refresh_fence

    def observed_try():
        connection = original()
        if connection is None:
            loop.call_soon_threadsafe(waiting.set)
        return connection

    async def verify(token):
        return AccessToken(token=token, client_id="fixture", scopes=["openid"], expires_at=int(time.time()) + 60)

    async def refresh_token(**kwargs):
        calls.append(kwargs["refresh_token"])
        entered.set()
        await release.wait()
        return {"access_token": "new-access", "refresh_token": "new-refresh", "scope": "openid", "expires_in": 60}

    @asynccontextmanager
    async def oauth_client():
        yield SimpleNamespace(refresh_token=refresh_token)

    for proxy in refresh_pair:
        monkeypatch.setattr(proxy, "_upstream_oauth_client", oauth_client)
        monkeypatch.setattr(proxy, "_token_validator", SimpleNamespace(verify_token=verify))
        monkeypatch.setattr(proxy, "_token_expiry_threshold_seconds", 1)
    monkeypatch.setattr(second, "_try_refresh_fence", observed_try)
    # The explicit winner persists its complete rotation before the access reader rereads.
    owner = asyncio.create_task(first.exchange_refresh_token(client, loaded, ["openid"]))
    await asyncio.wait_for(entered.wait(), 2)
    operation = (
        second.load_access_token(access)
        if contender == "transparent"
        else second.exchange_refresh_token(client, loaded, ["openid"])
    )
    waiter = asyncio.create_task(operation)
    await asyncio.wait_for(waiting.wait(), 2)
    assert calls == ["isolated-refresh-marker"]
    release.set()
    await owner
    if contender == "explicit":
        with pytest.raises(TokenError, match="consumed"):
            await waiter
    else:
        assert await waiter is not None
    assert calls == ["isolated-refresh-marker"]


@pytest.mark.parametrize("route", ["explicit", "transparent"])
@pytest.mark.parametrize("stop", ["timeout", "cancel", "database_error"])
async def test_waiting_refresh_fails_closed_and_cleans_up(refresh_pair, monkeypatch, route, stop):
    owner, waiter = refresh_pair
    token = await seed(owner)
    client = OAuthClientInformationFull(client_id="fixture", redirect_uris=["https://fixture.invalid/callback"])
    loaded = await waiter.load_refresh_token(client, token)
    access = owner.jwt_issuer.issue_access_token(client_id="fixture", scopes=["openid"], jti="access", expires_in=120)
    calls = []
    attempted = asyncio.Event()
    loop = asyncio.get_running_loop()
    original = waiter._try_refresh_fence

    def observed_try():
        if stop == "database_error":
            raise OSError("Disposable database failure")
        result = original()
        loop.call_soon_threadsafe(attempted.set)
        return result

    async def verify(token):
        return AccessToken(token=token, client_id="fixture", scopes=["openid"], expires_at=int(time.time()) + 60)

    async def forbidden(**kwargs):
        calls.append(True)
        raise AssertionError("Upstream must not be called without the database fence")

    @asynccontextmanager
    async def oauth_client():
        yield SimpleNamespace(refresh_token=forbidden)

    monkeypatch.setattr(waiter, "_try_refresh_fence", observed_try)
    monkeypatch.setattr(waiter, "_refresh_fence_timeout", 0.05)
    monkeypatch.setattr(waiter, "_upstream_oauth_client", oauth_client)
    monkeypatch.setattr(waiter, "_token_validator", SimpleNamespace(verify_token=verify))
    monkeypatch.setattr(waiter, "_token_expiry_threshold_seconds", 1)
    async with owner._refresh_fence():

        async def run():
            return (
                await waiter.load_access_token(access)
                if route == "transparent"
                else await waiter.exchange_refresh_token(client, loaded, ["openid"])
            )

        if stop == "cancel":
            # AnyIO cancellation exercises shielded handoff and cleanup semantics.
            async with anyio.create_task_group() as group:
                group.start_soon(run)
                await asyncio.wait_for(attempted.wait(), 2)
                group.cancel_scope.cancel()
        elif route == "transparent":
            assert await run() is None
        else:
            with pytest.raises(TimeoutError if stop == "timeout" else OSError):
                await run()
    assert calls == []
    assert await waiter.load_refresh_token(client, token) is not None
    # A failed transparent call must not poison the next normal access validation.
    upstream = await waiter._upstream_token_store.get(key="upstream")
    upstream.expires_at = time.time() + 60
    await waiter._upstream_token_store.put(key="upstream", value=upstream, ttl=120)
    assert await waiter.load_access_token(access) is not None
    # Owner and failed waiter released every connection/fence.
    async with owner._refresh_fence():
        pass


async def test_native_task_cancel_during_acquire_does_not_orphan_fence(refresh_pair, monkeypatch):
    proxy, other = refresh_pair
    thread_entered = threading.Event()
    release_thread = threading.Event()
    original = proxy._try_refresh_fence

    def delayed_acquire():
        connection = original()
        thread_entered.set()
        assert release_thread.wait(2)
        return connection

    monkeypatch.setattr(proxy, "_try_refresh_fence", delayed_acquire)

    async def acquire():
        async with proxy._refresh_fence():
            pytest.fail("Cancelled acquisition must never enter the exchange")

    task = asyncio.create_task(acquire())
    while not thread_entered.is_set():
        await asyncio.sleep(0.001)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release_thread.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with other._refresh_fence():
        pass


def test_refresh_namespace_is_stable_and_instance_scoped(refresh_pair):
    first, second = refresh_pair
    assert first._refresh_fence_key() == second._refresh_fence_key()
    second._upstream_client_id = "another-native-instance"
    assert first._refresh_fence_key() != second._refresh_fence_key()


async def test_contender_cannot_rollback_static_pool_owner(refresh_pair, isolated_refresh_database):
    engine = isolated_refresh_database
    if not isinstance(engine.pool, StaticPool):
        return
    owner, contender = refresh_pair
    async with owner._refresh_fence() as fence:
        # Inspect the owner's existing DBAPI transaction without another checkout.
        raw = fence.connection.connection.connection.driver_connection
        assert raw.in_transaction
        assert await anyio.to_thread.run_sync(contender._try_refresh_fence) is None
        assert raw.in_transaction
    assert not raw.in_transaction
    async with contender._refresh_fence():
        pass


@pytest.mark.parametrize("route", ["explicit", "transparent"])
async def test_cancel_during_exchange_releases_database_fence(refresh_pair, monkeypatch, route):
    owner, contender = refresh_pair
    token = await seed(owner)
    client = OAuthClientInformationFull(client_id="fixture", redirect_uris=["https://fixture.invalid/callback"])
    loaded = await owner.load_refresh_token(client, token)
    access = owner.jwt_issuer.issue_access_token(client_id="fixture", scopes=["openid"], jti="access", expires_in=120)
    entered = asyncio.Event()

    async def verify(token):
        return AccessToken(token=token, client_id="fixture", scopes=["openid"], expires_at=int(time.time()) + 60)

    async def refresh_token(**kwargs):
        entered.set()
        await asyncio.Event().wait()

    @asynccontextmanager
    async def oauth_client():
        yield SimpleNamespace(refresh_token=refresh_token)

    monkeypatch.setattr(owner, "_upstream_oauth_client", oauth_client)
    monkeypatch.setattr(owner, "_token_validator", SimpleNamespace(verify_token=verify))
    monkeypatch.setattr(owner, "_token_expiry_threshold_seconds", 1)
    operation = (
        owner.load_access_token(access)
        if route == "transparent"
        else owner.exchange_refresh_token(client, loaded, ["openid"])
    )
    task = asyncio.create_task(operation)
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with contender._refresh_fence():
        pass
