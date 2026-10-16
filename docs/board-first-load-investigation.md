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
