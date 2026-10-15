# Canary / Phoenix migration compatibility audit

Status: incomplete. This is an integration finding, not migration replay or a release receipt.

The original canary union remains `2e9b4b243044903fe3edfd85bbbed5a52164ebb1`. This worktree has an unfinished merge and is not deployable. The customer repair in PR #686 stays on its existing canary review path; this larger integration is not a prerequisite for that repair.

## Evidence

The static Alembic DAG audit found two heads:

- `a5b6c7d8e9f0`: 57 reachable revisions, no repeated table creation within this head.
- `c97659de63f4`: 117 reachable revisions, no repeated table creation within this head.

Combining both reachable sets exposes 24 repeated table-creation declarations. A merge revision alone cannot reconcile these schemas. The audit did not execute SQL.

Evidence: `.verification/migration-duplicate-create-audit.txt` and `.verification/migration-dag-merge-audit.txt`.

## Required compatibility work

1. Preserve applied revision identifiers and data. Do not stamp an incompatible database or discard retained history to make upgrades pass.
2. Compare the deployed revision and schema with both lineages before choosing the adoption path.
3. Use current main model and service contracts as the target, with explicit migration of retained canary fields and constraints. Avoid parallel runtime implementations.
4. Replay upgrades from a fresh database and a populated canary fixture retaining requests, delivery attempts, receipts, ownership, relationships, consent and reviews.
5. Recheck authorization and idempotency after conversion. Table creation alone is insufficient.

## Concrete differences

- Outbox: canary uses `(request_id, event_type)` uniqueness and `attempt_count`, `lease_until`, `delivery_history`; Phoenix uses unique `request_id` and `attempt`, `claim_expires_at`, `delivery_receipts`. Renaming fields does not establish compatibility with existing multiple events per request.
- Review: canary stores `fingerprint` and `receipt`; Phoenix stores `snapshot` and `receipt`. Existing review evidence must not become a fabricated current authority snapshot.
- MCP consent: Phoenix adds a connection foreign key and positive-revision check. Retained consent and connection deletion need explicit compatibility verification.
- Acknowledgment: Phoenix makes request and event identities unique. Inspect existing rows before adding that constraint.

## Narrow model verification

The acknowledgment and MCP consent models now follow incoming main contracts. An isolated subprocess loaded the merged sources with the retained native ORM. Duplicate request/event SQLite inserts were rejected; consent foreign-key metadata and the 200-character runtime-reference validation bound were inspected.

The first verification incorrectly assumed `max_length` became SQL column length. It failed; `.verification/app-model-merge-check-first-failure.txt` preserves the output. The corrected check inspects validation metadata and does not claim a SQL length constraint.

Passing evidence: `.verification/app-model-merge-check.txt`. This is not PostgreSQL migration replay, foreign-key enforcement testing, merged-service acceptance or live canary validation.

## Updated 2026-10-12: applied lineage convergence

The initial two-head/24-duplicate finding is resolved in the integration candidate.
Nineteen unapplied reconstruction revisions were backed up and retired. Applied
canary revisions and the nine upstream notification/runtime revisions remain;
`d0876afe1402` joins their heads. Additive `e1987bdf2503` introduces only missing
resource-selection revision / identity hash columns and human-intent / Active
receipt tables. The old request/outbox/Review/counter/history schema is preserved.

Fresh PostgreSQL and populated `c97659de63f4` upgrades pass. Retained rows, two
outbox event types per request, delivery attempts/history, canonical generations,
ownership, selections/audit, package metadata and MCP consent remain equal. Empty
forward roundtrip passes; rollback with intent/Active evidence is refused. Evidence
is the reusable `tests/migrations/test_phoenix_canary_union_postgres.py` and private
`phoenix-canary-migration-postgres-final.txt` (1 passed, no skips).

This resolves migration DAG and populated replay, not the overall release. Full
native operation on that migrated schema, constraint inventory, reconstruction
acceptance alignment, application lifespan and deployed canary remain pending.


## Updated 2026-10-14: validation candidate follows the current canary

The profile-validation candidate now integrates the current canary source while
retaining the upstream Phoenix runtime and its integration entrypoint. The
integration preserves the visibility-transition API, UI and tests, the patched
OS-library version guard, the reduced UI build graph, and the locked dependency
layers. API validation no longer requires the cron runtime. Document-worker
validation still includes the real document-processing dependency set and CJK
fonts. Named profiles remain `socket`, `default`, `api`, `worker`, and `ui`;
fresh-cache invalidation is restricted to the test execution stages.

Focused request-envelope, image reproducibility, and document-image checks passed:
30 tests and 26 subtests. Static profile resolution uses each profile's build
context, and all declared targets and fresh stages exist. This does not establish
actual Phoenix container execution, PostgreSQL integration acceptance, or live
canary acceptance. The current narrow API/UI delivery remains separate from the
larger Phoenix candidate until those full gates pass.

## Updated 2026-10-14: deployed realtime runtime is still Node

A read-only inspection of the canary deployment found `langboard_socket`
running the `langboard-socket` image. `docker top` reported two Node processes,
including `node /app/dist/`; the container command was
`["pm2-runtime", "ecosystem.config.cjs"]`. This is direct evidence that the
legacy Node realtime runtime has not been retired in the deployed canary.
No container was stopped, rebuilt or replaced during this inspection.

The inspected candidate was `91efb8ffc9d357c93b46f58efefd6c88be1bb05a`.
Its `socket` image profile produces the Elixir release from
`src/socket/Dockerfile`, with `/app/bin/langboard_socket start` as its entrypoint
and port 5690. The upstream Compose service is `socket-phoenix`, with readiness
at `/health/ready`, API/Kafka/Redis/graph dependencies, and retained editor-sync
storage. The directory name `socket` does not identify a Node implementation;
WebSocket transport also remains part of the Phoenix implementation.

An API/UI-only release cannot demonstrate this runtime migration. Acceptance
requires exact-source Phoenix validation and a signed socket image, a release
declaration covering the runtime and compatible API/UI routing, and live
authorization, notification, editor-sync and board-chat checks. Removal of the
old Node service follows successful cutover verification; its currently healthy
status is not Phoenix acceptance. The original canary database and retained
application history must remain intact throughout the cutover.
