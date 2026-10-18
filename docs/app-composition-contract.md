# Logical apps and independently authorized modules

A composition groups modules for management. Each module keeps its own identity, connections and
permissions. Availability depends on the installed host version and configuration. For SDK
usage, see the [external app SDK](external-app-sdk.md).

## Board Store and app management

The Store shows overview tiles; it does not eagerly mount every provider's connection editor.
Explicit compositions have one tile and a module overview. Each module's management view has
separate Connections, Permissions, Workflow and Panel sections. Provider connection editors are
loaded on demand when an app is managed, rather than with the Store overview. Once mounted,
connection editors remain mounted across section changes to preserve local drafts. Each section
explains its purpose and authority boundary in English, Korean, Japanese and Chinese. Empty or
whitespace-only app descriptions fall back to the provider summary, then to a generic setup
description for third-party apps. Commands continue to address the exact module key and its
approved revisions. Panel activation does not enable signal publication or create app-owned
cards.

Modern discovery returns membership and modules together. The UI uses one request and one
snapshot; it does not merge grants across modules. If composition discovery fails, the UI
reports degraded grouping and loads the unchanged flat module list. It does not display an old
grouped snapshot as a successful current refresh.

## Current host API

- `GET /settings/apps/compositions`: current administrator lists explicit groups and exact installed module keys, names, versions and enabled status.
- `POST /settings/apps/compositions`: current administrator creates/updates
  `key`, `name`, `description`, ordered `module_keys`, and `expected_revision`.
- `POST /settings/apps/compositions/{key}/ungroup`: revision-checked removal
  of presentation membership, preserving exact module authority records.
- `GET /board/{project_uid}/settings/apps/compositions`: current board reader
  obtains compositions and the unchanged module catalog in one consistent snapshot.

## Composition ownership and validation

The instance administrator approves a host-managed composition with a stable key, fallback name
(at most 80 characters), description (at most 1000 characters), revision, and an ordered set of
existing module keys. An app cannot place itself into another publisher's composition. Neither
provider names nor `*-panel` suffixes, icons, URLs, or `signal_sources` create a composition.
Each module belongs to at most one composition. A composition accepts 1–16 existing module keys.
Duplicate keys, unknown definitions and nested compositions are rejected. Removal from a
composition changes presentation only.

Composition edits use optimistic revision checks and current administrator authorization. An
unavailable or disabled module remains visible in details when it has retained records, with its
current status. Missing module definitions cannot silently fall back to another module or borrow
that module's capabilities.

## Module identity and independent features

Each module retains its exact key, definition version and revision, publisher, trust targets,
resource types, declared capabilities, and board binding revision. One logical tile does not sum
grants or join credentials. Details show each feature's declared availability separately from
its actual consent/status.

| Feature | Meaning | Required boundary |
| --- | --- | --- |
| Signal | Normalized external event or observation | Current publisher/adapter, signed connection, selected resource and publication policy |
| Inbox | Read and triage authorized signals | Current reader, source/resource-type consent and provider resource access |
| App-owned card | Durable workflow object owned by a declared app | Card creation grant, lifecycle declaration, durable ownership and selected resources |
| Rail panel | Optional UI contributed by a module | Panel rendering grant; separate grants for data reads and commands |

Signal receipt does not create a card. Explicit native inbox card creation creates an ordinary
card with an evidence link, not app ownership. A card-owned interaction or archive command
resolves through the owning module key and never the composition key. Native comments,
checklists and relationships remain usable.

## Connection and board management

The details screen displays module-specific connections, selected resources, panel consent, card
lifecycle and workflow mapping together. Every mutation still addresses one exact module and its
current revisions. Connections retain their personal/organization owner; grouping does not
expose another user's credentials or turn a personal connection into shared automation
authority.

Workflow mapping uses the host's built-in stage registry. A composition cannot introduce
provider-specific stage identities. Each applicable module retains its mapping and transition
opt-in. No mapping or transition is enabled merely by installing a panel, reading an inbox, or
receiving a signal.

