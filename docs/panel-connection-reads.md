# Native panel connection-read boundary

The native endpoint is `POST /board/{project_uid}/apps/{app_key}/panel/connections/read`.
It uses the existing authenticated user session, approved app revision and board
binding revision. This endpoint is one implementation prerequisite: the deployed
panel runtime must not advertise `connections.read` until its SDK and bridge are
qualified together. App installation alone supplies no provider access.

The body contains `app_revision`, `binding_revision`, `connection_uid`,
`read_key` and JSON-object `input`. The native input reserves `resource_uid` and
integer `access_revision`. Other input fields belong to the registered adapter's
closed schema. Neither an HTTP method, destination, authorization header nor a
credential can be chosen by the panel. Only trusted server adapters are registered;
an uploaded package cannot register executable host code.

The host checks the current actor, board Read permission, enabled approved app,
declared and granted `panels.render`/`resources.read`, connection ownership and
organization, selected resource, access revision and credential identity. The
resource must already be `granted`. Selection with `unknown` access is rejected
before provider I/O; this read does not verify, grant or update resource health.
Provider connections remain separate from app-authentication inbound principals.

Secret resolution uses the existing native secret store. Authority is rechecked
after secret resolution and after provider I/O, including app/binding/connection,
resource, policy generation and secret revisions. Provider I/O holds no database transaction. A
revoked or changed result is withheld. There is no provider mutation or retry.

Successful reads return `{ "ok": true, "data": ... }`. Safe errors use
`invalid_request`, `forbidden`, `stale_binding`, `unavailable` or `upstream_error`.
A provider rejection retains its actual optional HTTP status, never the response
body, URL, token or secret reference. Error status does not authorize a retry.
Responses use `Cache-Control: no-store` and remain below the SDK response bound.

The initial registered read is `devtron-panel` / `deployments.list`. It binds the
app's existing selected IDs: `app-<id>-env-<id>`, `helm-app-<id>`, and
`helm-release-<sha256>`. External Helm also supplies `helmAppId`, whose exact tuple
must hash to the selected identity. Native CD, installed Helm and external Helm
remain distinct; a panel cannot substitute another selector. Only reviewed
deployment metadata fields are returned. App code owns outcome interpretation.
Management application discovery is deliberately separate from this selected read.

The authenticated panel descriptor includes optional `connection_reads`, a list of
trusted installed read keys for that app. It is empty without current declared and
granted read capabilities; an older descriptor with no field advertises no new
operation. Descriptor responses are not cached. This advertises host support, not
permission to access any connection or selected resource.

The frame bridge advertises `connections.read` only for a nonempty descriptor
list. It forwards the three SDK request fields to the fixed native endpoint with
the descriptor's pinned app/binding revisions. The frame cannot override those
revisions. Closing, cancellation or a scope change prevents dispatch or discards
an inflight response. Native authority/revision denial invalidates the frame; a
typed upstream403 result remains a provider rejection and does not invalidate the
user session. No automatic retry is introduced.

Operational prerequisites remain operator-approved provider configuration, explicit
board resource selection and verified current provider access. Qualification must
include the actual authenticated provider workflow; a mocked-provider contract or
source-only SDK merge is not that acceptance or package publication.
