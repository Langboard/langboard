# Native GitHub App onboarding

The native host starts registration with authenticated `POST
/board/{uid}/settings/apps/github/manifest`. It returns the official GitHub registration form
target and Manifest JSON. Optional organization targets are strictly bounded GitHub slugs.
Current board update authority is required.

The host stores a random nonce bound to actor, board and an independent random HttpOnly,
SameSite=Lax, production Secure cookie for ten minutes. Callback completion requires the same
authenticated actor, board and cookie and claims state atomically before exchanging a one-time
code. Wrong session/board, missing/expired state, current permission revocation and replay fail
before GitHub exchange. Ambiguous exchange failure is never retried automatically.

The Board Apps onboarding panel POSTs `{state, code}` to the authenticated completion endpoint
after GitHub returns to the board URL. The host exchanges the code only at the fixed GitHub API
endpoint, with redirects disabled and a finite timeout. Credential response fields are validated
then stored as one personal Secret Reference; AppConnection retains only its canonical URI.
Returned data contains the pending connection and installation URL, never credential values.
Registration is distinct from installation: `pending` and `installation_verified: false` are
intentional.

The Manifest requests read permissions only. Webhook delivery starts disabled; activating it
requires explicit GitHub configuration and the receiver contract below. No manual PAT, private
key or webhook URL is required by this registration path.

## Availability and limits

Registration and repository inspection do not activate a connection. The Manifest does not
activate webhooks or automatically complete an installation return. Signed lifecycle processing
requires explicit webhook setup. Availability on an instance depends on its installed host
version and configuration.