## Updates and revocation

Review updates per module. Preserve only the capability intersection allowed by the update
consent policy; source, resource-type and signal-policy changes renew the affected
read/publication consent. Publisher, authentication, MCP or data target changes renew the
affected trust approval and connection consent.

Composition label/order edits do not renew module consent. Adding a module shows its existing
authorized state or requires its own new consent; it grants nothing through the
already-installed logical app. Removing a module does not revoke it implicitly. Disable it
explicitly to withdraw grants while retaining its records.

Panel-only disable withdraws `panels.render` and panel state, preserving independent
signal/card/workflow grants. Full logical-app disable is an explicit operation over its current
module set. The operation checks the composition revision and every module binding before
withdrawing grants atomically. Connections, signals, cards, ownership and history remain.
Already-disabled modules are idempotent targets. An unavailable definition or organization
policy never prevents withdrawal of retained board grants through the existing revocation
authorization path. A changed composition/binding revision or lost board revocation authority
rejects the whole write. External effects already executed are not reported as rolled back.

## Existing installations and compatibility

Grouping preserves existing module keys, definitions, connections, tokens, bindings, resources,
cards and signal receipts. It grants no new consent. The flat module catalog and
module-addressed API remain available to clients. Ungrouped modules appear as standalone apps.
If composition discovery is unavailable, the Store shows the flat catalog with a degraded state
rather than inferring groups from provider names.

## Administrator composition editor

`/settings/app-groups` is available only to instance administrators, within Apps/integrations
settings. It creates and updates explicit group metadata, selecting existing modules from the
authoritative discovery endpoint. A module already assigned to another group cannot be selected.
Disabled modules can be retained/grouped without becoming enabled. Module identities are shown
next to names and versions; a group never derives members from a provider-name suffix.

The group key is immutable during editing. Updates and confirmed ungrouping submit the observed
revision. Failed/unknown mutation results retain the draft and prevent repeated writes until an
explicit reload; reload discards the draft. An older host without module discovery exposes
recovery and no editable form. This UI is presentation management, not an install, credential,
grant or full-group disable operation.

## Settings navigation and loading

The board settings page separates basic board configuration, Apps, people and notifications, and
AI/automation. Revisiting a section retains local editor state. App Store overview tiles load
connection editors only when needed. Managing one module uses the full available content width.
Connection editors stay mounted while switching that module's management sections so unsaved
instance and credential-reference fields survive. Leaving the module or board still ends this
local draft; credentials are not persisted by this UI.

### Board group withdrawal

`POST /board/{project_uid}/settings/apps/compositions/{key}/disable` accepts `expected_revision`
for the composition and `binding_revisions` keyed by every exact member. An unconfigured member
uses `null`; the server does not create a binding for it. All current binding revisions must
match before any grants are changed. This board-scoped operation reuses module withdrawal
semantics and requires current board update/revocation authority. It does not disable global app
definitions or delete connections, resources, signals or cards.

The Store lists affected module keys before confirmation. A failed write or readback blocks
another group write until an explicit successful snapshot reload. There is no group-level
automatic re-enable: modules keep their own consent gates.

### Board composition detail navigation

An explicitly declared composition opens one full-width module detail surface. Its persistent
module navigation displays only the authoritative member keys; the active module retains its
exact identity, version, binding and app revision. Connections and selected resource status,
per-module permissions, workflow, and panel consent share this surface. Workflow and inbound
management expand within the active module. Group withdrawal still confirms every member and
submits the composition revision plus the complete binding revision snapshot.

Only explicitly visited modules and management sections mount their forms. Visited connection
and consent views remain locally mounted while hidden so unsaved fields and unknown-write locks
survive section/module navigation. They are discarded when leaving the app detail. No global
draft store or prefetch is introduced. Existing workflow/inbound dirty and busy callbacks block
navigation. Policy-disabled modules permit retained grant withdrawal; panel consent cannot add a
new grant while the definition is unavailable.
