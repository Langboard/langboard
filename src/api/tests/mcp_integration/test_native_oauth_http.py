"""Signed Bearer requests exercise native OAuth HTTP gates, without a live IdP."""

import json
import time
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from fastmcp.server.auth.oauth_proxy.models import JTIMapping, UpstreamTokenSet
from fastmcp.server.auth.oidc_proxy import OIDCConfiguration, OIDCProxy
from fastmcp.server.auth.providers.jwt import JWTVerifier
from joserfc import jwk, jwt
from key_value.aio.stores.memory import MemoryStore
from langboard.mcp_integration import OAuth, Server
from langboard.mcp_integration.Tool import McpTool
from langboard.middlewares.McpAuthMiddleware import mcp_auth_context
from starlette.testclient import TestClient
from test_native_board_consent import native, native_engine  # noqa: F401


@pytest.mark.parametrize(
    "failure",
    [
        None,
        "signature",
        "audience",
        "scope",
        "issuer",
        "subject",
        "inactive",
        "deleted",
        "expired",
        "upstream_audience",
        "missing_mapping",
        "native_scope",
    ],
)
async def test_signed_bearer_http_checks_identity_scope_and_revocation(monkeypatch, failure):
    configuration = OIDCConfiguration(
        issuer="https://id.example",
        authorization_endpoint="https://id.example/authorize",
        token_endpoint="https://id.example/token",
        jwks_uri="https://id.example/jwks",
        response_types_supported=["code"],
        subject_types_supported=["public"],
        id_token_signing_alg_values_supported=["HS256"],
    )
    monkeypatch.setattr(OIDCProxy, "get_oidc_configuration", lambda *args, **kwargs: configuration)
    upstream_key = "isolated-test-upstream-key-at-least-32-characters"
    provider = OAuth.LangboardOIDCProxy(
        config_url="https://id.example/.well-known/openid-configuration",
        client_id="native-fixture",
        client_secret="isolated-test-client-secret",
        base_url="https://board.example/mcp/oauth",
        jwt_signing_key="isolated-test-native-signing-key-at-least-32-characters",
        token_verifier=JWTVerifier(
            public_key=upstream_key, algorithm="HS256", issuer="https://id.example", audience="native-fixture"
        ),
        verify_id_token=True,
        valid_scopes=["openid", "mcp:access"],
        client_storage=MemoryStore(),
    )
    provider.get_routes(mcp_path="/stream")
    now = time.time()
    upstream_claims = {
        "iss": "https://other.example" if failure == "issuer" else "https://id.example",
        "aud": "foreign-client" if failure == "upstream_audience" else "native-fixture",
        "sub": "unlinked-sub" if failure == "subject" else "linked-sub",
        "iat": int(now),
        "exp": int(now) - 3600 if failure == "expired" else int(now) + 3600,
    }
    id_token = jwt.encode({"alg": "HS256"}, upstream_claims, jwk.OctKey.import_key(upstream_key))
    scopes = ["openid"] if failure == "scope" else ["openid", "mcp:access"]
    await provider._upstream_token_store.put(
        key="upstream-fixture",
        value=UpstreamTokenSet(
            upstream_token_id="upstream-fixture",
            access_token="isolated-upstream-access-token",
            refresh_token=None,
            refresh_token_expires_at=None,
            expires_at=now + 3600,
            token_type="Bearer",
            scope=" ".join(scopes),
            client_id="native-client",
            created_at=now,
            raw_token_data={"id_token": id_token},
        ),
    )
    await provider._jti_mapping_store.put(
        key="native-fixture-jti",
        value=JTIMapping(
            jti="native-fixture-jti",
            upstream_token_id="upstream-fixture",
            created_at=now,
        ),
    )
    if failure == "missing_mapping":
        await provider._jti_mapping_store.delete(key="native-fixture-jti")
    bearer = provider.jwt_issuer.issue_access_token(
        client_id="native-client", scopes=["openid"] if failure == "native_scope" else scopes, jti="native-fixture-jti"
    )
    if failure in {"signature", "audience"}:
        from fastmcp.server.auth.jwt_issuer import JWTIssuer

        alternate = JWTIssuer(
            issuer=provider.jwt_issuer.issuer,
            audience="https://foreign.example/stream" if failure == "audience" else provider.jwt_issuer.audience,
            signing_key=b"untrusted-test-signing-key-at-least-32-characters"
            if failure == "signature"
            else provider.jwt_issuer._signing_key,
        )
        bearer = alternate.issue_access_token(client_id="native-client", scopes=scopes, jti="native-fixture-jti")

    user = SimpleNamespace(
        activated_at=None if failure == "inactive" else object(), deleted_at=object() if failure == "deleted" else None
    )
    lookup = Mock(
        side_effect=lambda provider, subject, issuer, *, consistent: user
        if consistent and subject == "linked-sub" and issuer == "https://id.example"
        else None
    )
    service = SimpleNamespace(identity_link=SimpleNamespace(get_user_by_provider_external_id=lookup), close=Mock())
    monkeypatch.setattr(OAuth, "DomainService", lambda: service)
    monkeypatch.setattr(Server, "create_oauth_provider", lambda: provider)
    from langboard_shared.domain.services import NativeMcpConsent
    connection_calls = []

    def connect(actor, client_id):
        assert actor is user and client_id == "native-client"
        connection_calls.append(client_id)
        return SimpleNamespace(id=19)

    monkeypatch.setattr(NativeMcpConsent, "connect_native_client", connect)
    calls = []

    async def probe() -> dict:
        """Record a command reaching the authenticated native domain boundary."""
        principal = mcp_auth_context.get()
        assert principal["user_or_bot"] is user and principal["transport"] == "oauth"
        assert "tool_group" not in principal
        calls.append(True)
        return {"applied": True}

    monkeypatch.setattr(McpTool, "get_tools", lambda: {"fixture_probe": {"handler": probe, "description": "Probe"}})
    monkeypatch.setattr(McpTool, "get_tool", lambda name: None)
    monkeypatch.setattr(Server.McpServer, "_wrap_tool", lambda name, handler: handler)
    before = mcp_auth_context.get()
    monkeypatch.setattr(type(Server.Env), "MCP_OAUTH_ENABLED", property(lambda _: True))
    app = Server.McpServer.get_oauth_http_app()
    with TestClient(app, base_url="https://board.example") as client:
        response = client.post(
            "/stream",
            headers={"Authorization": f"Bearer {bearer}", "Accept": "application/json, text/event-stream"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "fixture_probe", "arguments": {}},
            },
        )
        if failure is None:
            assert response.status_code == 200
            payload = json.loads(next(line[6:] for line in response.text.splitlines() if line.startswith("data: ")))
            assert payload["result"]["structuredContent"] == {"applied": True}
            assert calls == [True]
            assert connection_calls == ["native-client"]
            assert lookup.call_args.args[1:] == ("linked-sub", "https://id.example")
            user.activated_at = None
            revoked = client.post(
                "/stream",
                headers={"Authorization": f"Bearer {bearer}", "Accept": "application/json, text/event-stream"},
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "fixture_probe", "arguments": {}},
                },
            )
            assert calls == [True]
            assert revoked.status_code == 200
            denial = json.loads(next(line[6:] for line in revoked.text.splitlines() if line.startswith("data: ")))
            assert "error" in denial or denial["result"]["isError"] is True
        else:
            assert calls == []
            if failure in {
                "signature",
                "audience",
                "scope",
                "issuer",
                "expired",
                "upstream_audience",
                "missing_mapping",
                "native_scope",
            }:
                lookup.assert_not_called()
            assert response.status_code in {200, 401, 403}
            if response.status_code == 200:
                payload = json.loads(next(line[6:] for line in response.text.splitlines() if line.startswith("data: ")))
                assert "error" in payload or payload["result"]["isError"] is True
    assert mcp_auth_context.get() is before


