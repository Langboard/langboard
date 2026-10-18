# Default agent execution limits

The default agent supports up to three rounds of tool requests, followed by a
final model invocation without tools. At most eight requested calls per round
can execute. Calls run sequentially, with API allowlisting, UID validation and
approval checked before invocation. Unknown, denied and over-limit calls receive
explicit responses without invoking the requested tool. Runtime errors also
become tool responses.

A tool timeout uses the configured AI request timeout. Its result may be unknown:
cancellation does not establish that an external effect was reversed. These
round and call limits do not establish a total wall-clock or monetary budget.

## Approval and resume

Completed tool steps are checkpointed separately from later approval requests.
Same-thread resume requests are serialized. Repeating a completed approval can
return its completed response; a conflicting decision returns HTTP 409.

Checkpointed resume does not guarantee exactly-once external writes. A process
can fail after an external commit and before its checkpoint is persisted.
Integrations need operation-specific idempotency and current authorization;
service transport authentication does not provide user/project permission or
checkpoint ownership by itself.

## History access

Chat history requires a valid signed user identity and is scoped to the user's
current owned session. A history anchor narrows collection to its session;
missing anchors and conflicting explicit sessions produce no history. Inactive
or deleted users cannot supply history authority.

Card-comment and project-activity history additionally require current board
membership and read authority, subject to existing owner/administrator role
exemptions and card visibility. Filters remain scoped to the requested board;
hidden or deleted card events are excluded before bounded pagination. Graph
history uses the API access channel, which excludes private cards even for their
owner. Bot-runner history is unavailable without a verified execution identity.

Deleted comments are represented as tombstones with identity and deletion time,
without their body in newly collected history. This does not erase original
stored bodies or earlier checkpoints.

## Credential and output limits

Structured credential fields are redacted from model context, history,
approval projections and public status output. This does not detect credentials
in arbitrary free text or provider error strings. Persisted runtime inputs can
still contain provider/API credentials. Operators must protect checkpoint
storage and apply their retention policy; output redaction is not a guarantee
that saved graph state is secret-free.

## Graph service integration

All `/api/v1/graph/` endpoints require service authentication. Backend callers
can use a short-lived service JWT with the instance signing key, exact instance
issuer, `langboard-graph` audience, `graph_service` token kind and a maximum
lifetime of five minutes. The configured socket service can also authenticate
with its internal service credential. Ordinary user and bot API tokens are not
service credentials. Deploy compatible callers and Graph together; callers
without accepted service credentials receive HTTP 401. Health checks remain
available. Service authentication does not grant end-user access.

Board chat resume requires the current user's owned chat session, the matching
board and the approval thread stored in the server-side message. A supplied
compatibility session ID cannot redirect approval to a different thread.
