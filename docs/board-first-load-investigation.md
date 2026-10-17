# Canary board first-load investigation

## Observed scope

On 2026-10-11, an authenticated in-app browser repeatedly loaded a board and displayed the native board-load error. A prior manual retry
displayed the board, but reloading reproduced the failure. This does not prove
the same behavior in other browsers or establish a backend outage.

The deployed JavaScript asset `index-CHcBG7Il.js` contains `Session changed`,
`_langboardAuthReplayed`, and `getSessionVersion`. The authentication guards
are present in the deployed bundle; these markers do not establish the full
deployed Git revision.

## HTTP evidence and correction

A fresh CDP Network capture was paged without buffer truncation. Cookie refresh,
identity hydration, and the board snapshot returned 200. The card GET remained
an original XHR that ended with `net::ERR_ABORTED`, `canceled: true`. The same
load also canceled active-work, approval-count, notification and dock XHRs.

The second request for the card URL was **OPTIONS**, with a `preflight` initiator
pointing to the original GET. Its 200 is not a successful card GET. Earlier
observations that interpreted this response as successful card retrieval must
not be used as acceptance evidence.

The observed preflight permits the exact UI origin, credentials, GET, and the
Authorization request header. This rules out an obvious missing allow header
in that response, but does not establish the cause of the subsequent abort.
The Cloudflare beacon DNS failure is a separate request and does not prove
the card endpoint failed DNS resolution.

## Remaining verification

Trace the original XHR's termination before changing application retry policy.
Distinguish browser/transport cancellation from an application cancellation,
session rotation, HTTP error, and snapshot hydration failure. Capture the GET
response or its failure on the original request ID, not the OPTIONS ID.

The local authentication transport suite passed 16 cases before this capture,
including obsolete-session responses, concurrent refresh, terminal refresh
failure, and real transport abort isolation. Those cases do not reproduce this
deployed first-load failure. No unconditional retry has been added: replaying
an obsolete-session request would weaken the existing session boundary.

Completion requires authenticated deployed first-load and reload acceptance,
including cookie refresh and socket recovery. This investigation leaves that
gate open.


## Settings follow-up

A later settings capture observed the original board GET return 200, then its
`/details` GET abort about 55 ms after dispatch. Dock and approval-count XHRs
aborted in the same interval. A fresh Page-event capture showed no new frame
navigation/detachment event at that cancellation interval. This narrows the
observed failure beyond a settings render-only defect but does not establish
whether the browser, transport, or application initiated cancellation.

Another reload failed earlier: refresh and identity returned 200, while the
original board GET aborted. No details request was dispatched on that load.
Do not treat a successful OPTIONS response as success of its original GET.
The board settings source now renders an explicit failed-load retry, but its
canary recovery remains unverified until deployment.


## Later authenticated settings readback

A subsequent in-app browser reload returned HTTP 200 for the original
`/auth/refresh`, `/auth/me`, `/board/{project_uid}`, `/details`, `/columns` and
`/column/dock` requests. The full settings screen rendered and its GlitchTip
connection selector showed only the new-connection option. The deployed screen
still used the old combined settings layout and UI asset `index-CHcBG7Il.js`.
This is one successful reload after prior failed loads, not proof that intermittent
cancellation is fixed or that the new Store/settings commits are deployed.
The central Langboard project view still showed only the earlier isolated SDK
acceptance issue with one event; actual canary event-to-board acceptance remains open.

## Fresh capture during the next immutable build

A 2026-10-11 authenticated reload reproduced the partial settings shell while
the immutable API image was still building. The original refresh
POST, identity GET and board GET returned 200. The original details GET,
dock GET and approval-count GET
ended with `net::ERR_ABORTED`, `canceled: true`, after their separate OPTIONS
requests returned 200. The capture had neither truncation nor remaining pages.
The deployed asset remained `index-CHcBG7Il.js`. Browser console reported Axios
`Request aborted`; this is not evidence that the session guard caused the abort.
The observed API origin matched the configured deployment API origin. No
credential material was read. Instance addresses, board identities and exact
request IDs are retained in private verification records, not product guidance. Current source auth transport regression passed 16 cases;
that local result does not resolve this deployed reproduction.


