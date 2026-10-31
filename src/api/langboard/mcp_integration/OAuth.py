"""Optional native OAuth using FastMCP and existing Langboard identity policy."""

import asyncio
import logging
import threading
from contextvars import ContextVar
from hashlib import sha256
from urllib.parse import urlsplit
from uuid import uuid4
from weakref import WeakKeyDictionary
import anyio
from fastmcp.exceptions import AuthorizationError
from fastmcp.server.auth.oidc_proxy import OIDCProxy
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware import Middleware
from fastmcp.utilities.async_utils import call_sync_fn_in_threadpool
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.domain.models import IdentityProvider
from langboard_shared.domain.services import DomainService
from langboard_shared.Env import Env
from mcp.server.auth.provider import RefreshToken, TokenError
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import StaticPool
from ..middlewares.McpAuthMiddleware import mcp_auth_context


_refresh_fence_failure = ContextVar("native_oauth_refresh_fence_failure", default=None)
_refresh_scope_limit = ContextVar("native_oauth_refresh_scope_limit", default=None)

_sqlite_connection_guards = WeakKeyDictionary()
_sqlite_guard_registry_lock = threading.Lock()


class _NativeRefreshConnection:
    def __init__(self, connection, guard):
        self.connection = connection
        self.guard = guard

    def close(self):
        try:
            self.connection.close()
        finally:
            if self.guard is not None:
                self.guard.release()


class _NativeRefreshFence:
    """Own the DB transaction even when cancellation interrupts acquisition."""

    def __init__(self, provider):
        self.provider = provider
        self.connection = None

    @staticmethod
    async def _database_call(function):
        # Uvicorn uses asyncio. Native task.cancel() must also wait for the
        # non-abandonable database operation, not just AnyIO cancel scopes.
        with anyio.CancelScope(shield=True):
            operation = asyncio.create_task(anyio.to_thread.run_sync(function))
            cancelled = False
            while not operation.done():
                try:
                    await asyncio.shield(operation)
                except asyncio.CancelledError:
                    cancelled = True
            result = operation.result()
            if cancelled:
                raise asyncio.CancelledError
            return result

    def _acquire(self):
        self.connection = self.provider._try_refresh_fence()

    async def __aenter__(self):
        try:
            with anyio.fail_after(self.provider._refresh_fence_timeout):
                while self.connection is None:
                    # A shielded handoff never abandons an acquiring database thread.
                    await self._database_call(self._acquire)
                    if self.connection is None:
                        await anyio.sleep(0.01)
                await anyio.lowlevel.checkpoint()
            return self
        except BaseException:
            await self.__aexit__(BaseException, None, None)
            raise

    async def __aexit__(self, error_type, error, traceback):
        if error_type is not None:
            failure = _refresh_fence_failure.get()
            if failure is not None:
                failure.append(True)
        if self.connection is not None:
            try:
                await self._database_call(self.connection.close)
            finally:
                self.connection = None


