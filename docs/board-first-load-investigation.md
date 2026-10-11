# Canary board first-load investigation

## Observed scope

On 2026-10-11, an authenticated in-app browser repeatedly loaded board
`2RRwwB5JNm0` and displayed the native board-load error. A prior manual retry
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
