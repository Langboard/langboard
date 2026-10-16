# Native GlitchTip connections

The native authenticated board API supports GlitchTip SaaS and self-hosted
HTTPS instances. Operators explicitly approve exact base URLs in
`APP_CONNECTION_ALLOWED_BASE_URLS` (comma separated). There are no company
endpoint defaults. This is a trusted outbound-network permission: approve only
instances the host is allowed to contact. Redirects and environment proxies are
disabled. Each request has a 15-second timeout and a 256 KiB decoded response
limit. It does not implement DNS pinning for operator-approved hosts.

## Credential and resource ownership

First create a personal or appropriately scoped native Secret Reference through
the authenticated browser secret-input flow. Store a GlitchTip **API token**,
not a project's ingest DSN. Tokens are resolved only inside the host and audited;
the connection stores only the canonical `secret://ref/{uid}` reference. Request
schemas never accept raw tokens or DSNs. A URL-shaped DSN is rejected before
external I/O. API scopes should be restricted to the required metadata reads.

GlitchTip 6.2.6 API token scopes and project visibility are separate boundaries.
Connection registration and project discovery require `org:read` and
`project:read`; issue reads also require the provider's event-read permission.
The token has no project selector. Native project lookup uses organization
membership, so team membership alone does not guarantee that another project
in the same organization is inaccessible. Treat such a credential as an
organization-wide read credential. Langboard's selected resource and current
board authority restrict consumption inside Langboard; they do not narrow the
credential's upstream authority. Never reuse a central administrator token or
describe a selected Board Binding as a project-scoped provider token.

Authenticated routes under
`/board/{board_uid}/settings/apps/glitchtip/connections`:

- `POST`: `{instance_url, credential_reference}` verifies organization metadata
  read access and persists an owner-scoped connection.
- `GET`: lists the current user's connected instances, 25 per page with `after`.
- `GET /{connection_uid}/resources`: lists organizations; `organization` selects
  that organization's projects. `cursor` retrieves the next page, maximum 25.
  Only resource ID, slug and name are returned.
- `POST /{connection_uid}/projects`: `{organization, project_slug,
  expected_revision, expected_resource_revision}` re-reads the selected project from GlitchTip and binds it
  to the current board. A listing is not authorization evidence for a write.
  Existing resources require their current `access_revision`, including re-selection
  after removal. A new resource uses a null resource revision.
- `GET /{connection_uid}/projects`: returns stored project selections and their
  revisions, including unselected history, 25 per page with `after`.
- `POST /{connection_uid}/projects/{resource_uid}/remove`: `{expected_revision}`
  unselects one currently authorized board resource without external API I/O.
- `POST /{connection_uid}/disconnect`: `{expected_revision}` disconnects the
  reusable connection and invalidates external access for its bindings. Existing
  selections, cards and board workflow configuration remain intact. The shared
  Secret Reference is not revoked implicitly.

Every operation uses current primary board Update authority. Connections are
owner scoped. External response handling rechecks board authority, connection
revision (including instance URL) and current secret revision/state. Persistence
locks current authority, credential reference and connection rows. First board
binding creation uses the existing board row lock. Bindings stay disabled;
resource selection does not grant signal capabilities or enable transitions.
Provider pagination links supply only a validated cursor; their URLs are never
followed. Redirects, denied/deleted resources and in-flight revocation cannot
produce a new binding.

## Official diagnostic surface and remaining work

GlitchTip's official Streamable HTTP MCP endpoint is `{instance_url}/mcp`,
supporting OAuth dynamic client registration or API tokens. The host returns
this endpoint as metadata; it does not claim MCP is enabled or authenticated.
Raw errors, stack traces, events, performance and logs stay with that official
MCP. This implementation introduces no diagnostic MCP wrapper or raw-event
replica.

Board Apps includes native connection selection, explicit secure token-input URL
issuance, metadata discovery, project selection/removal and confirmed disconnection.
Raw token values are entered only on the authenticated secret-input page; the Apps
UI receives only completion state and the canonical reference. Input completion
is checked on user request; no polling or background scanning is added. Every
provider list has explicit pagination. Stored selections must be loaded before
checkboxes become editable. Revocation/failure clears stale resource views, and
responses from a previous board/user/permission scope are ignored.

## Explicit issue observations

After selecting a project, explicitly enable board read access with current
connection and binding revisions. This grants `resources.read` and
`signals.read`; selection alone does not enable them.

