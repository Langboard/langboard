# Upstream-first canary integration

Upstream owns runtime architecture, public contracts, and source conventions.
Canary functionality must be ported to those contracts rather than maintaining
parallel legacy implementations. A selected upstream file is not a resolved
conflict until its callers, persisted state, and retained features agree.

## Event delivery boundary

Use the upstream `enqueue_event(event.id)` broker contract. Publish only after
the database transaction commits. Execution, interaction response, and archive
producers retain their immutable destination snapshot and current-authority
validation. Delivery remains separate from execution authorization and from
provider-reported outcomes.

Archive delivery must retain its native lifecycle receipt validation, including
ambiguous provider outcome handling. Upstream's default rejection of unsupported
event types must not silently remove this existing feature.

The integration remains incomplete: outbox field names, existing receipt data,
interaction authority snapshots, execution request identities, and migration
lineage must converge before event delivery is considered resolved. Preserve
existing receipt history during migration; do not stamp or discard old tables
to make the merge appear complete.

## Validation boundaries

Focused caller checks establish callback shape and publication timing only.
They do not establish PostgreSQL migration compatibility, native module import,
external delivery, Phoenix runtime operation, or deployed customer acceptance.
Those require independent evidence before deployment or completion.

## Editor synchronization boundary

Editable collaborative documents remain read-only until both provider sync and
editor initialization complete. The visible sync overlay is not an input guard.
Card description saves also require the existing collaborative update callback;
a pending document must retain its draft and allow cancellation without sending
a details patch. Toolbar hooks must keep a stable call order as readiness changes.

The UI-only transport fixture declares `@hocuspocus/server` as a development
dependency instead of loading the removed Node socket package. It exercises the
real Plate/Yjs editor against a local test server; it is not the production
Phoenix runtime and does not establish authentication or ACL acceptance.

Run from `src/ui` with the existing dependency environment:

```sh
node_modules/.bin/playwright test -c playwright.collaborative-draft.config.ts --workers=1
node_modules/.bin/playwright test -c playwright.card-deeplink.config.ts --grep 'unsynchronized restored|compact board refresh|comments mount once' --workers=1
```

These focused suites cover desktop/mobile draft retention, synchronization input
gating, pending-save cancellation, hydrated description retention and comment
mounting. Authenticated canary save/reload and external-account permission checks
remain separate release acceptance steps.
