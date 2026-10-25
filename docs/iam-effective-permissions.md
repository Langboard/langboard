# Native board and card read permission inspection

`GET /settings/users/{user_uid}/effective-permissions?project_uid={project_uid}`
requires the existing administrator authentication and `SettingRoleAction.UserRead`
checks. It inspects one existing user's current native board work-read scope for
one board, optionally narrowed to one caller-readable card. It does not create a target session, switch the caller's identity,
change grants, or offer hypothetical role overrides.

The caller must currently pass `CardService.resolve_work_visibility_contexts`
for that board through the API channel. An unreadable, unrelated, missing, or
deleted board returns the same 404, before target permissions are loaded.
Administrator status alone does not expose every board. Missing/deleted targets
return 404. Missing authentication returns 401; missing admin or user-read
permission returns 403. The response uses `Cache-Control: no-store`.

A successful response contains `inspection` with `user_uid`, resource type/UID,
`action: "read"`, `allowed`, `sources`, `reason`, API `channel`.
Only the existing native work visibility resolver decides `allowed`. It rereads
account activation/deletion and board ownership, assignment and read grants
from the primary database. The route also reads the current target and direct
role from the primary. Revocation is observed on the next request, without
waiting for a cached session or readonly replica to expire.

For an allowed result, source facts can identify `project_owner`,
`administrator_project_membership`, and/or `direct_project_role` (with the role
UID). A direct role includes the native wildcard behavior. These facts explain
the native owner's grant or the current membership-plus-role/admin path; the
route does not duplicate the permission engine. Denied results contain empty
sources and `reason: "native_project_read_scope_denied"`. No email, password,
profile, card body, token, or unrelated board information is returned.

## Explicit limits and remaining IAM work

This is the existing board work-read scope, not an assertion that every route
or resource action shares identical authorization. Native card visibility and
private ownership govern the optional card inspection described below. Other actions, wiki,
attachments, bots, API keys, managed sessions and hypothetical simulations are
outside this endpoint. It does not supply grant/revoke history, grantor, expiry,
or an authoritative denial-policy graph because the selected producer does not
provide those facts.

The existing work visibility resolver has owner and administrator/member paths
and direct project roles; it does not expose group-inheritance or exception
provenance. The endpoint does not invent those semantics or reinterpret group
directory membership as a grant. Full group/policy/exception inspection and a
unified action-specific engine remain separate work requiring shared producer
ownership. The test's unrelated board is an isolated resource owned by another
user; it is not proof of organization/tenant hierarchy enforcement, which this
producer does not model. No complete IAM checklist or deployment acceptance is
claimed.

## Verification

`src/api/tests/settings_users/test_effective_permissions.py` uses native HTTP,
JWT, auth/role middleware and database reads. It covers direct read and wildcard,
role and member revocation, owner and admin-member paths, target deactivation
and deletion, hidden/unrelated/missing resource denial, caller membership and
setting-role revocation, unauthorized and unauthenticated callers, and response
minimization. It reuses the SQLite/PostgreSQL fixture from the existing business
activity test. PostgreSQL runs only when an explicitly disposable
`LANGBOARD_FILE_TEST_DATABASE_URL` is supplied.

## User details panel

The existing UserDetails dialog includes a bounded board-read inspection panel.
It reuses `useGetProjects` and the readable dashboard catalog, with no automatic
board selection and no requests to inspect hidden resources. Select one board
to inspect; refresh rereads the native endpoint. Loading, denied, unavailable,
failed/retry, and empty catalog states hide previous results. Board/user changes
and closing the dialog cancel the request; request identity also prevents stale
results from rendering. Grant source labels and the scope limitation are
translated into the existing four settings locales. The public response omits
the internal engine class name.

The Chrome suite imports the production UsersPage/UserDetails panel through the
existing fixture and dashboard controller. HTTP responses are fixtures, so this
is actual component/browser evidence, not authenticated canary evidence. The
existing `playwright.user-login-range.config.ts` and existing UI contracts CI job
include the new spec; no job was added.

## Current card read inspection

Add `card_uid={card_uid}` to the same endpoint to inspect `action: "card.read"`
and resource type `card`. Existing administrator/user-read middleware and current
caller board scope remain required. Before target lookup,
`CardService.resolve_readable_card(..., metadata_only=True)` authorizes the
caller for that exact board/card. Unknown, deleted, unrelated and hidden cards
return the same 404 without title, body or target grant metadata.

The target must pass both current native board read scope and the same native
card resolver. Shared cards require current membership; internal cards also
require the configured internal audience; private cards require ownership and
a permitted channel. The card channel comes from verified authentication
middleware: native sessions use `human_ui`, API credentials retain `api`.
This evaluates the target in that channel without creating a target session.
Original board responses retain action `read` and channel `api`.
Allowed card responses include source `native_card_visibility`; denied responses
contain no sources and reason `native_card_read_scope_denied`. No title, body or
whisper content is returned. Card read does not imply whisper-content access.

The user-details panel reuses native scoped card-context search with at least
two search characters and explicit card selection. Board changes clear card
selection/search. Search is keyed by actor/session and refreshed on mount;
loading/failure hides cached options. Inspection identity includes card UID,
so switching cards/boards/users and closing the dialog suppress stale grants.
Browser fixtures cover card request identity, denial, switches, unavailable
resources, retry and failed/empty search. Component/browser fixture evidence
remains separate from native JWT/middleware/database evidence.
