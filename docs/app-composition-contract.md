# Logical apps and independently authorized modules

Status: host persistence, HTTP discovery and board Store grouping implemented
and locally verified. The administrator composition editor is implemented and locally verified. Full-composition disable is implemented with atomic revision checks. Deployed migration/browser acceptance remains open.

## Board Store and app management

The Store shows overview tiles; it does not eagerly mount every provider's
connection editor. Explicit compositions have one tile and a module overview.
Each module's management view has separate Connections, Permissions, Workflow
and Panel sections. Editors and provider reads mount only in the selected section.
Commands continue to address the exact module key and its approved revisions.
Panel activation does not enable signal publication or create app-owned cards.

Modern discovery returns membership and modules together. The UI uses one request
and one snapshot; it does not merge grants across modules. If composition discovery
fails, the UI reports degraded grouping and loads the unchanged flat module list.
It does not display an old grouped snapshot as a successful current refresh.
English, Korean, Japanese and Chinese management labels are provided. Local
desktop/mobile tests cover group navigation, module-targeted panel writes,
retained signal consent, workflow drafts, credential recovery, read-only authority
and degraded discovery. These fixture tests do not prove canary deployment.

## Current host API

- `GET /settings/apps/compositions`: current administrator lists explicit groups and exact installed module keys, names, versions and enabled status.
- `POST /settings/apps/compositions`: current administrator creates/updates
  `key`, `name`, `description`, ordered `module_keys`, and `expected_revision`.
- `POST /settings/apps/compositions/{key}/ungroup`: revision-checked removal
  of presentation membership, preserving exact module authority records.
- `GET /board/{project_uid}/settings/apps/compositions`: current board reader
  obtains compositions and the unchanged module catalog in one host transaction.

The migration creates `app_composition` and `app_composition_member` without
renaming or copying module records, and seeds no provider-name associations.
Unique module membership is enforced in SQL. A downgrade refuses to discard
approved groups until they have been explicitly ungrouped. Local SQLite and
PostgreSQL tests cover authority preservation, stale revision/membership
conflicts, current administrator/read revocation, authenticated HTTP and schema
upgrade/rollback. This is not deployed Store or migration acceptance evidence.

## Outcome

Show one explicitly configured logical app in the Store and one details screen
for its modules. Preserve all existing module keys and authorization records.
Signals, an inbox, app-owned workflow cards, and a rail panel remain separate
features. A logical app is a management grouping, never an execution identity.

## Composition ownership and validation

The instance administrator approves a host-managed composition with a stable key,
English fallback name, description, generation, and an ordered set of existing
module keys. An app cannot place itself into another publisher's composition.
Neither provider names nor `*-panel` suffixes, icons, URLs, or `signal_sources`
create a composition. Each module belongs to at most one composition. Reject
duplicate module keys, unknown definitions, and cyclic/nested compositions.
Removal from a composition changes presentation only.

Composition edits use optimistic revision checks and current administrator
authorization. An unavailable or disabled module remains visible in details
when it has retained records, with its current status. Missing module definitions
cannot silently fall back to another module or borrow that module's capabilities.

## Module identity and independent features

Each module retains its exact key, definition version and revision, publisher,
trust targets, resource types, declared capabilities, and board binding revision.
One logical tile does not sum grants or join credentials. Details show each
feature's declared availability separately from its actual consent/status.

| Feature | Meaning | Required boundary |
| --- | --- | --- |
| Signal | Normalized external event or observation | Current publisher/adapter, signed connection, selected resource and publication policy |
| Inbox | Read and triage authorized signals | Current reader, source/resource-type consent and provider resource access |
| App-owned card | Durable workflow object owned by a declared app | Card creation grant, lifecycle declaration, durable ownership and selected resources |
| Rail panel | Optional UI contributed by a module | Panel rendering grant; separate grants for data reads and commands |

Signal receipt does not create a card. Explicit native inbox card creation
creates an ordinary card with an evidence link, not app ownership. A card-owned
interaction or archive command resolves through the owning module key and never
the composition key. Native comments, checklists and relationships remain usable.

## Connection and board management

The details screen displays module-specific connections, selected resources,
panel consent, card lifecycle and workflow mapping together. Every mutation
still addresses one exact module and its current revisions. Connections retain
their personal/organization owner; grouping does not expose another user's
credentials or turn a personal connection into shared automation authority.

Workflow mapping uses the host's built-in stage registry. A composition cannot
introduce provider-specific stage identities. Each applicable module retains
its mapping and transition opt-in. No mapping or transition is enabled merely
by installing a panel, reading an inbox, or receiving a signal.

## Updates and revocation

Review updates per module. Preserve only the capability intersection allowed
by `update_consent`; source, resource-type and signal-policy changes renew the
affected read/publication consent. Publisher, authentication, MCP or data target
changes renew the affected trust approval and connection consent.

