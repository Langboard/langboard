# Native OAuth HTTP regression gates

`tests/mcp_integration/test_native_oauth_http.py` exercises the actual native
OAuth HTTP application with signed Bearer requests. It covers native reference
token validation, stored token lookup, signed upstream ID-token validation,
required `mcp:access` scope, linked identity resolution and current user state.

An isolated OIDC configuration and memory storage replace network discovery and
production storage. Fixture signatures are verified by FastMCP's JWTVerifier;
`load_access_token`, token verification and native middleware are not mocked.
The tests populate FastMCP's internal token stores to model a completed login.
This intentionally couples the fixture to the pinned FastMCP storage models;
an upgrade must revalidate the fixture against its real authorization flow.

The domain handler is an isolated probe and the identity repository is a fixture.
No production credentials or account changes are used. These tests establish
the HTTP authentication boundary, not real IdP login, consent, refresh,
durable storage, replica concurrency, database ACL or deployed connectivity.

The cases include invalid native signatures/audiences, missing scope, invalid
upstream issuer/audience/expiry, missing token mapping, unlinked subject,
inactive/deleted users and revocation between requests using the same token.
Denied requests must never reach the probe; token/scope failures must never
reach identity lookup. Successful requests need no legacy ToolGroup.

The isolated provider replaces `Server.create_oauth_provider`, the imported
factory used when constructing the HTTP application. Replacing only the
definition in `OAuth` leaves that import unchanged and accidentally reads the
process environment. Correcting this fixture restored all twelve HTTP cases;
the production HTTPS and credential configuration checks remain unchanged.

The 2026-10-18 source audit ran the native provider, refresh, durable-storage,
readiness, signed HTTP and employee-proof suites on pinned FastMCP 4.0.10.
Sixty-six cases passed; twelve HTTP cases initially failed at app construction
because of that fixture target. Those twelve passed after the correction.
These are separate runs, not a claim of 78 cases passing in one combined run.
The failed and corrected logs are retained in local verification evidence.

The regression reproduced a scope expansion: an upstream token's broader
scope list could allow `mcp:access` even when the native client token granted
only `openid`. `LangboardOIDCProxy` retains FastMCP token validation and storage,
then intersects the verified client grant with upstream scopes. Scope middleware
runs before linked identity lookup. Neither layer grants domain permissions.
