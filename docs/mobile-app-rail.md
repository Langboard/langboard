# App discovery and mobile navigation

Use the workspace rail to navigate and open app shortcuts. On desktop the rail
appears on the left; on mobile it appears along the bottom, above the device safe
area. Up to two primary actions and two app shortcuts are visible directly. When
an Add action is present, it occupies one app slot. Additional actions are
available through **More**. Hidden actions do not appear in either view.

Opening a mobile context panel moves focus into it. Dismissing it returns focus
to its launching button or the More menu. Nested menus handle their own Escape
key. Floating board controls reserve space above the bottom navigation.

## Find and configure apps

Open **Add** in the dashboard or board rail, or visit `/apps`. App details are
available at `/apps/:appKey`. Starting from a board keeps that board's context
and offers its existing configuration flow for installed apps. Viewing a catalog
entry does not install an app, connect an account, or grant permissions.

Instance administrators manage ZIP installations and updates at
`/settings/app-packages`. The older `/settings/app-store` address redirects to
`/apps`. General app discovery does not require installation privileges.

Names, descriptions, capabilities, resource types, installation provenance and
public package images describe the available app. A listed capability describes
what the app requests; it is not a permission already granted to the user.
Manual packages retain their manual provenance even when they use an official
app key. Package icons follow the current light or dark theme.

## Catalog API

`GET /apps/catalog` requires an authenticated user. It accepts `limit` (1–50,
default 25), `after` (the previous returned key), and optional `project_uid` for
board context. Use `key` for an exact app detail lookup. Follow returned cursors
explicitly rather than assuming the first page contains every installed app.

Without a board identifier, instance policy applies. With a board identifier,
current board read access, organization state and effective instance/organization
policy also apply. Disabled policy returns no installed or official entries.
Responses use `Cache-Control: no-store`. The `can_manage_installation` flag
reflects current administrator authority; it does not grant board consent or
runtime scopes. Official catalog entries are suggestions rather than app approval.

The discovery response excludes raw declarations, panel and trust destinations,
storage locators, personal connections and installation revisions. Listing an app
does not download a release or initialize its panel.

## Personal shortcuts

Pin or unpin an app from its detail page. The shortcut list provides **Move
earlier**, **Move later** and removal controls. The same ordered shortcuts appear
in dashboard, board, app-store and settings rails; mobile overflow keeps all
remaining shortcuts reachable.

A shortcut stores only an app key and display name. It never stores a connection,
token, runtime URL, board permission or consent. Unavailable apps remain removable.
Changing shortcuts preserves existing cards, connections and work.

On a board, a shortcut launches a panel only when the current catalog allows the
app, its declaration includes `panels.render`, and the enabled board binding
grants that capability. The panel performs a fresh permission read. Missing policy
metadata, a revoked binding, removed capability or absent panel prevents launch.
Otherwise the shortcut opens the app detail with board context. It does not
install, enable, connect or consent automatically.

## Account synchronization and recovery

Shortcuts synchronize through the authenticated account's preference API:

- `GET /account/app-preferences` returns `revision` and `pinned_apps`.
- `PUT /account/app-preferences` accepts `expected_revision` and an ordered list
  of at most 32 unique `{key, name}` entries. App keys contain at most 32 characters;
  display names contain at most 80 characters.

Extra properties, duplicate keys, invalid identifiers and foreign account
selectors are rejected. A never-saved account has revision 0. Each explicit save
increments the revision; a stale save returns 409. GET does not create a saved
preference. Responses use `Cache-Control: no-store`.

Until the first explicit account save, existing browser shortcuts remain visible
and are not automatically uploaded at login. After a saved preference is read,
it replaces the browser's shortcut cache. Saving an empty list clears it. Account
preferences support restoration on another device after signing into that account.

Editing waits for a successful current read and is disabled while a save is
pending or failed. After a conflict, invalid save response or lost response, use
**Retry** to read the account state before editing again. A save with an uncertain
outcome is not automatically replayed. If the API is unavailable, cached shortcuts
remain visible while edits stay disabled. There is no background polling.

Operators must make the account-preference database migration and API available
before deploying the matching UI. A downgrade with saved preferences is refused
to prevent data loss.

## Recover a failed panel launch

Use **Retry** after a permission-read or network failure. Each explicit retry
starts a fresh permission read and disposes the previous session. It does not
reuse cached authority or submit consent. A denied response cannot recreate a
revoked panel; observed revocation removes its frame and transient state.

## Install or update a ZIP

An administrator can install an official release or select a manual ZIP at
`/settings/app-packages`. Manual uploads must be nonempty and no larger than
8 MiB. Closing the manual installer discards its file selection; select the file
again after reopening. A confirmed installation also clears the selection.
The installer cannot be closed while its installation is pending.

Updates require the current installation revision. After a conflict or lost
response, installation writes stay locked until **Refresh** reads the current
installation. Recovery does not automatically repeat an upload. Installation
still requires separate board configuration and permission consent. See the
[external app SDK guide](external-app-sdk.md#static-package-installation) for
package contents and compatibility requirements.
