# Execution Binding integration

An Execution Binding connects a Langboard project to an external executor.
Bindings are disabled by default. Enabling one may make existing cards in the
configured ready column executable; review the project and destination before
enabling it. The signed webhook must explicitly allow
`io.langboard.work.ready.v1`.

## Ready event

A `io.langboard.work.ready.v1` event signals that one card generation became
executable. Its title, labels, assignees and `source_revision` are frozen in the
producer's commit transaction. Every delivery of the same `event_id`, including
retries and recovery after a worker restart, uses that persisted semantic payload.
This event provenance does not replace the consumer's current authorized task read.

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

`data.source_revision` is the commit-time card update timestamp, provided for
provenance. It is not an execution fence. `description.revision` is a separate
content revision and must not be compared as though it were the same value.

Legacy rows with a complete commit snapshot keep the same payload contract.
Rows with absent, incomplete or malformed snapshot content are permanently blocked
with `snapshot_unavailable`; delivery never reconstructs them from mutable card
content. Missing destination provenance remains `destination_unavailable`.
Reconciliation cannot recreate missing historical content. Recover those rows only
through an explicitly authorized new execution generation, after current readiness
and binding authorization have been reviewed.

## Authentication and duplicate delivery

Verify the signed `X-Langboard-Webhook-*` headers before accepting an event.
Delivery is at-least-once: `event_id` remains stable across retries. Maintain
durable duplicate detection and fence by execution generation so a retried
delivery cannot create a duplicate execution. A suitable execution key includes
project, card and generation, such as `langboard:{project}:{card}:{generation}`.

Disabling a binding stops new execution events and invalidates delivery for the
disabled binding. Avoid having a second polling integration execute the same
cards independently of this event and generation contract.

Current binding, destination and authorization are checked before delivery.
Revoking access or changing a destination cannot retract an HTTP request already
sent. A completed workflow-stage policy revokes readiness even if a binding
still maps that column to `ready`; pending events for that readiness are
superseded without creating another execution generation.

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

Recovery work can be delayed during sustained foreground demand. Monitor
recovery latency and backlogs when operating the integration; background priority
does not provide additional processing capacity.