Official protocol: [Register a GitHub App from a
manifest](https://docs.github.com/en/apps/sharing-github-apps/registering-a-github-app-from-a-manifest).

## Installation and repository inspection

Native authenticated `GET /board/{uid}/settings/apps/github/installations/{id}/repositories`
requires `connection_uid`, expected `account_id` and a bounded page. Host checks current board
update authority and Connection owner/state, resolves its secret only in runtime, and signs an
RS256 App JWT. The fixed GitHub API re-reads the installation and validates App ID, installation
ID, expected account identity/type and suspension state.

A metadata-only ephemeral installation token lists up to 100 repository identities per page.
Each repository owner must match the verified account. Token is never persisted or returned and
is revoked after inspection; revocation failure prevents a successful result. Current board
authority and Connection state/reference are rechecked after external IO. Responses contain
repository IDs/names and pagination, not tokens or arbitrary external URLs. Inspection does not
create a Binding or mark the Connection connected.

## Incremental repository resources

Authenticated GET and PUT `/board/{uid}/settings/apps/github/resources` share current board
update authority. GET returns repository resource metadata and a snapshot revision. PUT accepts
one current-owner Connection, installation/account identity, up to 25 positive IDs to add and 25
to remove, and `expected_revision`. Empty, overlapping or duplicate deltas are rejected.
Additions mint a metadata-only token restricted to the requested IDs and require an exact
returned ID set and count; the token is revoked before persistence. Missing, substituted or
duplicate external repositories fail without changing selection.

Stale resource snapshots or changed Connection identity reject selection changes. Removal only
deselects the targeted board/Connection repository; re-addition reuses its existing row. Other
repositories and other boards remain intact. New Binding stays disabled; resource access does
not grant workflow capabilities, enable webhook processing or connect the App.

## User authorization and installation discovery

The Manifest now registers the board URL as an OAuth callback. Native authenticated POST
`/board/{uid}/settings/apps/github/authorization` takes a current-owner `connection_uid`; POST
`/authorization/complete` takes `{state, code}` and the HttpOnly path-scoped session cookie.
State is bound to actor, board, Connection revision and a PKCE S256 verifier for ten minutes,
with atomic one-time claiming. The fixed GitHub token endpoint exchanges the code, then `/user`
and `/user/installations` identify the GitHub user and visible installations for this App. Each
discovery page is bounded to 100 installations with `next_page` explicitly returned. The user
access token is neither stored nor returned; its revoke must succeed. Current host authority and
Connection identity are checked again after external IO.

Completion issues an opaque five-minute installation proof only after token revocation and
current host checks. Its server-side cache record binds actor, board, Connection revision and
each non-suspended installation/account pair. Repository additions require this proof before
GitHub IO and check it again with the locked current Connection before persistence. Missing,
expired, mismatched or suspended proof rejects without changes. Removal alone does not require a
proof, so a board owner can deselect resources after external access disappears.

The proof is a bounded snapshot of GitHub user visibility, not a continuous user revocation feed
or permission to activate a Binding. GitHub App repository access is independently revalidated
for every addition. User access revocation after proof issuance may remain unobserved for up to
five minutes. Older registered Apps require their callback URL updated to use this flow.

Official user flow:
https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-user-access-token-for-a-github-app

## Board Apps onboarding UI

The existing settings App Store mounts the GitHub panel. Manifest registration uses the official
external POST form; return and OAuth callbacks target the board settings route. The panel
removes one-time code/state from the URL before exchange and never retries an ambiguous exchange
automatically. Current-tab onboarding metadata is scoped to host user and board in
sessionStorage; secrets and proofs are not persisted there. OAuth results show account
installation choices, bounded repository paging and revision-checked selection deltas. Unloaded
repositories remain selected. Read-only users cannot start or exchange callbacks. Four locales
share the same onboarding controls.

Automatic installation-return processing is unavailable.

## Reusable Connection discovery

Authenticated GET `/board/{uid}/settings/apps/github/connections` requires current board update
authority and lists only the current host user's GitHub Connections in pending/connected state.
Fifty-row keyset pages expose Connection UID, App ID and state; no credential URI or material is
resolved or returned. Other users' connections and revoked/disconnected rows remain hidden.
Native OAuth and resource mutation recheck authority independently after selection.

The settings panel can choose an existing Connection in a fresh tab, fetch more connections and
return to new registration. Switching clears installation/proof/ repository edit state. Install
navigation re-reads current App metadata via the native endpoint below. Account verification and
existing installation selection remain independent.

## Current App metadata and install navigation

Authenticated GET `/board/{uid}/settings/apps/github/connections/{uid}/app` uses current board
update and Connection ownership/state checks. The shared runtime SecretReference/App JWT helper
calls fixed GitHub GET `/app`, verifies the exact App ID and bounded slug, then rechecks host
authority and Connection revision. Only Connection UID, App ID, slug and a host-constructed
GitHub installation URL are returned. Arbitrary response URLs, credential fields and JWT are
omitted. The UI fetches this endpoint on install click for both new and reused Connections.
Reading App metadata does not activate lifecycle delivery.

Connection health and job receipts are available through the routes below; registration alone
does not establish installation health.

## Explicit resource health refresh

Authenticated POST `/board/{uid}/settings/apps/github/resources/refresh` takes Connection UID
and resource snapshot revision. Current board authority and owned Connection are required.
Selected repositories are refreshed in explicit cursor pages of at most 25. Each
same-installation/account group uses one restricted ephemeral token. Successful access becomes
granted/healthy (or degraded for an archived repository). Missing external evidence, suspension,
denial, transport or credential failure becomes unknown/unavailable. This does not prove
uninstall or revoke selection.

Before saving, the host rechecks authority, Connection identity and resource revision. Other
boards, unselected resources, mapping/grants/activation and Connection state remain unchanged.
UI has an explicit refresh control and refreshes Store status after success. Explicit refresh
works independently of configured lifecycle delivery.

Health continuation uses a selected-resource UID cursor and current snapshot revision on every
request. The UI requires another explicit click for each page; there is no background full-board
scan. Ambiguous group failure marks the group unknown/unavailable; it does not identify a
particular removed repository.

## Installation discovery pages

Authorization start accepts a strict page integer from 1 to 10000. It binds that page to the
actor/board/Connection/session nonce and PKCE context; completion cannot override it. Each
authorization exchanges one code, reads that 100-row installation page, revokes its user token
and issues a proof for only the returned installations. The UI offers previous/next pages via
fresh authorization. This avoids retaining user access tokens but requires a GitHub
authorization redirect per page. Returning to an earlier page requires new verification; only
current-page choices are shown. No automatic all-installation scan is introduced.

## Lifecycle verification boundary

The Manifest supplies `/apps/github/events` as the webhook target with delivery inactive and no
default event subscriptions. Activating delivery and selecting GitHub event subscriptions are
explicit provider configuration steps.

`POST /apps/github/events` accepts signed lifecycle events and `check_run` events. Lifecycle
types are `installation`, `installation_repositories` and `ping`. Requests require original JSON
bytes, an `X-Hub-Signature-256` HMAC and a valid `X-GitHub-Delivery` UUID. Bodies are limited to
1 MiB; compressed bodies and duplicate security headers are rejected. App identity, current
connection and stored secret authority are checked. A payload cannot choose a Langboard actor.

A valid delivery is durably recorded and returns HTTP 202. Repeated delivery IDs must retain the
same verified payload; conflicting deliveries return 409. Acknowledgment does not mean that
asynchronous health or signal processing has finished. Lifecycle invalidation does not grant
signal access, select a repository or enable a workflow transition. Installed worker
configuration and current authority still determine processing.

Current board managers who own the connection can inspect `GET
/board/{uid}/settings/apps/github/connections/{connection_uid}/health` and `GET
/board/{uid}/settings/apps/github/connections/{connection_uid}/jobs`. Job history is paged, at
most 25 per page with `after`; it reports retained job state separately from current resource
health. These reads expose no credentials.

A configured resource also accepts signed check events at `POST
/apps/github/boards/{board}/connections/{connection}/resources/{resource}/events`. Current board
managers can read its signal receipts at `GET
/board/{board}/settings/apps/github/connections/{connection}/resources/{resource}/signals`. The
event must match the selected installation/account/repository and current connection/resource
authority. A check observation does not itself approve work or move a native card.

See the [external app SDK](external-app-sdk.md) for capability consent and signed receipts, and
[Secret References](secret-reference-contract.md) for credentials.
