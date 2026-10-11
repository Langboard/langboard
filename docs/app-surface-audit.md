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

A future single logical app tile must retain the actual module identity,
version, trust targets, binding revision, connection ownership and grants for
each operation. It must not infer grouping or authorization from a key suffix,
icon, name, panel URL or `signal_sources`. Source declarations grant no ownership
of their providers. No identity migration or Store consolidation is implemented
by this audit; those acceptance items remain open.

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
