# App card lifecycle and opt-in interactions

## Independent features

| Concept | Purpose | Durable effect |
| --- | --- | --- |
| Signal | Observe an external event or state change | Evidence with an external identity; no implied workflow mutation |
| Inbox | Receive, group and triage signals | Inbox read/triage state; no implied card completion or approval |
| App-owned card | Track actionable work in an existing board workflow | Native card identity, app provenance, comments, relations and checklists |

An app may provide an inbox, workflow cards or both. Linking a signal to a card preserves both
identities and histories. Reading, dismissing or resolving an inbox entry does not complete or
archive its linked card. Responding to or archiving a card does not consume its inbox evidence.

Signal ingestion, card creation, interactions and archive synchronization require separate
consent. New connections enable none of the card creation, interaction or external archive
effects automatically. Declaring a capability grants nothing. Native Inbox card creation creates
an ordinary card without app ownership.

Every card-producing app must declare a lifecycle policy for each card type. Declaration schema
2 requires `card_lifecycle` for `cards.create`; schema 1 remains readable, but approval/update
and new creation require lifecycle declarations. Creation requires an explicit declared
`card_type`; replays retain its original type and changed types conflict. Existing cards and
history remain readable. App-created cards retain the creating app and selected connection
resource. Creation grants no interaction capability and does not resume execution.

Archive policies use `retain` or `external`. External policies name a stable app-owned action,
use `default_enabled: false` and explain the effect. Restoring a card retains the external
state; reopening an issue requires a separate action. Neither archiving nor deleting a card
deletes its external resource.

## Intended app behavior

These policies describe optional provider behavior. Declaring a policy does not install a
provider adapter or establish that an instance can execute it.

| App card | Archive with synchronization enabled | Optional interaction |
| --- | --- | --- |
| GitHub issue | Close the linked issue and read back `closed` | Close or defer |
| GitHub pull request | Retain state; never implicitly merge or close | Merge, close without merging, or hold |
| GlitchTip issue | Resolve the linked issue and read back `resolved` | Resolve, ignore, or escalate |
| Dokploy deployment | Retain deployment and logs; never stop or delete a service | Review evidence or explicitly select another deployment action |

PR merge requires a distinct action with the frozen head SHA, current checks, mergeability and
repository permission. Archiving is not merge approval. Provider action availability depends on
the installed app and its configuration.

## Archive synchronization consent

Use `PUT /board/{board}/apps/{app}/archive-sync` with the current binding revision and selected
card types. Only approved external lifecycle policies with separate `events.receive` consent
qualify. New bindings have an empty `archive_sync_types` list. Lifecycle or trust changes reset
activation. Disabling a board binding clears consent; re-enabling cannot revive an older queued
operation.

An archive receipt records `retained` when policy retains state or synchronization is disabled,
and `blocked` when lifecycle provenance is missing or ambiguous. Reordering within Archive does
not create another operation; restoring and archiving again creates a separate receipt.
Restoring invalidates an undelivered archive request. Archiving, event delivery and the external
outcome have separate receipts. Native archival alone never establishes provider success.

## Signed archive event

The event `io.langboard.app.card.archived.v1` contains stable `event_uid` and `archive_uid`,
card revision, declared action and immutable external identity. Current app, connection,
selected resource, grants, destination and synchronization consent are rechecked before
delivery. Connection revocation blocks pending work. Archive delivery is independent of
runtime-start eligibility.

Receivers deduplicate by operation identity, recheck provider authority, inspect current
external state before mutation and read it back afterward. Timeout or a lost mutation response
leaves the result unknown; reconcile before another write. Provider credentials and API
execution belong to the receiving app.

## Provider outcomes and receipt reads

An app reports an outcome with `POST /apps/v1/boards/{board}/archives/{archive}/outcome`, its
connection Bearer credential, `expected_revision` and `outcome: {state, external_state,
readback_confirmed}`. Reported states are `applied`, `already_applied`, `failed`, `blocked` or
`unknown`. Applied states require nonempty external state and confirmed readback. Reports are
authenticated app reports, not independent provider verification. Unknown outcomes suppress
further mutation delivery until reconciliation.

The owning app reads the receipt with `GET /apps/v1/boards/{board}/archives/{archive}`. The
response includes outcome revision and delivery state and uses `Cache-Control: no-store`.
Current archived incarnation, ownership, resources and explicit synchronization consent still
apply. A conflicting terminal report does not by itself prevent an authorized receipt read.
Consent withdrawal or changed card/connection boundaries prevent app reads. Reading does not
retry delivery or mutate the provider.

Native users read bounded history through `GET /board/{board}/card/{card}/apps/archives` under
current card visibility. Card detail loads the latest ten receipts when history is opened and
distinguishes delivery from provider outcomes. Retained native history remains available to
currently authorized users after app consent is withdrawn.

See the [external app SDK](external-app-sdk.md) and [human
interactions](app-human-interactions.md) for related consent and receipts.


## Reconcile an app creation response

The creating app reads one source identity with
`GET /apps/v1/boards/{board}/card-creation-receipt`, its existing connection Bearer
credential, and query fields `resource_uid`, `external_id`, `payload_fingerprint`.
The namespace includes the authenticated app and connection, not a caller-supplied
namespace. Current board read authority, approved `cards.create`/`resources.read`
grants, connection policy and selected resource access are required before either
absence or a receipt is returned. A found target must still be readable under its
current app ownership and resource selection. Archived cards may be reconciled;
deleted cards and withdrawn ownership/selection are denied. A changed Backlog
mapping does not make a historical creation receipt disappear.

The fingerprint is lowercase SHA-256 of UTF-8 JSON using Python `json.dumps` with
`sort_keys=True`, `ensure_ascii=False` and default separators. Its input is
`[title, description, presentation, card_type]`, omitting the final element when
`card_type is None`. This is the original POST fingerprint, including its default
spaces. Preserve the complete original payload after an unknown create outcome;
the SDK computes this query field from those original arguments.

A matching response is `{found: true, receipt: {card_uid, receipt_uid, created:
false, effects_state}, payload_fingerprint}`. `created: false` describes this read,
not whether the original POST created a card. A current authorized absence is
`{found: false}`. A different original payload is HTTP 409, after current card
scope is checked; withdrawn authority is HTTP 403. Transport and server errors
leave reconciliation unknown. Older hosts may return 404 for the unavailable
route; that is not a missing source receipt. Responses use `Cache-Control:
no-store`.

This GET performs no inserts, updates, deletes, card creation, delivery dispatch,
or implicit retry. A missing result is a point-in-time observation and does not
authorize an automatic POST retry. Existing POST replay/conflict behavior and
original receipt identity remain unchanged.
