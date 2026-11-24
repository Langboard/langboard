# Native execution review

`submit_card_execution_review` is a native MCP entry point backed by the same
transaction as `PUT /projects/{project_uid}/cards/{card_uid}/executions/{generation}/receipt`.
It requires the current stored `card_update` project role and an existing positive
execution generation. It does not claim work or mint an execution generation.

The report requires `changed`, `verified`, `remaining`, and nonempty `evidence_refs`.
Reported verification is review evidence, not an acceptance decision. The command
never changes human-authored description, checkitem completion, or verification
records. Optional machine checklist evidence retains the receipt's existing rules.

Use `langboard:{project_uid}:{card_uid}:{generation}:receipt` as the idempotency key.
Retries with identical semantic content retain the first timestamp and receipt.
Changed content or a stale generation is rejected. The receipt, machine projection,
and configured review move share one database transaction; notifications run after
commit. `moved_to_review` describes a move by this call, not the current column.

Review movement currently follows the existing explicit execution binding. Missing,
disabled, ambiguous, or inapplicable mappings save the report without moving the card.
The returned `moved_to_review=false` makes that limit visible. Submitting a report
does not independently claim work, approve acceptance or mark a card complete.

`get_card_execution_receipts` reads the native report history under the current stored
`read` role. Both tools are available through native MCP without the GPT plugin.

## Explicit human runtime choices

The native card runtime list and the host-owned dialog opened by panel SDK
`runtimes.open({card_uid})` share human card authorization. Opening the dialog
never submits a stop, resume, or new-run request. The app iframe cannot invoke
these mutations directly or supply a user identity.

- `POST /board/{board}/card/{card}/apps/executions/{request}/new`
- `POST /board/{board}/apps/{app}/panel/cards/{card}/runtimes/{request}/new`

Both require the retained request's `generation`, the observed
`expected_generation`, and `expected_revision` (the exact card timestamp returned
by the list). The panel route also requires current `app_revision` and
`binding_revision` and restricts requests to that app. Stop/resume do not accept
new-run fence fields. A stale fence or conflicting previous choice is rejected.

A new run requires a runtime-reported stopped lease and current producer Ready
eligibility. It issues the next generation and execution event, then checks
current app ownership, connection/resource authority,
consent, workflow, prerequisites, and approval gates in the same transaction.
Failure rolls back the generation and both outboxes. A repeated matching choice
returns its retained request; a resume/new-run cross-selection cannot create a
second request. The stopped lease, its history, and prior execution evidence
remain retained. A request receipt is not runtime-start evidence.

The existing readiness producer is PostgreSQL-specific and requires an explicit
enabled execution binding and webhook. The list advertises `new_run_available`
only for that producer's current Ready generation; SQLite does not advertise the
choice and rejects direct new-run submissions. There is no independent UI or
SQLite generation counter. Read the current state after a mutation; an uncertain
response must be reconciled before another explicit submission.