## Recent-card history follow-up

A later authenticated canary retry reached the original card GET with HTTP200
and rendered the detail description, native checklists and persisted summary
comment. An XHR-send debugger breakpoint was briefly enabled and removed before
the final observation. This single successful retry under the debugger does not
establish that the intermittent cancellation is fixed. Later unrelated board
availability, approval and read-state requests still aborted. A subsequent normal
close/reopen without an active debugger also returned HTTP200 for its original
card GET and rendered the comment composer. These two successes narrow the
problem to intermittent behavior rather than a persistent card ACL rejection.

The browser capture also showed availability requests for multiple other boards.
Source inspection traced these to recent-card validation in the mounted Explorer,
even while its context panel was hidden. The section passed its entire history
to validation while displaying only ten entries. This is confirmed unnecessary
background work; it is not proven to cause the card GET cancellation.

Explorer now passes its actual desktop/mobile visibility to recent-card
validation. A hidden panel or collapsed recent-card section attaches no focus or
online validation listeners and dispatches no history lookup. An open section
validates the displayed ten cards; expanding older history validates those entries
on demand. The existing serial batches, explicit permission checks, confirmed
403/deletion pruning, transient-error retention and cancellation cleanup remain.
No periodic reconciliation, endpoint or dependency was added.

Chromium regression covers 1280/700/390px layouts, permission loss, offline/online
recovery and a fourteen-board history: hidden zero requests, ten visible cards,
older-history expansion and collapsed focus/online inactivity. These local
results require separate deployed canary acceptance.

## MCP bundle read consolidation

The native bundle adapter previously loaded the project and card through repository
lookups before calling `CardService.resolve_readable_card`. That shared visibility
owner already reloads current primary rows and validates deletion, ancestry,
membership and channel visibility. Pass the requested IDs directly to it, removing
two redundant preliminary reads and avoiding dependence on an older replica row.
All optional bundle sections still load only after the owner authorizes the card.
This reduces source-level query work; it does not establish a deployed latency gain
or fix the browser cancellation above.

An additional authenticated canary capture observed the original cards XHR cancel
with `net::ERR_ABORTED` after 154ms while unrelated availability calls returned
200. No CORS error or HTTP 500 was present in that failure event. The cause remains
unproven; neither a 15-second timeout nor successful MCP writes explains this
specific cancellation. Preserve it as a separate browser acceptance failure.

## Telemetry visibility and fresh navigation

The configured browser telemetry ingestion project was not present in the
current observer's accessible project list. The organization selector exposed
only one accessible organization. A successful envelope HTTP response therefore
does not establish that this observer can retrieve the corresponding event.
Check project existence, key-to-project mapping and observer access separately;
absence from this list does not prove project deletion. Infrastructure ownership
retains this investigation. Do not create a second project or change credentials
to work around missing evidence.

A later full navigation reproduced cancellation before card loading: identity
hydration and the board's primary GET reported `net::ERR_ABORTED` with
`canceled=true`, without an HTTP error response or a reported CORS block. A
separate third-party analytics script failed DNS resolution; that script failure
does not establish an API DNS failure. Preserve gateway/API arrival evidence
before attributing these cancellations to application authorization or CPU
pressure. Previously rendered in-memory cards are not fresh-navigation proof.

## Server error payload boundary

An existing error event exposed a tool request body and database breadcrumbs.
`send_default_pii=False` and disabled local-variable collection did not remove
those fields. The shared logger now supplies an explicit `before_send` allowlist
to the installed Sentry SDK. It retains exception types, module/function/line
coordinates, release/environment and valid trace/span identifiers. It excludes
requests, users, breadcrumbs, arbitrary scope data, exception text, log arguments,
source context and local variables.

The tool dispatcher supplies request ID, tool name and cause type as explicit
diagnostics. Log-only tool errors group by tool and cause instead of their
redacted message. Existing structured stdout records and mutation receipts remain
unchanged. An in-memory official SDK transport verifies the final error envelope
without sending customer content to a telemetry service. This is source evidence;
the deployed server and browser still require separate acceptance.

