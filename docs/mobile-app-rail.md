# Common mobile activity rail

The existing `ActivityRail` owns both desktop and mobile navigation. Desktop keeps
its 48px left rail. Below `md`, the same item list supplies a 56px bottom row plus
its border and device safe area. The Header does not render a duplicate navigation
drawer when the page registers an activity rail.

`IActivityRailItem.mobile` optionally identifies primary navigation, app shortcuts,
or the final add action. At most two primary actions and two app shortcuts are
directly visible; an add action reserves one app slot. Other visible actions stay
in the existing accessible DropdownMenu. Hidden actions are excluded from both
surfaces. These display roles grant no permissions or installation rights.

The shell reserves body space and publishes `--workbench-bottom-inset` for existing
floating controls, the board work dock, and minimap. Desktop uses zero inset.
Opening a mobile context panel transfers focus into it; explicit dismissal returns
focus to its launching rail button or overflow trigger. Nested menus retain Escape
ownership. Immersive chat retains its existing visual viewport handling.

The general-user catalog now uses separate lazy routes described below. Personal
pin persistence, app-specific launch/consent flows, and authenticated canary
acceptance remain separate requirements. Source browser
fixtures do not establish canary acceptance or a measured performance improvement.

Run the focused shell checks from `src/ui`:

```sh
npm run typecheck
npm run test:workbench-shell
```

The initial complete run passed 34 of 35 cases. The description navigator Home-key
case retained `scrollTop=2` instead of zero; that failure is preserved and is not
waived. The final selected rail, focus, floating-dock, and shell cases passed 12
tests; three viewport cases were rerun after the final border inset adjustment.

The Home-key failure was subsequently reproduced in four of five isolated runs.
Keyboard scrolling now reapplies its target on the next render frame, matching
the existing section-navigation correction. The previous scheduled navigation is
cancelled before every new request. The same Home-key check then passed five of
five runs, and the complete Workbench suite passed all 35 cases in 30.2 seconds.
Type checking and scoped ESLint also passed. Disabling browser scroll anchoring
and using native instant scrolling alone did not reliably fix the failure; those
experiments were discarded. Exact browser-internal causality remains unproven.
These results still do not establish authenticated canary acceptance.

## User catalog boundary

`GET /apps/catalog` is authenticated user discovery, separate from the existing
administrator-only package registry and installation endpoints. It returns a
bounded keyset page (`limit` 1–50, default 25; `after` is the previous key) of
enabled, instance-approved definitions. Only name, description, version,
capabilities, resource types, installation presence/provenance, and a whitelisted
static package icon/screenshot descriptor are projected. Discovery
does not return raw declarations, panel/trust URLs, storage locators, personal
connections, or installation revisions. It downloads no release and initializes
no application. The official catalog is static metadata, not an approval grant.

Without `project_uid`, the instance policy applies. With a board identifier, the
current primary user, board read authority, active organization, and effective
instance/organization policy are checked. Disabled policy returns no installed
or official entries. Responses are `Cache-Control: no-store`; an explicit
`can_manage_installation` flag reflects current primary administrator status and
does not grant board consent or runtime scopes. Existing connection and binding
wizards must still enforce those permissions when the UI launches an app.

Authenticated HTTP coverage passed on SQLite and PostgreSQL alongside the
existing install/update/revision/disable regression: six cases. Coverage includes
non-admin discovery, admin installation denial, paging, sanitization, invalid
limits/cursors, foreign-board denial, organization restriction, role revocation,
instance disablement, and deactivated users. Initial fixture-construction
failures were corrected and retained in local verification logs. The development
JWT fixture emits short-key warnings; these are not runtime deployment evidence.
The lazy `/apps` and `/apps/:appKey` views now use this boundary directly,
without mounting SettingsProxy, requesting administrator setting roles, probing
Ollama, or initializing app panels. `/settings/app-store` redirects to `/apps`;
administrator ZIP/install/update controls remain at `/settings/app-packages`.
Dashboard and board rails expose the final add action. Board-origin discovery
retains the board identifier and links installed app details to the existing board
configuration flow; it does not submit consent automatically.

Exact app-key lookup supports detail deep links beyond the first page. A separate
bounded query for the three host-owned official keys preserves their actual
installation state across catalog pages. Logical keys are merged once per view;
manual packages using an official key keep their manual provenance. Package
icons reuse AppPackageIcon's light/dark handling and lazy loading. Capability
names describe the declaration, not permissions already granted to the user.
English, Korean, Japanese, and Chinese copy is bundled with existing locale files.

Final verification: all 38 Workbench browser cases passed in 32.0 seconds,
including 390px/1440px user discovery, detail navigation, duplicate-key merging,
board configuration links, and disabled-policy legacy redirects. TypeScript,
scoped ESLint, Ruff and diff checks passed. Authenticated SQLite/PostgreSQL and
package install/update/disable checks passed six cases in 10.21 seconds after the
final public-metadata projection. These browser cases use intercepted API
responses and the real store component/routes in a local fixture; the separate
HTTP suite checks the server policy boundary. Neither proves deployment or
canary acceptance. Personal pinning, offline/revocation UX, app-specific launch,
and authenticated canary performance/E2E remain pending.

## Personal shortcuts

`usePinnedApps` extends the existing UserSettingsStore with a per-user ordered
`pinned_apps` list. Each entry stores only an app key and display name, never a
connection, token, URL, board permission, or consent. Reads normalize malformed
entries, deduplicate keys, trim names, and bound the list to 32 shortcuts. The
store detail exposes pin/unpin; the shared shortcut list exposes accessible
move-earlier/later and remove controls. This is browser-local persistence using
the existing store. Server synchronization and cross-device persistence have
not been implemented at this checkpoint; the account synchronization below
supersedes that initial local-only behavior.

