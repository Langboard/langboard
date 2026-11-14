# Component branches and upstream contribution

## Ownership and branch model

Use the [repository map](upstream-repositories.json) to locate the component that
owns a proposed change. Follow that repository's contribution policy and submit a
focused feature or fix branch for maintainer review.

## Upstream synchronization and reconciliation

Check whether the current upstream already provides the requested behavior.
Preserve supported contracts, authorization boundaries and published history when
updating a contribution. Explain the behavior and compatibility effects in its PR.

## Existing work recovery

Retain the source history and references needed to review a superseded change.
Do not rewrite applied schema migrations; submit a new revision for corrections.

## Repository access and optional forks

Use an upstream feature branch only when the repository grants that access.
Otherwise follow its fork policy. A private repository must not be mirrored or
published without its owner's permission.

## Delivery and completion

Follow the destination repository's review and release requirements. A merged
source change does not establish that a released installation contains it.
