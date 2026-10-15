# Graph service authentication

This change is based on main `45171c109`, which owns the Phoenix socket runtime.
It does not restore Node socket code, Compose configuration, Docker stages or
workflows from older integration branches.

Graph `/api/v1/graph/` endpoints require backend authentication before route
execution. Python bot and approval callers issue a five-minute JWT with the
instance issuer, `langboard-graph` audience and `graph_service` kind. Phoenix
Graph streaming and resume clients reuse their existing
`SOCKET_PHOENIX_INTERNAL_SECRET` contract. Graph accepts that secret only when it
is configured with at least 32 characters, using a constant-time comparison.
An empty or incorrect value fails closed. Ordinary user/API tokens are not
Graph service credentials. Health probes remain outside this protected prefix.

Service authentication is not user authorization. Python run/approval claims
still own per-user/project decisions; callers must not turn service authority
into arbitrary user authority. Deploy Graph and its API/socket callers together.
Graph with an older caller returns 401. No configuration value or secret is
embedded in source, and no new Phoenix JWT dependency is needed.

Authentication adds no database or network round trip. The service secret
comparison and optional JWT check run once per Graph HTTP request. A prior
local JWT-only ASGI benchmark showed about 29.54 microseconds of added median
latency; that result is not a measurement of this Phoenix deployment.

Validation on the exact main-based Python lock environment:

- 49 ASGI tests pass in 10.31 seconds, covering invalid/expired/mis-scoped JWTs
  over run/webhook/resume/status/delete, valid JWT status, and correct/incorrect/
  missing Phoenix secret status.
- Ruff and diff checks pass.
- Phoenix compilation and streaming/resume HTTP integration remain unverified:
  the current local host does not provide Elixir/Mix. This PR remains Draft.
- No merge or deployment has been performed. Older PR 681 and the local canary
  union remain preserved and are not assumed compatible with current main.

## Signed chat history on current main

The independent chat-history authority guard is now reconstructed on current
main. Signed user identity, active primary account and current ChatSession
ownership are required before any history is returned. An anchor-only request
is scoped to its actual session rather than every session. Invalid, expired,
wrong-kind and out-of-range subjects fail closed. Combined authentication and
SQLite/PostgreSQL chat fixtures pass 83 tests in 16.26 seconds. Card visibility
and app/runtime checkpoint changes are not copied without their missing domain
foundation; see `phoenix-app-reconstruction.md`.


## Board history query budget

Comment and activity history now require the signed user, an active primary
account, current board membership and a read role (or board ownership). The
checks are included in each bounded content SELECT; the separate card-board
preflight query is removed. Each comment/activity collector performs one
SELECT, including denied valid-user cases. Missing credentials and bot actors
perform no database queries. This is a round-trip budget, not a deployed
latency benchmark. Deleted comments return tombstones without their body.
SQLite and disposable PostgreSQL tests exercise revocation and cross-board
requests against current main. CardVisibility and app principal policy remain
pending reconstruction; these tests do not claim those missing contracts.

The API bot-draft caller now uses the shared service credentials too. Tests import the production API module and check its HTTP request token,
then route that request through the real local Graph ASGI middleware/route.
Only external model inference is replaced by a fixture. Live deployment,
real provider inference and Phoenix compilation remain pending.

Combined regression: 128 tests pass in 37.72 seconds on SQLite and disposable
PostgreSQL. Ruff and `git diff --check` pass.


## PostgreSQL connection consistency

Graph now recognizes PostgreSQL dialects through the existing ORM database
helper, then converts the scheme to psycopg's native `postgresql://` form.
The authority, host, database and query parameters are preserved verbatim;
credentials are not decoded or logged. Checkpoint setup, its reusable pool,
thread lock pool and background concurrency slot use this same conversion.
Previously `postgresql+psycopg://` was accepted by the ORM but did not enable
Graph checkpoint storage and was invalid in direct psycopg connections.

This adds no database calls and does not resize pools. The integration test
uses disposable PostgreSQL to interrupt a native LangGraph graph, close its
pool, recreate the pool and resume from the persisted checkpoint. It also
exercises thread locks and the actual background slot acquisition/release
with `postgresql://`, `postgres://` and `postgresql+psycopg://` settings.
SQLite is not used as evidence for this persistence or locking behavior.
Official connection format: https://www.psycopg.org/psycopg3/docs/api/connections.html

Combined suite: 132 tests pass in 36.37 seconds; the new durable checkpoint
and lock cases run only against real disposable PostgreSQL. Ruff and diff
checks pass. Deployment and Phoenix runtime validation remain pending.


## Bot draft import and local HTTP integration review

The AST-extracted helper test has been removed. API-side integration tests now
import BotSettingsApi normally, preserving its native dependency/decorator
registration. A local transport connects the imported helper to the Graph
FastAPI application, GraphServiceAuthMiddleware and BotDraftApi. Valid service
credentials return a parsed generated draft; missing/invalid credentials return
401 before the fixture model is invoked. Routine imports are at module scope.
Graph-only tests do not import the API module or add FastMCP as a Graph runtime
dependency. API integration tests require both existing runtime dependencies.

Validation uses the frozen root API lock (FastMCP 4.0.0b3), plus the separately
locked Graph dependencies for the integration route. Starlette 1.6 TestClient
returns httpx2 responses; the test transport wraps them as production httpx
responses so failures exercise the caller's real HTTPError handling. This is
local ASGI integration, not a deployed network/provider or Phoenix check.

Combined service-auth and API integration regression: 51 tests pass in
2.88 seconds. Ruff and diff checks pass.