### Optional performance transactions

`SENTRY_TRACES_SAMPLE_RATE` accepts a value from `0` to `1` and defaults to
`0`. Invalid, non-finite and out-of-range values disable transaction sampling.
The local rate also applies to incoming sampled traces; an upstream sampling
decision cannot enable collection on an instance that has not opted in.

The official SDK transaction hook retains correlation identifiers, timestamps
and a bounded span category (`db`, `http`, `cache`, `middleware`, `queue`,
`function`, or `other`). The transaction name is constant. Request paths,
SQL text, span data, users and application payloads are omitted from the final
envelope. This intentionally limits diagnosis to timing and category; it does
not provide statement-level query attribution. Existing fixed server timing
metrics provide request-stage detail independently.

In-memory SDK tests cover disabled collection, incoming sampling decisions and
the serialized transaction envelope. An operator must explicitly configure a
rate after deploying the new logger. Source tests do not prove that the running
release collects transactions or that the telemetry backend received them.


## Correlated server delay and bounded stage timings (2026-10-17)

A later authenticated canary load returned the original card XHR with HTTP 200
and a matching `Network.loadingFinished`: response headers arrived in 6,386.934 ms,
including `Server-Timing: app;dur=6202.576`. The matching API telemetry record
reported 6,205.817 ms overall, 435,206 response-body bytes and `completed=true`.
The board metadata response separately reported 174.133 ms of application time.
Raw request/trace correlation is retained privately and supplied to the central
observability owner. The event buffer was truncated, so this supports those
matched responses only, not a complete waterfall or percentile result.

The source card route now emits five fixed `Server-Timing` metrics:
`board_context`, `board_cards`, `board_checklists`, `board_bots`, and
`board_serialize`. They separate authorized context/column preparation, card
projection, checklist projection, bot configuration and response serialization.
They omit resource IDs, actor identity, payloads, connection details and query
text. Existing middleware app timing, request correlation and CORS exposure remain
unchanged; no tracing backend, sampling expansion or new dependency is added.
These metrics are only attached after the existing authorized route succeeds.
See the [standard header documentation](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Server-Timing).

The route regression failed first because the stage header was absent, then
passed with a deterministic clock while preserving the shared archive cutoff and
response body. The combined archive, actor-visibility and runtime telemetry suite
passed 32 cases, including streamed body preservation and safe correlation.
These source stages are not deployed; the six-second sample cannot yet be
attributed to an individual stage. Read-only aggregate inspection also found no
AI-description bytes in this board's 294 hot cards, so deferring that field alone
cannot explain this sample. Their stored description JSON totaled 1,128,146 bytes;
that is neither wire size nor proof of the latency cause.

### Concurrent database observation

A subsequent authenticated original cards XHR completed with HTTP 200,
`app;dur=5579.045` and response headers at 5,917.638 ms. A read-only
`pg_stat_activity` observation window fully bracketed its dispatch-to-headers
interval. Of 200 samples across 41.837 seconds, 29 overlapped this request;
three observed active statements, with maximum observed statement age 188.388 ms
and no reported wait-event type. These statements are not request-correlated.
The 200 ms sampling interval can miss short statements and cannot measure pool
checkout wait or application CPU. This does not rule out cumulative SQL cost or
database connection contention. Earlier windows ended before the response and
are retained as incomplete measurements, not equivalent acceptance evidence.

The initial browser event buffer was truncated, but the original request's
dispatch, response and loading completion were all retained. Only that matched
request is accepted; this is not a complete navigation waterfall or percentile.

Separate cgroup observation found two effective CPUs, no CFS quota, approximately
97.7% utilization of those two CPUs over one measured second, and CPU pressure
`some avg10=93.78`. Host-owner historical evidence also failed the agreed CPU/RAM
headroom condition. These are capacity-contention evidence, not an attribution
of every millisecond to CPU. The central infrastructure owner is handling the
placement/capacity remedy; the product does not silently change broker topology,
host allocation, database settings, or volumes to mask the symptom. The route
stage timings above still require deployment before application stages can be
compared under the same runtime conditions.

