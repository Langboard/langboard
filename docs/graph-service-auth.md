# Graph service authentication

Graph endpoints under `/api/v1/graph/` require backend service authentication
before route execution. An unauthenticated or incorrectly authenticated caller
receives HTTP 401. Health probes remain outside this protected prefix.

## Backend credentials

Python bot, draft and approval callers send a short-lived Bearer JWT signed with
the instance's configured JWT key and algorithm. The token has subject
`langboard-service`, issuer `PROJECT_NAME`, audience `langboard-graph` and
`internal: graph_service`. Issued tokens expire after five minutes; verification
requires the subject, issuer, audience, issued-at and expiration claims and a
positive validity interval of at most five minutes. Ordinary user or API tokens
are not Graph service credentials.

Phoenix streaming and resume callers instead send `x-socket-internal-secret`
using `SOCKET_PHOENIX_INTERNAL_SECRET`. Graph accepts this credential only when
its configured secret has at least 32 characters and the supplied bytes match
using a constant-time comparison. Missing, short or incorrect secrets fail
closed.

Configure matching credentials in Graph and its corresponding API/Phoenix
callers through deployment secrets. The standard Compose configuration forwards
`SOCKET_PHOENIX_INTERNAL_SECRET` to those services. Upgrade callers and Graph
together: callers that omit a supported service credential receive 401 even if
they could reach an older Graph service. The realtime deployment options are
covered in [bundled or external Phoenix](external-phoenix.md).

## User identity and history access

Service authentication does not authorize a caller to act as an arbitrary user.
Run and approval operations retain their user/project authorization. Callers
must preserve the signed identity and current resource permissions required by
each operation.

Chat history requires a signed bot-context user identity, an active primary
account and current ownership of the requested ChatSession. An anchor history
identifier selects its actual session; combining it with another session
identifier does not broaden access. Missing, invalid, expired, wrong-kind or
out-of-range identity claims return no history.

Board comment and activity history require a current active account and board
read authority: board ownership, or current membership with the required read
role or administrator authority. Card comment history also checks current card
readability. Deleted comments are represented as tombstones without their body.
These checks are separate from app connection credentials, consent and card
visibility; a backend credential does not override them. See
[card visibility](card-visibility-transitions.md) and the
[external service app SDK guide](external-app-sdk.md) for those public contracts.

## PostgreSQL checkpoint connections

Graph recognizes PostgreSQL settings through the same database dialect helper
as the host. `postgresql://`, `postgres://` and `postgresql+psycopg://` settings
are converted to psycopg's native `postgresql://` scheme for checkpoint storage,
thread locks and background concurrency slots. The authority, credentials,
host, database and query parameters are preserved without decoding them.

Durable Graph checkpoint storage requires PostgreSQL. A non-PostgreSQL setting
does not enable that checkpointer. Preserve the database and its checkpoint
history across restarts if interrupted work must remain resumable. Authentication,
checkpoint persistence and permission to resume are separate requirements.
For connection syntax, see the
[psycopg connection documentation](https://www.psycopg.org/psycopg3/docs/api/connections.html).

Use [app platform compatibility](app-release-compatibility.md) when coordinating
host, SDK and database upgrades. This guide describes the source contract and
does not identify the runtime version or readiness of a deployed instance.