Composition label/order edits do not renew module consent. Adding a module shows
its existing authorized state or requires its own new consent; it grants nothing
through the already-installed logical app. Removing a module does not revoke it
implicitly: offer an explicit separate disable action and retain its records.

Panel-only disable withdraws `panels.render` and panel state, preserving independent
signal/card/workflow grants. Full logical-app disable is an explicit operation
over its current module set. Lock the composition revision and module bindings,
authorize all targets before mutation, and clear their execution grants in one
host transaction. Preserve connections, signals, cards, ownership and history.
Already-disabled modules are idempotent targets. An unavailable definition or
organization policy never prevents withdrawal of retained board grants through
the existing revocation authorization path. A changed composition/binding revision
or lost board revocation authority rejects the whole write. External effects
already executed are not reported as rolled back.

## Existing installations and compatibility

Migration is presentation-only: approve explicit compositions and assign exact
existing keys without renaming or copying definitions, connections, tokens,
bindings, resources, cards or signal receipts. This avoids inventing consent and
preserves existing foreign-key/history references. Re-running the migration is
idempotent; conflicting membership is rejected for administrator review.

Keep the existing flat module catalog and module-addressed API for old SDK/MCP
clients. Add composition discovery separately, including the composition revision
and exact member keys. The new Store resolves members from the same authorized
catalog snapshot and renders ungrouped modules as standalone apps. If discovery
is unavailable, retain the flat catalog with a visible degraded state; do not
infer provider grouping. Rollback removes presentation memberships only.

## Acceptance gates

1. The explicitly grouped provider appears once in the Store, with all module
   revisions, connections, resources and separate feature controls in details.
2. A third provider can be grouped through administrator configuration without
   a host code change, name heuristic or permission promotion.
3. Panel-only disable, module disable and full-composition disable have distinct
   tested effects; no stored card, signal or connection is deleted.
4. Trust/source/resource updates renew only affected consent. A concurrent
   composition/module update rejects stale mutations before any partial write.
5. An idempotent migration and rollback preserve exact existing record identities,
   owner scopes, grants and API responses for old SDK/MCP clients.
6. PostgreSQL HTTP tests and authenticated desktop/mobile browser flows prove
   these operations. Source checks and this document alone do not satisfy them.


## Settings navigation and loading

The board settings page separates basic board configuration, Apps, people and
notifications, and AI/automation. A section mounts when first visited; revisiting
it retains local editor state. App Store overview tiles do not mount native
connection editors. Managing one module uses the full available content width.
Connection editors stay mounted while switching that module's management sections
so unsaved instance and credential-reference fields survive. Leaving the module
or board still ends this local draft; credentials are not persisted by this UI.

Instance settings group permitted routes by workspace setup, people/notifications,
AI/automation, and Apps/integrations. Route permissions and direct links remain
unchanged. Individual settings pages load through React lazy/Suspense.

Board details failures now expose a retry instead of leaving only notification
settings visible. This recovery affordance does not establish the cause of a
browser request abort. Canary browser verification reproduced the abort twice;
the fix must be deployed before its recovery behavior can be verified there.


## Administrator composition editor

`/settings/app-groups` is available only to instance administrators, within
Apps/integrations settings. It creates and updates explicit group metadata,
selecting existing modules from the authoritative discovery endpoint. A module
already assigned to another group cannot be selected. Disabled modules can be
retained/grouped without becoming enabled. Module identities are shown next to
names and versions; a group never derives members from a provider-name suffix.

The group key is immutable during editing. Updates and confirmed ungrouping
submit the observed revision. Failed/unknown mutation results retain the draft
and prevent repeated writes until an explicit reload; reload discards the draft.
An older host without module discovery exposes recovery and no editable form.
This UI is presentation management, not an install, credential, grant or
full-group disable operation. Local desktop/mobile browser tests cover exact
create/update payloads, confirmed ungrouping, conflict draft retention and
non-admin exclusion. Native authenticated HTTP tests separately verify current
administrator authority and preservation of flat module discovery.


### Board group withdrawal

`POST /board/{project_uid}/settings/apps/compositions/{key}/disable` accepts
`expected_revision` for the composition and `binding_revisions` keyed by every
exact member. An unconfigured member uses `null`; the server does not create a
binding for it. All current binding revisions must match before any grants are
changed. This board-scoped operation reuses module withdrawal semantics and
requires current board update/revocation authority. It does not disable global
app definitions or delete connections, resources, signals or cards.

The Store lists affected module keys before confirmation. A failed write or
readback blocks another group write until an explicit successful snapshot reload.
There is no group-level automatic re-enable: modules keep their own consent gates.


Local withdrawal acceptance: 14 native SQLite/PostgreSQL, authenticated HTTP and
migration cases passed. The Store/workflow browser suite passed all 41 cases,
including full group confirmation at 1440px/390px and failed-write reload gating.
TypeScript type checking, changed production UI lint, Ruff and diff checks passed.
These local results do not establish deployed composition migration or provider
integration acceptance.
