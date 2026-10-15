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
