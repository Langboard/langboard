# Execution Binding integration

An Execution Binding connects a Langboard project to an external executor.
Bindings are disabled by default. Enabling one may make existing cards in the
configured ready column executable; review the project and destination before
enabling it. The signed webhook must explicitly allow
`io.langboard.work.ready.v1`.

## Ready event

A `io.langboard.work.ready.v1` event signals that one card generation became
executable. It is not an immutable task snapshot. The delivered title, labels,
assignees and `source_revision` come from one delivery-time card read.

The event uses a relative `card_url` path. Resolve it against the project origin
configured for the integration. Retrieve authoritative content with the
consumer's own credentials and current project authorization.

Before execution, compare the card point-read with the event:

- `execution.is_ready` must be `true`.
- `execution.generation` must equal `data.execution_generation`.

Reject a revoked or superseded generation. Moving a card out of readiness and
back creates a new generation. A content edit that preserves readiness does not
increase the generation, so the consumer uses current authorized content rather
than executing a second task for the same generation.

`data.source_revision` is the delivery-time card update timestamp, provided for
provenance. It is not an execution fence. `description.revision` is a separate
content revision and must not be compared as though it were the same value.

## Authentication and duplicate delivery

Verify the signed `X-Langboard-Webhook-*` headers before accepting an event.
Delivery is at-least-once: `event_id` remains stable across retries. Maintain
durable duplicate detection and fence by execution generation so a retried
delivery cannot create a duplicate execution. A suitable execution key includes
project, card and generation, such as `langboard:{project}:{card}:{generation}`.

Disabling a binding stops new execution events and invalidates delivery for the
disabled binding. Avoid having a second polling integration execute the same
cards independently of this event and generation contract.

Execution binding, project, column, relationship and webhook authorization reads
use the primary database. Webhook delivery also reloads its current endpoint on
the primary; a lagging fanout replica cannot select a revoked event allowlist or
old destination. Candidate discovery can still use the existing replica path.
The regression retains a separate unchanged replica while revoking each primary
dependency or changing the endpoint, against SQLite and isolated PostgreSQL
primaries. This is a pre-delivery check, not a claim that later changes can
retract an HTTP request already sent.

## Delivery exhaustion

The initial HTTP attempt and three retries share a four-attempt budget. Failed
attempts one through three release the lease back to `pending`; the fourth failed
HTTP attempt persists `failed` with `delivery_attempts_exhausted` immediately.
No fifth task or recovery-cron pass is required to reach this terminal state.
Already exhausted pending or expired claims are also closed without another POST.
Manual reconciliation can explicitly reset failed rows to a fresh attempt budget.

HTTP completion updates require the same unexpired `lease_until` captured by the
claim and a still-`delivering` row. A late success or failure cannot overwrite a
replacement claimant or its terminal result. Expired claims remain recoverable;
this fence does not retract HTTP requests already sent to the consumer.

The PostgreSQL lifecycle regression checks the persisted state after each failed
attempt, then verifies that another drain performs no delivery. Its other checks
cover success, expired-lease recovery, live-lease exclusion, row locking, concurrent
hot-path/recovery exclusion, late success/failure after expiry with and without a
replacement claim, destination drift and manual reconciliation. The
fixture replaces the HTTP sender; it does not prove an external consumer's
signature verification, durable deduplication or deployed cross-repository E2E.