class LangboardOIDCProxy(OIDCProxy):
    """Keep upstream scopes within the MCP client's verified consent grant."""

    _refresh_fence_timeout = 5.0

    def _refresh_fence_key(self):
        namespace = "\0".join(
            (
                "langboard-native-oauth",
                str(self.base_url).rstrip("/"),
                self._upstream_client_id,
                self._upstream_token_endpoint,
            )
        )
        return int.from_bytes(sha256(namespace.encode()).digest()[:8], "big", signed=True)

    def _try_refresh_fence(self):
        """Attempt once without waiting in a database thread; caller owns the connection."""
        engine = DbEngine.get_main_engine()
        guard = None
        # StaticPool shares one DBAPI connection. Prevent another OAuth checkout
        # from rolling back that same connection while its owner holds the fence.
        if engine.dialect.name == "sqlite" and isinstance(engine.pool, StaticPool):
            with _sqlite_guard_registry_lock:
                guard = _sqlite_connection_guards.setdefault(engine, threading.Lock())
            if not guard.acquire(blocking=False):
                return None
        connection = None
        try:
            connection = engine.connect()
            dialect = connection.dialect.name
            if dialect == "postgresql":
                acquired = connection.execute(
                    text("SELECT pg_try_advisory_xact_lock(:key)"),
                    {"key": self._refresh_fence_key()},
                ).scalar_one()
            elif dialect == "sqlite":
                timeout = connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one()
                try:
                    connection.exec_driver_sql("PRAGMA busy_timeout=0")
                    connection.exec_driver_sql("BEGIN IMMEDIATE")
                    acquired = True
                except OperationalError as error:
                    if "locked" not in str(error.orig).lower():
                        raise
                    acquired = False
                finally:
                    connection.exec_driver_sql(f"PRAGMA busy_timeout={int(timeout)}")
            else:
                raise RuntimeError("Native OAuth refresh supports PostgreSQL and SQLite")
            if acquired:
                return _NativeRefreshConnection(connection, guard)
        except BaseException:
            try:
                if connection is not None:
                    connection.close()
            finally:
                if guard is not None:
                    guard.release()
            raise
        try:
            connection.close()
        finally:
            if guard is not None:
                guard.release()
        return None

    def _refresh_fence(self):
        return _NativeRefreshFence(self)

    def _get_refresh_lock(self, token_id):
        # FastMCP enters this before its authoritative upstream-token reread.
        return self._refresh_fence()

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        async with self._refresh_fence():
            current = await self.load_refresh_token(client, refresh_token.token)
            if current is None:
                raise TokenError("invalid_grant", "Refresh token was already consumed or expired")
            try:
                granted = self.jwt_issuer.verify_token(current.token, expected_token_use="refresh").get("scope", "")
            except Exception as error:
                raise TokenError("invalid_grant", "Invalid refresh token") from error
            if not isinstance(granted, str):
                raise TokenError("invalid_grant", "Invalid refresh scope grant")
            allowed = set(granted.split()).intersection(current.scopes)
            if not set(scopes).issubset(allowed):
                raise TokenError("invalid_scope", "Refresh scope exceeds the approved grant")
            scope_token = _refresh_scope_limit.set(frozenset(scopes))
            try:
                return await super().exchange_refresh_token(client, current, scopes)
            finally:
                _refresh_scope_limit.reset(scope_token)

    def _translate_scopes_from_idp(self, scopes: list[str]) -> list[str]:
        translated = super()._translate_scopes_from_idp(scopes)
        limit = _refresh_scope_limit.get()
        return translated if limit is None else [scope for scope in translated if scope in limit]

    async def storage_readiness(self, *, timeout=3):
        """Probe the existing encrypted store without touching client credentials."""
        if not hasattr(self, "_readiness_lock"):
            self._readiness_lock = anyio.Lock()
        result = {"component": "oauth_storage", "status": "ready"}
        key = str(uuid4())
        collection = "langboard_oauth_readiness"
        payload = {"probe": key}
        try:
            with anyio.fail_after(timeout):
                async with self._readiness_lock:
                    try:
                        await self._client_storage.put(key, payload, collection=collection, ttl=60)
                        if await self._client_storage.get(key, collection=collection) != payload:
                            result.update(status="not_ready", reason="round_trip_mismatch")
                    finally:
                        await self._client_storage.delete(key, collection=collection)
        except TimeoutError:
            result.update(status="not_ready", reason="storage_timeout")
        except OSError:
            result.update(status="not_ready", reason="storage_io_error")
        except Exception:
            result.update(status="not_ready", reason="storage_error")
        state = result.get("reason", "ready")
        if getattr(self, "_readiness_state", None) != state:
            logging.getLogger(__name__).log(
                logging.INFO if state == "ready" else logging.WARNING,
                "Native MCP OAuth storage readiness: %s",
                state,
            )
            self._readiness_state = state
        return result

    async def load_access_token(self, token: str):
        failure = []
        marker = _refresh_fence_failure.set(failure)
        try:
            validated = await super().load_access_token(token)
        finally:
            _refresh_fence_failure.reset(marker)
        if failure:
            return None
        if validated is None:
            return None
        try:
            signed = self.jwt_issuer.verify_token(token)
            granted = signed.get("scope", "")
            client_id = signed.get("client_id")
        except Exception:
            return None
        if not isinstance(granted, str) or not isinstance(client_id, str) or not client_id:
            return None
        allowed = set(granted.split())
        return validated.model_copy(
            update={
                "client_id": client_id,
                "claims": {**(validated.claims or {}), "langboard_native_client_id": client_id},
                "scopes": [scope for scope in validated.scopes if scope in allowed],
            }
        )