@pytest.mark.parametrize("name,offset", [("get_card", 200), ("read_wiki_content", 400)])
@pytest.mark.parametrize("revocation", ["consent", "membership", "inactive_user", "deleted_user"])
async def test_signed_http_actual_card_and_wiki_persisted_board_boundary(
    native, monkeypatch, tmp_path, revocation, name, offset,  # noqa: F811 - imported pytest fixture
):
    """A single signed bearer rechecks current persisted grants and resource ownership."""
    from langboard.mcp_tools.CardMcp import get_card
    from langboard.mcp_tools.WikiMcp import read_wiki_content
    from langboard_shared.core.caching import Cache
    from langboard_shared.core.caching.InMemoryCache import InMemoryCache
    from langboard_shared.core.db import EditorContentModel
    from langboard_shared.domain.models import (
        CardExecutionGeneration,
        ProjectAssignedUser,
        ProjectWiki,
        User,
        UserIdentityLink,
    )
    from langboard_shared.domain.models.UserIdentityLink import IdentityProvider
    from langboard_shared.domain.services.NativeMcpConsent import (
        connect_native_client,
        list_native_connections,
        read_native_board_consent,
        save_native_board_consent,
    )
    from sqlalchemy import delete, update
    from sqlalchemy.orm import Session

    engine, user, _, boards = native
    # Full production card details read related native tables. Persist empty tables,
    # rather than replacing a service, permission resolver, or handler.
    ProjectWiki.metadata.create_all(engine)
    CardExecutionGeneration.__table__.create(engine)
    monkeypatch.setattr(type(Server.Env), "CACHE_DIR", property(lambda _: tmp_path / "cache"))
    monkeypatch.setattr(Cache, "_cache", InMemoryCache())
    with Session(engine) as db:
        db.add(UserIdentityLink(id=900, user_id=user.id, provider=IdentityProvider.Oidc,
                                external_id="linked-sub", issuer="https://id.example"))
        for board_id in (10, 20):
            db.execute(update(ProjectWiki).where(ProjectWiki.id == board_id + 400).values(
                content=EditorContentModel(content=f"Only board {board_id} wiki body"),
            ))
        db.commit()
    connection = connect_native_client(user, "native-client")
    save_native_board_consent(user, connection.id, 10, ["read"], None)
    configuration = OIDCConfiguration(
        issuer="https://id.example",
        authorization_endpoint="https://id.example/authorize",
        token_endpoint="https://id.example/token",
        jwks_uri="https://id.example/jwks",
        response_types_supported=["code"],
        subject_types_supported=["public"],
        id_token_signing_alg_values_supported=["HS256"],
    )
    monkeypatch.setattr(OIDCProxy, "get_oidc_configuration", lambda *args, **kwargs: configuration)
    upstream_key = "isolated-test-upstream-key-at-least-32-characters"
    provider = OAuth.LangboardOIDCProxy(
        config_url="https://id.example/.well-known/openid-configuration",
        client_id="native-fixture",
        client_secret="isolated-test-client-secret",
        base_url="https://board.example/mcp/oauth",
        jwt_signing_key="isolated-test-native-signing-key-at-least-32-characters",
        token_verifier=JWTVerifier(
            public_key=upstream_key, algorithm="HS256", issuer="https://id.example", audience="native-fixture"
        ),
        verify_id_token=True,
        valid_scopes=["openid", "mcp:access"],
        client_storage=MemoryStore(),
    )
    provider.get_routes(mcp_path="/stream")
    now = time.time()
    upstream_claims = {
        "iss": "https://id.example",
        "aud": "native-fixture",
        "sub": "linked-sub",
        "iat": int(now),
        "exp": int(now) + 3600,
    }
    id_token = jwt.encode({"alg": "HS256"}, upstream_claims, jwk.OctKey.import_key(upstream_key))
    scopes = ["openid", "mcp:access"]
    await provider._upstream_token_store.put(
        key="upstream-fixture",
        value=UpstreamTokenSet(
            upstream_token_id="upstream-fixture",
            access_token="isolated-upstream-access-token",
            refresh_token=None,
            refresh_token_expires_at=None,
            expires_at=now + 3600,
            token_type="Bearer",
            scope=" ".join(scopes),
            client_id="native-client",
            created_at=now,
            raw_token_data={"id_token": id_token},
        ),
    )
    await provider._jti_mapping_store.put(
        key="native-fixture-jti",
        value=JTIMapping(
            jti="native-fixture-jti",
            upstream_token_id="upstream-fixture",
            created_at=now,
        ),
    )
    bearer = provider.jwt_issuer.issue_access_token(
        client_id="native-client", scopes=scopes, jti="native-fixture-jti"
    )
    monkeypatch.setattr(Server, "create_oauth_provider", lambda: provider)
    monkeypatch.setattr(type(Server.Env), "MCP_OAUTH_ENABLED", property(lambda _: True))
    monkeypatch.setattr(type(Server.Env), "MCP_ALLOWED_HOSTS", property(lambda _: ["board.example"]))
    metadata = {name: McpTool.get_tool(name) for name in ("get_card", "read_wiki_content")}
    assert metadata["get_card"]["handler"] is get_card
    assert metadata["read_wiki_content"]["handler"] is read_wiki_content
    monkeypatch.setattr(McpTool, "get_tools", lambda: metadata)
    before = mcp_auth_context.get()
    app = Server.McpServer.get_oauth_http_app()
    with TestClient(app, base_url="https://board.example") as client:
        request_id = 0

        def call(name, project_uid, resource_uid, *, allowed):
            nonlocal request_id
            request_id += 1
            arguments = {"project_uid": project_uid,
                         "card_uid" if name == "get_card" else "wiki_uid": resource_uid}
            # get_card is an extended production command, reached through the native raw dispatcher.
            params = ({"name": "call_raw_tool", "arguments": {"name": name, "arguments": arguments}}
                      if name == "get_card" else {"name": name, "arguments": arguments})
            response = client.post("/stream", headers={
                "Authorization": f"Bearer {bearer}", "Accept": "application/json, text/event-stream",
            }, json={"jsonrpc": "2.0", "id": request_id, "method": "tools/call", "params": params})
            assert response.status_code == 200, response.text
            payload = json.loads(next(line[6:] for line in response.text.splitlines() if line.startswith("data: ")))
            assert payload["id"] == request_id
            if allowed:
                assert "error" not in payload, payload
                assert payload["result"].get("isError") is not True, payload
                board_id = 10 if project_uid == boards[0].get_uid() else 20
                expected = f"Card {board_id}" if name == "get_card" else f"Only board {board_id} wiki body"
                assert expected in json.dumps(payload), payload
            else:
                assert "error" in payload or payload["result"].get("isError") is True, payload
                for hidden in ("Card 10", "Card 20", "Only board 10 wiki body", "Only board 20 wiki body"):
                    assert hidden not in json.dumps(payload), payload
            return payload

        from langboard_shared.core.types import SnowflakeID
        call(name, boards[0].get_uid(), SnowflakeID(10 + offset).to_short_code(), allowed=True)
        call(name, boards[1].get_uid(), SnowflakeID(20 + offset).to_short_code(), allowed=False)
        call(name, boards[0].get_uid(), SnowflakeID(20 + offset).to_short_code(), allowed=False)

        # Incremental board consent preserves exactly one connection and the same bearer.
        save_native_board_consent(user, connection.id, 20, ["read"], None)
        call(name, boards[1].get_uid(), SnowflakeID(20 + offset).to_short_code(), allowed=True)
        # Both boards are now consented; a forged project/resource pair still fails.
        call(name, boards[0].get_uid(), SnowflakeID(20 + offset).to_short_code(), allowed=False)

        # Revoke B after granting A and B; the same bearer must preserve A.
        if revocation == "consent":
            grant = read_native_board_consent(user, connection.id, 20)
            save_native_board_consent(user, connection.id, 20, [], grant["revision"])
        else:
            from langboard_shared.core.types import SafeDateTime

            with Session(engine) as db:
                if revocation == "membership":
                    db.execute(delete(ProjectAssignedUser).where(
                        ProjectAssignedUser.project_id == 20, ProjectAssignedUser.user_id == user.id,
                    ))
                else:
                    values = ({"activated_at": None} if revocation == "inactive_user"
                              else {"deleted_at": SafeDateTime.now()})
                    db.execute(update(User).where(User.id == user.id).values(**values))
                db.commit()
        user_revoked = revocation in {"inactive_user", "deleted_user"}
        call(name, boards[0].get_uid(), SnowflakeID(10 + offset).to_short_code(), allowed=not user_revoked)
        call(name, boards[1].get_uid(), SnowflakeID(20 + offset).to_short_code(), allowed=False)
        if not user_revoked:
            connections = list_native_connections(user)
            assert connections == [{"uid": connection.get_uid(), "client_id": "native-client"}]
    assert mcp_auth_context.get() is before
