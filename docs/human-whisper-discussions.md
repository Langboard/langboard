# Human whisper discussions

## Access boundary

Whisper discussions are separate from card visibility and public card comments. Only a
server-authenticated `human_ui` user with current read membership and the instance's existing
internal-access policy can use them. API keys, MCP and bots cannot list, create, reply to,
invite to or resolve discussions. Caller-supplied channel headers do not grant human access.
Inaccessible anchors return the same 404 response as missing anchors. Responses use
`Cache-Control: no-store`.

PRIVATE cards cannot host discussions, including for their owner. A relationship thread is a
single object available from either endpoint only while both cards are currently readable,
nondeleted INTERNAL or SHARED cards. Converting either endpoint to PRIVATE hides that
relationship discussion. No public comments, ordinary activity events or shared notifications
are generated.

Updating unchanged relationship edges preserves their IDs and discussions. Removing an edge
deletes its anchored discussion along with the relationship; visibility changes only hide
existing discussions and do not delete them.

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

Content is plain text, 1–16000 characters, never interpreted as HTML. Mentions are structured
user UIDs validated independently of the display text. Invalid recipients abort the whole reply
or creation. A mention adds a participant with an unread cursor; it does not send a public card
notification. Authors' explicit writes acknowledge their own latest message. Card subanchors
support `content_block`, `comment`, `checklist` and `attachment`, validated against the same
current card; deleted resources are rejected.

## Card interface and bounded loading

The interface is absent on PRIVATE/linked-resource cards and for nonhuman or external callers.
The discussion starts collapsed and performs no whisper request until opened. Thread/message
pages are bounded; participant receipts load only when requested. Reads are cancelled on unmount
and do not poll or retry automatically. Account or card changes reset the displayed discussion
state.

The context selector offers card, relationship, content block, comment, checklist and attachment
anchors. A removed context disables creation instead of silently falling back to the card.
Structured mention selection is bounded to ten people; known external members are omitted, and
the server revalidates all recipients at the write boundary. A bounded first-message preview
identifies each discussion. English, Korean, Japanese and Chinese labels are included. Message
content and previews render as plain text.

Drafts remain local while folding the discussion or switching threads. Unsaved drafts show a dot
and register the browser unload warning; they are not written to public-comment models or
persistent browser storage. Navigating away from the card unmounts this local state; durable
draft recovery is not provided. An unknown-result write keeps its draft and blocks further
writes without automatic replay. Current access failures hide cached discussions until a
successful explicit refresh; rejected validation leaves the draft editable and displays an
error. Users can refresh and inspect saved messages before explicitly discarding the failed
draft and unlocking sending. Read receipts advance only through the explicit read button; GET
and expanding a discussion never acknowledge messages.
