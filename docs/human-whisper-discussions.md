# Human whisper discussions

## Delivery status

Persistence, the human UI API and the card discussion interface are implemented.
Local browser interaction and native HTTP/database acceptance are verified; a
new product image and authenticated canary acceptance remain pending. This is
not a deployment-complete feature.

## Access boundary

Whisper discussions are separate from card visibility and public card comments.
Only a server-authenticated `human_ui` user with current read membership and the
instance's existing internal-access policy can use them. API keys, MCP and bots
cannot list, create, reply to, invite to or resolve discussions. Caller-supplied
channel headers do not grant human access. Inaccessible anchors return the same
404 response as missing anchors. Responses use `Cache-Control: no-store`.

PRIVATE cards cannot host discussions, including for their owner. A relationship
thread is a single object available from either endpoint only while both cards
are currently readable, nondeleted INTERNAL or SHARED cards. Converting either
endpoint to PRIVATE hides that relationship discussion. No public comments,
ordinary activity events or shared notifications are generated.

Updating unchanged relationship edges preserves their IDs and discussions. Removing
an edge deletes its anchored discussion through the native cascading foreign keys;
visibility changes only hide existing discussions and do not delete them.

## Human UI endpoints

Base: `/board/{project_uid}/card/{card_uid}/whispers`.

- `GET /`: thread page, optional `before_uid`, `limit` (default 25, maximum 50).
- `POST /`: content, optional `relationship_uid`, or a card `subanchor_type` and
  `subanchor_uid`; optional `mention_uids` (maximum 10).
- `GET /{thread_uid}/messages`: chronological message page with an older-page
  cursor. Reading does not mutate the read receipt.
- `POST /{thread_uid}/messages`: reply content and optional explicit mentions.
- `PUT /{thread_uid}/read`: acknowledge a specific message belonging to this
  thread. The read cursor only advances.
- `PATCH /{thread_uid}`: `state` is `OPEN` or `RESOLVED`. Resolve/reopen is not a
  card workflow transition. Resolved discussions reject new replies.
- `GET /{thread_uid}/participants`: bounded participant page with explicit read
  receipts; deleted or deactivated users are omitted.
- `POST /{thread_uid}/participants`: up to 10 explicit `user_uids`. Each recipient
  must currently satisfy the human/internal/card and both relationship endpoint
  checks. Repeated invitations are idempotent.

Content is plain text, 1–16000 characters, never interpreted as HTML. Mentions
are structured user UIDs validated independently of the display text. Invalid
recipients abort the whole reply or creation. A mention adds a participant with
an unread cursor; it does not send a public card notification. Authors' explicit
writes acknowledge their own latest message. Card subanchors support
`content_block`, `comment`, `checklist` and `attachment`, validated against the
same current card; deleted resources are rejected.

## Storage and verification

Revision `c84d52a9e610` follows `a31f9e7b402c`. Native `WhisperThread`,
`WhisperMessage` and `WhisperParticipant` tables preserve messages separately from
public comments. Database constraints enforce anchor shape and the two states.
The migration refuses rollback while retained threads exist.

Focused real-database tests run on SQLite and disposable PostgreSQL schemas.
They cover relation identity, PRIVATE conversion, current-account/policy denial,
all four subanchor types, explicit read receipts, pagination, recipient rejection,
atomic reply failure, migration compatibility and rollback retention. Listing
1 or 25 threads performs the same four primary SELECTs, with no per-thread
permission or unread queries. A composite message `(thread_id, id)` index supports
latest-message lookup and cursor reads without a redundant thread-only index.
Native HTTP tests exercise route registration, AuthFilter, server channel derivation
and database persistence with isolated credential verification. Transport identity tests additionally verify that
client headers cannot turn API/MCP credentials into a human session.


## Card interface and bounded loading

Card details project `whispers_allowed` from the existing server-resolved current
visibility context. The interface is absent on PRIVATE/linked-resource cards and
for nonhuman or external callers. No new permission lookup is added to card
details. The discussion starts collapsed and performs no whisper request until
opened. Thread/message pages are bounded; participant receipts load only when
requested. Reads are cancelled on unmount and do not poll or retry automatically.
Query keys include the current user, board and card; cache entries are removed
when the last observer unmounts. Account/card changes reset component state.

The context selector uses already loaded card, relationship, content block,
comment, checklist and attachment models without fetching every child. A removed
context disables creation instead of silently falling back to the card.
Structured mention selection is bounded to ten people; known external members
are omitted, and the server revalidates all recipients at the write boundary.
A bounded first-message preview identifies each discussion. Existing theme-aware
buttons, selects, checkboxes, text areas and locale/date helpers are reused.
English, Korean, Japanese and Chinese labels are included. Message content and
previews render as plain text.

Drafts remain local while folding the discussion or switching threads. Unsaved
drafts show a dot and register the browser unload warning; they are not written
to public-comment models or persistent browser storage. Navigating away from the
card unmounts this local state; durable draft recovery is not provided. An unknown-result
write keeps its draft and blocks further writes without automatic replay. Current
access failures hide cached discussions until a successful explicit refresh; rejected
validation leaves the draft editable and displays an error. Users
can refresh and inspect saved messages before explicitly discarding the failed
draft and unlocking sending. Read receipts advance only through the explicit
read button; GET and expanding a discussion never acknowledge messages.

The `WhisperDiscussions.fixture.html` UI fixture isolates transport and includes
`?lost`, `?private`, `?write-denied` and `?locale=ko-KR` modes. Run the existing UI Vite configuration
and open the fixture in a fresh browser to reproduce lazy loading, message
creation, mentions, resolve/reopen, access revocation and lost-response handling.
This fixture is not evidence of live canary authorization or deployment.
