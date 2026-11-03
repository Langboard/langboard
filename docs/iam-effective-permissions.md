# Native board and card read permission inspection

`GET /settings/users/{user_uid}/effective-permissions?project_uid={project_uid}`
requires the existing administrator authentication and `SettingRoleAction.UserRead`
checks. It inspects one existing user's current native board work-read scope for
one board, optionally narrowed to one caller-readable card. It does not create a target session, switch the caller's identity,
change grants, or offer hypothetical role overrides.

The caller must currently be able to read that board through the API channel.
An unreadable, unrelated, missing, or deleted board returns the same 404, before
target permissions are loaded.
Administrator status alone does not expose every board. Missing/deleted targets
return 404. Missing authentication returns 401; missing admin or user-read
permission returns 403. The response uses `Cache-Control: no-store`.

A successful response contains `inspection` with `user_uid`, resource type/UID,
`action: "read"`, `allowed`, `sources`, `reason`, API `channel`.
Results reflect current account activation/deletion and board ownership,
assignment and read grants. Revocation is observed on the next request.

For an allowed result, source facts can identify `project_owner`,
`administrator_project_membership`, and/or `direct_project_role` (with the role
UID). A direct role includes the native wildcard behavior. These facts explain
the native owner's grant or the current membership-plus-role/admin path. Denied
results contain empty sources and `reason: "native_project_read_scope_denied"`.
No email, password, profile, card body, token, or unrelated board information is
returned.

## Explicit limits

This inspects the existing board work-read scope. Other routes and resource
actions may apply different authorization. Native card visibility and private
ownership govern the optional card inspection described below. Other actions,
wiki, attachments, bots, API keys, managed sessions and hypothetical simulations
are outside this endpoint. It does not supply grant/revoke history, grantor,
expiry or an authoritative denial-policy graph.

The endpoint exposes owner, administrator/member and direct project-role paths.
It does not expose group-inheritance or exception provenance, reinterpret group
directory membership as a grant, or model organization/tenant hierarchy
enforcement.

## User details panel

Select a board from the readable dashboard catalog in the user-details panel.
No board is selected automatically, and hidden boards are not inspected. Refresh
rereads the endpoint. Loading, denied, unavailable, failed/retry and empty catalog
states hide previous results. Changing boards or users, or closing the dialog,
clears stale results. Grant source labels and the scope limitation are available
in the existing four settings locales.

## Current card read inspection

Add `card_uid={card_uid}` to the same endpoint to inspect `action: "card.read"`
and resource type `card`. Existing administrator/user-read middleware and current
caller board scope remain required. Before target lookup, the caller must be
authorized to read that exact board/card. Unknown, deleted, unrelated and hidden
cards return the same 404 without title, body or target grant metadata.

The target must pass both current native board read scope and native card
visibility checks. Shared cards require current membership; internal cards also
require the configured internal audience; private cards require ownership and
a permitted channel. The card channel comes from verified authentication
middleware: native sessions use `human_ui`, API credentials retain `api`.
This evaluates the target in that channel without creating a target session.
Original board responses retain action `read` and channel `api`.
Allowed card responses include source `native_card_visibility`; denied responses
contain no sources and reason `native_card_read_scope_denied`. No title, body or
whisper content is returned. Card read does not imply whisper-content access.

In the user-details panel, search readable cards with at least two characters
and explicitly select a card. Board changes clear card selection and search.
Search results refresh when the panel opens; loading or failure hides previous
options. Switching cards, boards or users, or closing the dialog, clears stale
inspection results.
