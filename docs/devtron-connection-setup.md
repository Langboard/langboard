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

Both operations reuse preflight and post-I/O current-authority checks and release
database transactions during provider I/O. Responses use `Cache-Control: no-store`.
Errors expose only a stable code and, for a provider rejection, its optional actual
HTTP status. Provider bodies, credentials and private exception messages are omitted.

This component does not add a settings form, verify selected-resource access, adopt
a new SDK runtime, or advertise panel `connections.read`. Those steps and actual
authenticated provider acceptance remain required for the complete app workflow.
