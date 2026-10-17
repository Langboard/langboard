# Human whisper discussions

## Delivery status

The persistence and human UI API boundary are implemented. The card interface,
mention picker, anchor entry points and authenticated canary acceptance remain
pending. This is not a completed user-facing feature.

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
