# External Devtron account setup

The authenticated board settings API reuses the existing verified connection and
secret-reference services. It does not accept raw credentials, arbitrary HTTP
methods or caller-selected provider endpoints. The exact instance base must already
be operator-approved. The current actor needs board Update access; organization
connections also require current organization management authority and a matching
board organization.

`POST /board/{project_uid}/settings/apps/devtron-panel/connections` accepts the
current `app_revision`, `instance_url`, existing `credential_reference`, optional
`organization_uid`, and optional `deployment_scope`. The scope is one existing
native application/environment pair, installed Helm application ID, or external
Helm tuple. Existing closed native validation rejects mixed or malformed selectors
before provider I/O. An omitted scope verifies application inventory access.
Repeated successful verification reuses the current account receipt; this is not
permission to retry an unknown write outcome automatically.

`POST /board/{project_uid}/settings/apps/devtron-panel/connections/{connection_uid}/discover`
accepts `app_revision`, optional `search`, `offset` and `size` (1 to 250). It returns
bounded application/environment metadata only. A result that echoes the resolved
credential or secret reference is rejected. Discovery does not select resources,
grant provider access, create credentials, or change their health.

Both operations reuse preflight, post-secret and post-I/O current-authority checks and release
database transactions during provider I/O. Responses use `Cache-Control: no-store`.
Errors expose only a stable code and, for a provider rejection, its optional actual
HTTP status. Provider bodies, credentials and private exception messages are omitted.

## Verify one selected deployment resource

Use the existing board resource selection API first. Selection persists an
`unknown` access receipt; inventory, installation and account registration do not
grant resource access.

`POST /board/{project_uid}/settings/apps/devtron-panel/connections/{connection_uid}/resources/{resource_uid}/verify`
accepts `app_revision`, `binding_revision` and the selection's `access_revision`.
For an external Helm release, also supply `helm_app_id` using its existing
cluster/namespace/release tuple. The tuple must hash to the selected release ID.
Native application/environment and installed Helm selections reject that extra
selector. The request cannot replace the selected resource or choose an endpoint.

The actor needs current board Update, connection management authority and secret
access. The approved app and enabled board binding must both allow `resources.read`.
Current app, binding, selected-resource identity/revision, connection, policy and
secret are checked before dispatch and again before saving the same receipt.
Verification owns its transaction boundaries; provider I/O runs outside them.

A successful bounded deployment-history read sets access to `granted`, including
an empty history for an accessible identity. Health remains `unknown`: permission
to read does not establish deployment health. Provider HTTP 401/403/404 sets access
to `denied`; other provider/transport uncertainty sets it to `unknown`. Neither
failure can preserve a previous grant. Health becomes `unavailable` on failure.
The receipt advances `access_revision`, leaving selection, sibling resources,
workflow mapping and capabilities unchanged. Authority/revision changes suppress
the result without changing the receipt. Concurrent requests with the same input
revision cannot both save.

Responses contain `ok`, a `resource` receipt with its UID, access state, health,
access revision and binding revision, and an optional safe `error` code/status.
Provider failures use HTTP 200 with `ok: false`; native authority and revision
errors remain HTTP 403/409. No provider response body or automatic retry is exposed.
If a write response is lost, read the existing selection receipt before deciding
whether to verify again. A changed access revision is not permission to replay the
old request; every explicit new verification must use the current receipt.

The board app settings form provides account registration, deployment selection,
explicit read verification and withdrawal. Authenticated acceptance against the
deployed provider remains required for the complete app workflow. See [panel connection reads](panel-connection-reads.md) for
the independent SDK bridge and registered read advertisement contract.


## Manage accounts in board settings

Open the Devtron app's Connections section. The account owner can register a
personal connection or select an organization they currently manage that matches
the board. Registration, resource selection, read verification and disconnect
are distinct actions with explicit confirmation. Resource selection alone never
grants access. The form displays read access separately from service health.

`GET /board/{project_uid}/settings/apps/devtron-panel/connections` returns only
external Devtron account metadata owned by the current actor, or by the explicitly
selected `organization_uid` after current management checks. The page size is
1–50 (default 25); `after` is the last connection UID. An exact `connection_uid`
lookup cannot be combined with `after`. The response includes the current app
revision, available ownership scopes and board binding metadata. It excludes
secret references, raw credentials and unrelated inbound connections.

`POST /board/{project_uid}/settings/apps/devtron-panel/connections/{connection_uid}/disconnect`
requires `expected_revision`. It disconnects the account and revokes that account's
resource access across all linked boards in one transaction. Other accounts are
unchanged. Saved selections, stored credentials and the external provider account
remain. Owners can inspect and withdraw a connection after the app is disabled;
current board Update and account management permissions are still required.

For personal credentials, `POST /board/{project_uid}/settings/apps/devtron-panel/secret-input`
accepts the current `app_revision` and reuses the existing secure input page.
`GET` on that path plus `/{input_uid}` with the same app revision reports its
owner-scoped status. Both calls check current board authority. The form validates
an exact same-origin input URL and stores only the returned secret reference.
Organization connections reuse an accessible shared credential reference.

The existing resource selection list also returns `app_revision`, `binding_uid`,
`binding_revision` and `resource_types` from its current authorization snapshot.
The form checks app, connection identity, binding and resource revisions again
before writes. Unknown outcomes stop further changes until explicit refresh;
refresh reads current receipts and never replays the original mutation.
