# App card lifecycle and opt-in interactions

## Independent features

| Concept | Purpose | Durable effect |
| --- | --- | --- |
| Signal | Observe an external event or state change | Evidence with an external identity; no implied workflow mutation |
| Inbox | Receive, group and triage signals | Inbox read/triage state; no implied card completion or approval |
| App-owned card | Track actionable work in an existing board workflow | Native card identity, app provenance, comments, relations and checklists |

These are distinct resources and functions, not alternate names for one card.
An app may provide only an inbox, only workflow cards, or both. Linking a signal
to a card preserves both identities and histories. Reading, dismissing or resolving
an inbox entry does not complete or archive a linked card. A card response or
archive does not silently consume its inbox evidence. Any configured conversion
or synchronization requires its own explicit feature, authority and receipt.

Signal inbox ingestion, app-owned workflow card creation, card interactions and
archive synchronization are separately enabled connection features. Receiving a
signal never enables any of the others. New connections default to no card
creation, no interactions and no external archive effects. Settings must retain
the explicitly enabled features and recheck current capability and scope before
each external action. Declaring a feature does not enable or authorize it.

Every card-producing app must declare an archive policy for each supported card
type. SDK declaration schema 2 requires `card_lifecycle` for `cards.create`;
schema 1 remains readable during explicit migration. An undeclared type cannot
silently inherit an external action. The host must reject creation of undeclared
types and require migration before enabling card creation for old definitions.
Host approval/update now requires lifecycle policies for card-producing apps,
including schema 1 submissions; old approved definitions remain readable.
Creation now requires an explicit declared `card_type`, validates it against the
current app lifecycle and records it in immutable import provenance. Replays
retain the original type; changed types conflict. Definitions without lifecycle
must be explicitly updated before creating new cards. Existing native cards and
their history remain intact; automatic migration and lifecycle dispatch are pending.

New app-created cards persist app ownership and their authenticated connection's
selected resource in the creation transaction, including native ownership/resource
audit history. Duplicate source recovery does not add audit rows or overwrite
later human configuration. This lets the creating app use its explicitly granted
interaction capability on the same native card; creation does not grant that
capability or resume execution. Failed creation rolls back configuration together
with card and import lineage.

Archive policies use `retain` or `external`. External policies name a stable
app-owned action, have `default_enabled: false`, and provide explanatory text.
Restoring a card retains the current external state; reopening an issue requires
a separate explicit action. Neither archiving nor deleting a card deletes its
external resource. Existing comments, relations and checklists remain native.

## Intended app behavior

| App card | Archive with synchronization enabled | Optional interaction |
| --- | --- | --- |
| GitHub issue | Close the current linked issue and read back `closed` | Close or defer |
| GitHub pull request | Retain state; never implicitly merge or close | Merge, close without merging, or hold |
| GlitchTip issue | Resolve the current linked issue and read back `resolved` | Resolve, ignore, or escalate |
| Dokploy deployment | Retain deployment and logs; never stop or delete a service | Review deployment evidence or explicitly select a new deployment action |

GitHub issue closure uses the official issue update API. PR merge must be a
distinct action with the frozen head SHA, current checks/mergeability and current
repository permission. Archive is not PR merge approval. No existing review PRs
are merged by developing or testing this feature.

## Host execution requirements

Archive intent, native archive state and external outcome are separate durable
facts. Persist an outbox event with the same native transaction as the archive
intent; deliver through the signed app channel after commit. Include event UID,
card UID, card type, ownership revision, external resource identity and feature
configuration revision. Recheck current app, connection, selected resource,
actor or automation permission and enabled feature at dispatch and execution.
Archiving must not accidentally invalidate its own authorized completion event;
do not reuse runtime-start eligibility for lifecycle delivery.

Use one idempotency key per archive operation. A repeat reads current external
state before mutation. Record `pending`, `applied`, `already_applied`, `retained`,
`failed`, `blocked` or `unknown` independently of transport delivery. A successful
HTTP write requires a readback of the same resource in the desired state.
Connection revocation blocks pending work. Timeout or lost mutation response is
unknown and needs readback before any retry. Do not hold a database transaction
across provider calls or claim external success from an archived native card.

Before activation, the settings UI explains the external effect; the archive UI
shows the enabled effect and later its receipt. Archive sync is not an arbitrary
provider-code execution hook. Provider verbs and credentials stay in each app.

## Current implementation evidence

SDK source 0.2.15 validates finite lifecycle and disabled-by-default card action
declarations. GitHub adapter source closes issues using one write and a readback,
protects PR resources, and builds an opt-in structured PR decision request.
GlitchTip adapter source supports a resolve/readback interface; its HTTP provider
adapter is still required. Dokploy returns an explicit no-external-action receipt.
These modules are not yet wired to signed events, settings or host archive paths.
No deployed provider mutations or end-to-end archive behavior are claimed.