def create_oauth_provider(*, client_storage=None):
    """Keep provider choice and persistent storage under operator control."""
    if not Env.MCP_OAUTH_ENABLED:
        return None
    for value in (Env.MCP_OAUTH_BASE_URL, Env.MCP_OAUTH_DISCOVERY_URL):
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("Native MCP OAuth requires HTTPS public and discovery URLs")
    if not Env.MCP_OAUTH_CLIENT_ID or len(Env.MCP_OAUTH_SIGNING_KEY) < 32:
        raise ValueError("Native MCP OAuth requires a client and stable signing key")
    if "mcp:access" not in Env.MCP_OAUTH_SCOPES.split():
        raise ValueError("Native MCP OAuth scopes must include mcp:access")
    return LangboardOIDCProxy(
        config_url=Env.MCP_OAUTH_DISCOVERY_URL,
        client_id=Env.MCP_OAUTH_CLIENT_ID,
        client_secret=Env.MCP_OAUTH_CLIENT_SECRET or None,
        base_url=Env.MCP_OAUTH_BASE_URL,
        jwt_signing_key=Env.MCP_OAUTH_SIGNING_KEY,
        verify_id_token=True,
        valid_scopes=Env.MCP_OAUTH_SCOPES.split(),
        extra_authorize_params={"prompt": Env.MCP_OAUTH_PROMPT} if Env.MCP_OAUTH_PROMPT else None,
        require_authorization_consent=True,
        client_storage=client_storage,
    )


def resolve_principal(access_token, service):
    """Resolve verified OIDC claims; never infer identity from names or email."""
    claims = access_token.claims if access_token else None
    issuer = claims.get("iss") if isinstance(claims, dict) else None
    subject = claims.get("sub") if isinstance(claims, dict) else None
    if not isinstance(issuer, str) or not issuer or not isinstance(subject, str) or not subject:
        raise AuthorizationError("A verified OIDC identity is required")
    issuer = issuer.strip().rstrip("/")
    if not issuer:
        raise AuthorizationError("A verified OIDC identity is required")
    user = service.identity_link.get_user_by_provider_external_id(
        IdentityProvider.Oidc, subject, issuer, consistent=True
    )
    if not user or not user.activated_at or user.deleted_at:
        raise AuthorizationError("An active linked user is required")
    from langboard_shared.domain.services.NativeMcpConsent import connect_native_client

    client_id = claims.get("langboard_native_client_id")
    if not isinstance(client_id, str) or not client_id or client_id != access_token.client_id:
        raise AuthorizationError("A verified native OAuth client is required")
    connection = connect_native_client(user, client_id)
    return {
        "native_connection_id": connection.id,
        "user_or_bot": user,
        "api_key": None,
        "transport": "oauth",
        "collaboration_channel": CollaborationChannel.Mcp,
    }


class NativeOAuthMiddleware(Middleware):
    """Refresh linked-user status on each authenticated MCP request."""

    async def on_request(self, context, call_next):
        service = DomainService()
        try:
            principal = await call_sync_fn_in_threadpool(resolve_principal, get_access_token(), service)
        finally:
            service.close()
        token = mcp_auth_context.set(principal)
        try:
            return await call_next(context)
        finally:
            mcp_auth_context.reset(token)