`usePinnedAppRail` supplies the same shortcuts to dashboard, board, store, and
settings rails without another catalog request or app initialization. Mobile
keeps its bounded visible row and overflow; desktop keeps its scrollable rail.
Stable app identifiers prevent React key collisions when app display names match.
Unavailable pins remain removable and lead to scoped discovery/configuration.

On a board, an enabled binding alone is insufficient to launch a panel. The
current catalog must positively allow the app, the declaration must still request
`panels.render`, and the enabled binding must grant it. Missing policy metadata,
disabled/revoked bindings, removed capabilities, and missing panels fail closed.
A successful shortcut launch uses the existing AppPanel permission read and
runtime. Otherwise the shortcut opens the app detail with board context; it does
not install, enable, create a connection, or submit consent. Historical cards,
connections, and work are not deleted by pin changes.

Source checks: all 41 Workbench cases passed in 33.0 seconds; TypeScript, scoped
ESLint, and diff checks passed. Added coverage exercises local persistence,
reorder, unpin, account isolation, normalized/bounded input, no API write on pin,
no panel initialization, and positive/negative launch policy combinations.
The store browser tests intercept catalog responses; the launch predicate test
is not a deployed AppPanel handshake. Server preference synchronization, real
board launch/consent acceptance, and authenticated canary performance/E2E remain
open. The native checklist item for the complete persistence/launch flow stays
unchecked until its remaining acceptance is demonstrated.

## Recovering a failed panel launch

A failed permission read previously left the visible panel without a recovery
action until it was closed and reopened. The error surface now reuses the shared
Retry button and translation. Each explicit retry disposes the old session and
performs a fresh permission read before creating a frame. It does not restore
cached authority, submit consent, initialize hidden apps, or run a polling loop.
Revocation still removes the frame and clears its transient state; retrying a
403 response cannot recreate it.

The missing recovery action was reproduced against the original source. All
seven AppPanel browser cases then passed in 16.0 seconds, including initial 503
and simulated network-disconnection failures, retry denied with 403, successful
reconnection through the SDK handshake, and subsequent revocation. Each recovery
case observed four explicit GETs and no writes. TypeScript and scoped ESLint
passed. These are local fixtures with intercepted server responses, not live
canary or external app availability evidence.

The existing administrator installer regression also passed four cases in
7.6 seconds: official install receipt, responsive details, uncertain receipt
write lock, and ordinary-member denial. Six published-package asset cases were
skipped because verified release assets were not supplied. Manual ZIP upload and
the complete deployed store-to-board launch remain separate acceptance checks.

## Account synchronization

`GET /account/app-preferences` reads the authenticated account's shortcuts from
the primary database. `PUT` accepts only `expected_revision` and an ordered list
of at most 32 unique `{key, name}` entries (32-character app keys and 80-character
display names). Extra properties, duplicate keys, invalid identifiers, and foreign
account selectors are rejected. No token, connection, consent or runtime URL is
stored. Unavailable apps remain valid removable shortcuts; a preference grants
no installation or execution authority.

An absent preference is revision 0. Each explicit save increments the revision;
stale writes return 409. The account row serializes initial creation, and existing
rows also use a conditional revision update. Account activation and deletion are
checked in the same primary transaction. GET does not create a row. Responses
use `Cache-Control: no-store`.

The shared rail hook reuses TanStack Query with an account-scoped key, a 30-second
freshness window and five-minute inactive retention. Existing identity cleanup
clears the query cache. There is no polling timer or app initialization. Successful
server reads update the browser cache. Revision 0 preserves existing local pins
until the user explicitly edits them; it does not upload them on login. A saved
empty list clears the account's local pins too.

Writes wait for an authoritative read, are not retried automatically, and disable
editing while pending or failed. A conflict, invalid receipt or lost response
triggers readback and exposes a manual Retry action. A server-committed write with
a lost response is not replayed. Retry reads the account state before enabling
another edit. The app-store explanation and recovery copy use existing widgets
and English, Korean, Japanese and Chinese locale resources.

Migration `f2098cef3604` adds only `user_app_preference` after `e1987bdf2503`;
existing application, identity, card and permission tables are unchanged. A
populated downgrade is refused to avoid erasing preferences. Deploy the migration
and API before the matching UI; an unavailable endpoint leaves edits disabled
while cached shortcuts remain visible. No operating database was migrated during
local verification.

Verification: authenticated preference HTTP checks passed on SQLite and PostgreSQL,
including account isolation, bounded validation, reordered/empty lists, stale
revision rejection, deactivation and two concurrent PostgreSQL writers. Actual
Alembic history replay passed for populated and fresh PostgreSQL schemas, empty
rollback/re-upgrade and populated rollback refusal. Both checks were added to the
existing API/PostgreSQL validation profiles: 356 passed/7 skipped (41.54s) and
52 passed (22.95s). The seven skips require an explicitly supplied Redis server;
development JWT fixtures emit short-key warnings. Full Workbench: 42 passed
(40.1s); final shortcut-only regression: 3 passed (8.7s), including preservation
of 80 Unicode code points without splitting emoji surrogate pairs. The browser test uses
two isolated contexts and intercepted API responses to exercise restoration,
409 readback, response loss without write replay, and offline recovery. Server
HTTP/migration proof and browser fixture proof are separate; authenticated canary
store-to-board launch and performance remain outstanding.
