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
