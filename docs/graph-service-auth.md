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

The API bot-draft caller now uses the shared service credentials too. A focused
test executes the actual helper and checks one HTTP request with a valid
service token. Full API-to-Graph draft generation and Phoenix compilation
remain pending runtime validation.

Combined regression: 128 tests pass in 37.72 seconds on SQLite and disposable
PostgreSQL. Ruff and `git diff --check` pass.