`POST /{connection_uid}/projects/{resource_uid}/issues/refresh` requires current
connection and resource access revisions. It rechecks project identity and
provider access, then reads at most 25 issues per page. An explicit validated
cursor retrieves the next page. Current board authority, connection, both binding
and resource revisions, secret state and the signal watermark are checked after
external I/O before observations are committed. No background full scan is added.

The signal is `issue.status_observed` with server UTC observation time. An observed
unresolved or resolved status is not the provider's reopen, regression or
resolution occurrence timestamp. Unresolved observations can supply blocker and
Inbox context; resolved observations alone do not approve verification, close a
card or move it to another workflow column. Signal consumption and native card
creation retain current permission and private-card ownership boundaries.

Scheduled access health, provider OAuth onboarding and optional resolution-proof
workflow transitions remain unfinished. Live SaaS/self-hosted acceptance also
remains unverified. Native authentication, SQLite/PostgreSQL storage, host Vault,
observations, Inbox/card boundaries and browser fixtures are tested using mock
official API transport. These checks are not live deployment evidence. A central
installation must supply an authorized test project and an API-token SecretRef;
then authenticated provider reads, binding, observations and target-environment
readback must be verified independently of deployment health.

Official contracts:

- [GlitchTip OpenAPI](https://app.glitchtip.com/api/openapi.json)
- [Integration tokens](https://glitchtip.com/documentation/integrations/)
- [Official GlitchTip MCP](https://glitchtip.com/documentation/mcp/)


## Optional browser error ingest

The browser uses the official `@sentry/browser` SDK, pinned at 11.6.0. Set these
public **build-time** values in the deployment's private configuration:

- `PUBLIC_SENTRY_DSN`: public project ingest DSN, never an API/admin/release token.
- `PUBLIC_SENTRY_ENVIRONMENT`: deployment environment (for example `canary`).
- `PUBLIC_SENTRY_RELEASE`: the exact source revision shipped in this UI build.

An empty DSN leaves telemetry disabled and does not import the SDK chunk. SDK
initialization does not block rendering. Invalid configuration logs a generic
warning and leaves the application usable. HTTPS DSNs may include a deployment
path prefix. No instance address or credential is embedded in repository defaults.

SDK 11 uses `dataCollection` controls rather than the removed `sendDefaultPii`
option. This integration disables user, cookie, header, body, query, GraphQL,
AI, database/queue data, local variables and source-context collection. Only
browser error handlers and deduplication integrations are enabled. Tracing,
replay, console collection, feedback and automatic session instrumentation are
not enabled.

The final error projection retains deployment identity, exception type and
bundled `/assets/*.js` coordinates. Exception text, function names, arbitrary
file URLs, request, user, contexts, extras and breadcrumbs are omitted. This
trades detailed error text for protection of card/comment/credential content.
The browser acceptance fixture verifies a real SDK envelope after an uncaught
error and verifies absence of synthetic secrets added to error text and scope.
It also verifies no SDK load or ingest request when no DSN is configured. Current-session API network/timeout failures and HTTP 5xx are reported through the same initialized transport; expected cancellations and obsolete-session responses are excluded before reporting. Errors occurring before the optional SDK finishes loading are not buffered.

Handled transport errors retain only allowlisted `transport_code` values
(`ECONNABORTED`, `ERR_NETWORK`, `ETIMEDOUT`, `ERR_BAD_RESPONSE`) and an integer
HTTP status from 400 through 599. The final projection validates these tags
again and drops every other tag. No request path, card identifier, response
body or arbitrary error code is retained. Official SDK envelope tests cover
503 diagnostics, invalid metadata and confidential content in error/config/scope.
These diagnostics do not identify a failed card and do not establish the cause
of a browser cancellation excluded by the API interceptor.

This ingest configuration is independent of a board's GlitchTip read connection.
The latter still requires an API token SecretRef, selected resources and explicit
capability consent. A synthetic local SDK envelope is not proof of deployed
canary collection or of Signal Inbox/card delivery.

References: [GlitchTip JavaScript setup](https://glitchtip.com/sdkdocs/javascript/)
and [Sentry SDK migration contract](https://github.com/getsentry/sentry-javascript/blob/develop/MIGRATION.md).


The production UI Docker build declares the three `PUBLIC_SENTRY_*` arguments.
Operators must supply them as public build metadata through the approved private
image profile; runtime container environment alone cannot change an already-built
Vite bundle. Empty defaults keep collection off for standalone installations.
A source-map upload token is a different build credential and is never accepted
as one of these public arguments.
