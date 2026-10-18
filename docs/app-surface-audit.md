# App identity and independent surfaces

## Current source audit

The built-in provider keys and independent service keys are separate authority
identities. Their shared provider name is not proof that they have the same
publisher, destinations, approved version, credentials or board consent.

| Surface | Current owner | Data and actions | Authority |
| --- | --- | --- | --- |
| Signal | Built-in `github`, `glitchtip`, `dokploy`, or an approved service with `signals.publish` | Bounded normalized provider evidence; no implicit card creation | Current definition, signed connection, selected resource and board publication grant |
| Inbox | Native host; an explicitly approved panel can read declared sources | List and triage evidence; explicit native create command can create an ordinary card with an evidence link | Current user access, provider resource access and panel source/resource-type consent; creation has a separate card mutation gate |
| App-owned workflow card | Approved service with `cards.create` and a declared card lifecycle | Native card content plus optional app interactions and archive behavior | Durable `CardAppOwnership`, selected app resources and current card capability; inbox lineage is insufficient |
| Panel | Independent `github-panel`, `glitchtip-panel`, `dokploy-panel` | Optional UI; default profiles read the legacy provider inbox | `panels.render` plus separately granted `signals.read`; rendering does not grant card mutation or workflow execution |

The three independent services default to read-only panel definitions. Their
`--publish-signals` profiles add their own signed publication policy and source
key. `--workflow-cards` separately adds card capabilities and lifecycle policies.
GitHub and GlitchTip optionally support interactions on workflow-card profiles;
Dokploy retains deployment cards on archive without an external mutation. These
are independently declared features, not consequences of opening an inbox.

## Observed duplication

`AppManifest.APP_MANIFESTS` retains built-in provider declarations.
`AppRegistry.save_definition` rejects overwriting those keys; approved external
services therefore use distinct keys. `WorkflowStageService.get_app_catalog`
returns one entry per authority key. `BoardSettingsApps` renders native provider
connection controls and every returned catalog entry, including external panels.
Consequently, the same provider name can appear in multiple configuration areas.
This is a presentation/composition issue, not evidence that the identities or
consent records are interchangeable.

A logical app tile must retain the actual module identity,
version, trust targets, binding revision, connection ownership and grants for
each operation. It must not infer grouping or authorization from a key suffix,
icon, name, panel URL or `signal_sources`. Source declarations grant no ownership
of their providers. No authority identity migration follows from composition. The global catalog now
projects existing explicit compositions as described below.

## Separation verification

`test_external_provider_signal_contracts.py` runs the reviewed declarations and
normalizer snapshots of all three independently packaged services through native
signed HTTP on SQLite and PostgreSQL. Publication, duplicate replay and inbox
reads leave both native card identities and card-evidence links unchanged.

`test_signal_card_creation.py::test_inbox_creation_and_replay_do_not_grant_app_card_ownership`
separately verifies that explicit inbox card creation saves an evidence link but
creates no app ownership, app resource selection, external-import lineage or
changes to the original signal. App-owned card commands use `AppCardScope` and
require their own durable ownership and selected resources.

These are local contract/database checks. They do not prove deployed provider
onboarding, authenticated browser acceptance, module consolidation, migration or
production rollout.

## Panel-only withdrawal

Closing a panel removes only `panels.render`. Independently granted
`signals.read`, `signals.publish`, card capabilities and workflow settings remain
unchanged. Panel HTTP still rejects reads because rendering consent is required
for that bridge. An explicitly granted native inbox or publisher keeps its own
current authority. Full app disablement and explicit capability withdrawal remain
the ways to stop those functions.

Enabling/reviewing a panel with its signal-read checkbox cleared explicitly
withdraws `signals.read`; checked consent still requires declared sources.
Every change updates the binding revision. Old publisher envelopes fail with 409,
and editing a persisted envelope's revision cannot turn it into a valid replay.
A new provider event uses the current revision. Existing receipts remain stored;
the inbox projects current evidence rather than one row per receipt.

The registry HTTP tests cover close, denial at the panel bridge and explicit
signal withdrawal on SQLite and PostgreSQL. The three-service publication tests
also cover panel-only close, preserved native inbox reads, rejection of stale or
modified replay envelopes, new signed event acceptance and credential revocation.

### Full board app disablement

The three independent provider contracts additionally exercise
`disable_app_binding` with the current board binding revision. After full
withdrawal, all board grants are empty, signed publication is denied, and the
provider-specific inbox is empty. Both stored event receipts, pre-existing cards,
evidence links and the connection record remain intact. Credential revocation is
exercised first and then a fresh native credential is issued, so the board-disable
assertion cannot pass merely because an old credential was revoked. This proves
native storage and authority behavior; live service cancellation and browser
acceptance remain separate.


## Global logical catalog projection

The default `AppCatalog.list_catalog` and `/apps/catalog` contract remains a flat
list of enabled definitions, including composition members, with raw-key
`next_cursor` values. Cached AppStore versions and existing clients keep the same
response shape, featured modules and pagination behavior.

The current AppStore automatically requests `include_compositions=true` and uses
a distinct query cache key. This additive projection pages a SQL union of explicit
composition identities and ungrouped enabled definitions before fetching members. Membership comes only from
`AppCompositionMember`; provider names and key suffixes never infer a group.
Each page contains at most 50 logical entries, each composition contains at most
16 explicitly registered members, and member/definition queries have bounded
limits. Featured official modules resolve to the same explicit composition so
an installed group appears once alongside standalone installation suggestions.

Logical composition and execution module keys can collide. The catalog cursor
in the opt-in projection therefore includes the identity kind
(`key~composition` or `key~module`). Within that projection, legacy `after=key`
remains accepted and skips all identities up through that key. Default discovery
continues to emit and accept only raw-key cursors. Exact
`key=module` detail preserves the original module identity, including when the
module is a member. The additive `composition_key=key` selector opens logical
detail and explicitly selects the composition projection even without
`include_compositions`; the detail selectors are mutually exclusive. Existing module routes, binding
keys and account shortcut records retain their meaning. Logical detail links to
individual modules for existing shortcut controls; no logical-group shortcut
schema or execution identity is introduced.

The logical detail contains sanitized current member versions, requested
capabilities, definition revisions and administrator availability. Disabled
members remain visible as unavailable members and have no module action.
Capabilities are never combined into a group grant. Built-in members retain
their native keys; grouping does not rewrite their approved definitions. Board configuration continues through the
existing board consent surface. Global and organization policy and current board
read access are checked before catalog queries; disabled policy returns no
logical detail or featured entries. Declarations, trust destinations, storage
locators, personal connections and credentials are excluded.

`test_app_catalog_http.py` covers SQLite and PostgreSQL HTTP discovery, logical
flat default/raw-cursor compatibility and opt-in pagination including a
colliding standalone key, current disabled/revised
members, exact legacy detail, grouped featured entries, cross-board denial and
unchanged board grants. `AppsPage/AppStore.spec.ts` exercises the real AppStore
component in desktop and mobile browsers with controlled HTTP responses, including
logical/module route selection, existing module shortcuts, disabled members and
policy denial. These source, database and browser-fixture checks do not establish
a production deployment or authenticated production acceptance.