### Recovery process startup

The document cleanup recovery script now invokes its existing task module with
`python -m langboard_shared.tasks.docling.DocumentCleanupTask`, matching the
direct module entry points used by other recovery jobs. It retains the existing
`flock` path, nonblocking duplicate behavior, one-minute schedule and bounded
cleanup query. It does not load the full CLI command catalog or DomainService.
The operator CLI command remains available. An isolated database regression
executes the module entry point and verifies that a committed cleanup request
is dispatched and remains durable until its worker acknowledges it.

A local fresh-process observation loaded 2,052 modules through CLI discovery
versus 1,255 through the cleanup module. Single samples took 2.649 and 1.759
seconds respectively; user CPU was 1.604 and 1.355 seconds. These observations
identify avoidable imports, not production latency savings or proof that this
one recovery job explains host CPU contention. Compare deployed process CPU
and authenticated request timings before claiming an operational improvement.

### Identity lookup during a cache outage

The native user and bot lookup paths previously returned `None` when the
identity database lookup succeeded but the subsequent cache write failed.
Cache population is now best effort after a successful database lookup.
A missing user still returns an invalid-token error; a missing bot or a database
failure still returns no identity. Token verification, cache TTLs and revocation
paths are unchanged. Regression coverage reproduces both valid-identity
failures before the fix and verifies missing-identity/database-error denial.

This source defect was independently confirmed in the running API image during
the Redis adoption investigation. It does not establish the exact cause of an
individual browser logout or OIDC rejection without correlated request logs.
The browser session and MCP connection must be verified separately after release.

OIDC discovery and JWKS use the same optional-cache boundary: cache read errors
fall back to fetching the current provider metadata, and cache write errors do
not discard a successful provider response. No local metadata store, retry loop
or secondary cache is introduced. Provider request failures still propagate;
JWT signature, issuer, audience, time and nonce checks remain in the existing
validation path. A real RSA-signed JWT regression covers cache read/write
outages, incorrect audience, incorrect signature and provider failure. The
pre-change module fails both outage scenarios; the corrected implementation
passes them together with the existing identity/self-link tests (14 tests).
This does not make a Keycloak gateway timeout recoverable inside Langboard.

### Reproduce the authentication outage regressions

Run from the repository root in a Python 3.12 or 3.13 environment containing the
locked project and development dependencies (`uv sync --locked --group dev`,
then activate `.venv`). The focused tests use in-process fixtures and synthetic
identities/keys; no production database, Redis instance, IdP credentials or
network access is required.

```sh
PROJECT_NAME=langboard PYTHONPATH=src/api:src/shared/py python -m pytest \
  src/api/tests/runtime/test_auth_cache_outage.py \
  src/api/tests/runtime/test_socket_auth.py \
  src/api/tests/identity/test_oidc_cache_outage.py \
  src/api/tests/identity/test_oidc_identity.py \
  src/api/tests/identity/test_oidc_self_link.py -q
```

Expected: 106 tests pass with no skips. Existing short synthetic HMAC fixtures
may emit key-length warnings; they are not deployment failures. A missing
`pytest` module means the development environment has not been installed or
activated, not that a regression test failed.

The six native identity cases require a successful database identity to survive
cache read/write failure, while a missing identity or database error remains
denied. The two signed OIDC cases require metadata fetched from the configured
provider to remain usable during cache read/write failure. Incorrect audience,
incorrect signature and provider failure must still raise an error. The
unchanged socket and self-link suites protect the surrounding authorization
boundaries. Against the pre-fix implementations, both valid native identity
cases and both signed OIDC outage cases fail; the other denial cases pass.

These tests establish source behavior only. Separately verify the deployed
image revision, CI execution rather than skipped jobs, existing-session card
entry, a fresh federated SSO round trip, and live MCP read/write behavior.
A recovered cookie session does not prove that a new SSO flow succeeded.
