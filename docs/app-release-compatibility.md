# App platform compatibility

This guide describes compatibility for this repository version. It does not
identify the version installed on any particular instance. Operators must check
their installed host, SDK, app declarations and database before upgrading or
rolling back. Use the [external service app SDK guide](external-app-sdk.md) for
API, consent, panel and package instructions.

## Versions and supported features

The following version identifiers serve different purposes:

| Identifier | Meaning |
| --- | --- |
| Python SDK package | The client library's available methods and validation rules. Read the selected version in [`provenance.json`](../src/sdk/vendor/provenance.json) and the API/Graph manifests and locks. |
| Panel SDK package | The browser library's available operations. Read the selected version in the same provenance file and the UI manifest and lock. |
| Panel message protocol | The host/frame wire contract; protocol v1 is independent of panel package versions. |
| Declaration schema and app version | The declaration format and the app's approved version, capabilities, trust targets and lifecycle policy. |
| Static package format | ZIP manifest `package_version: 1`, independent of the declaration's app version. |
| Database revision | The schema and retained history used by the host; it is not an SDK or app version. |

A newer SDK package does not enable a capability on an older host. For example,
`resources.list` was added in panel SDK 0.2.1 while retaining protocol v1; an older
host may reject it. Match the APIs an app actually uses to the installed host,
its approved declaration, current connection and board consent. Existing apps
must satisfy those checks independently of the SDK version used to build them.

The [SDK guide](external-app-sdk.md) describes the capability requirements for
resource and signal reads, human interactions, connection management and static
packaging. The [app lifecycle guide](app-card-lifecycle.md) and
[human interaction guide](app-human-interactions.md) describe execution and
response receipts. Accepting an execution request, receiving a webhook,
acknowledging a response, obtaining a runtime permit, reporting process start
and moving a card to Review are distinct operations. None alone approves work
or establishes completion.

## Host compatibility readback

Read the target host's public resource manifest to identify its installed panel
SDK package:

```sh
curl --fail --silent --show-error https://langboard.example.com/panel-sdk-resources.json
```

For a bounded read and comparison against this checkout's reviewed provenance:

```sh
python3 scripts/sdk/read_host.py https://langboard.example.com
```

Add `--check-assets` to read the declared JavaScript and CSS in the same receipt.
Each asset is requested once, with a 15-second timeout and an 8 MiB limit.
The diagnostic accepts same-origin HTTPS URLs without queries or credentials,
does not follow asset redirects, and checks HTTP 200 and the expected content
type before recording byte size and SHA-256. Missing assets, login HTML and
transport failures produce exit 1 independently of a matching SDK identity.
The served asset hashes identify compiled responses; they are separate from the
original package module digest. This option does not authenticate an app or
attempt an operation.

The command makes one public request with a 15-second timeout and a 128 KiB
response limit. Its JSON records both requested and final URLs, observation time,
host metadata and the reviewed package identity. Exit 0 means those package
identities match; exit 2 means different or unknown. Neither result establishes
application compatibility or permissions. HTTP, transport and malformed-response
errors remain errors; do not interpret them as missing consent or retry a write.
Failed reads include `failure`, the HTTP status when available, and `next_action`.
HTTP 401/403 concerns access to this public manifest, not proof of app consent
failure; 404 leaves the installed identity unknown; 5xx requires host health
inspection. Transport failures retain the existing app request identity. HTML
login pages, non-object JSON and oversized responses are invalid manifests,
not SDK version mismatches. The diagnostic never retries or mutates an app.

`host_panel_sdk` contains the installed host transport's `name`, `version` and
`module_sha256`. Production records the package used at build time. Development
reads the currently installed package, independently of an older compiled widget
manifest. The digest is for the package's original `index.mjs`, before bundling;
compare it with `app_panel_module_sha256` in reviewed SDK provenance, not with the
HTTP-transformed JavaScript digest. `version: 1` at the manifest root remains the
resource manifest format, not the SDK package version.

A missing field on an older host means **unknown**, not a version guessed from
source documentation. A 503 means panel SDK resources are unavailable;
it does not prove missing consent. This readback does not identify API/worker
Python packages or establish browser handshake, capability grants or provider
access. Do not use public Vite dependency paths as the production discovery API.

When reporting an integration failure, include the target host, observation time,
manifest package version and digest, app declaration version, selected board and
exact operation/result. Exclude credentials and private payloads. Keep the original
request ID for uncertain writes and reconcile the receipt before retrying.
A successful package read does not establish application permissions.

The native permission lookup is `GET /board/{project_uid}/apps/{app_key}/panel`.
Open the app from that board's app rail for authenticated acceptance. Check the
actual handshake, explicit consent, revocation and board switching with the
intended user; a package or resource manifest alone cannot complete those checks.

### Resource availability

The public manifest identifies compiled widget JavaScript and semantic CSS.
Both referenced URLs must be available; source-module URLs are not substitutes
for those compiled resources. If the manifest returns 503, ask the host operator
to check resource availability. Changing app consent does not repair missing
assets.

| Observation | Meaning |
| --- | --- |
| Public resource manifest 503 | Resources unavailable; host resource inspection is required. |
| Native MCP operation 404 | A tool, route or resource may be unavailable or inaccessible. The status alone does not prove tool removal or hidden resource existence. Preserve the exact operation and request ID when reporting it. |
| App consent or capability denial | Inspect the current user's board/app grant through the native permission lookup. Successful unrelated reads do not establish authorization. |
| Write response timeout | Outcome unknown; reconcile the original request's receipt before retrying. |

### Choose the matching integration contract

Choose the contract for the operation your app needs. An app name alone does not
imply that a new provider adapter is required.

| Operation | Existing contract | Evidence still required |
| --- | --- | --- |
| Read resources already bound to a registered inbound app | Panel `resources.list`; registered declaration, host connection identity and board consent | Current user/board authorization, app and binding revisions, selected resources and revocation |
| Discover or fetch live business data from an external service | The app's service integration; a list of saved host bindings does not fetch upstream data | Service authority and actual returned data |
| Read native provider signals | Panel `signals.list` with explicitly declared sources and an installed supported adapter | Source support, current grants and provider authority |
| Submit observations or execute work | The documented signed ingestion or execution lifecycle | Original request ID, saved receipt, actual operation and authorization outcome |

Source documentation does not establish which operations an installed host
supports. Check the target host before relying on an operation. For an uncertain
write, reconcile the original request's receipt before retrying. Report a host
failure with the exact operation, request ID and observed response; avoid exposing
private resource details.

## Package installation and updates

An administrator can install an official stable release ZIP or upload a manual
ZIP. Official downloads verify the catalog-owned repository, release asset,
version and GitHub-provided SHA-256 digest. Both paths validate the declaration,
archive paths and limits, and static panel assets before approval.

A package contains `manifest.json` and public files under `panel/`. Its manifest
uses `package_version: 1`, `declaration`, `panel_entry` and optional `store`
metadata. Light/dark icons and screenshots must name included images. Panel
ESM imports use relative paths. The host serves the installed static assets;
installation does not deploy an app's separate server service. Keep credentials,
private configuration and executable server code out of the package.

Installation and declaration approval do not grant board capabilities, create
connections, select resources or enable workflow transitions. Complete those
steps explicitly using the [SDK guide](external-app-sdk.md). Applications that
create cards must declare their lifecycle policy.

Updates require the current installation/declaration revision and a strictly
newer declaration version. A declaration-only update preserves installed package
metadata. Same-trust updates retain only compatible previously granted
capabilities; additions require consent. Changes to trust targets revoke affected
connections and grants. Changed MCP tool scopes withdraw affected owner consent.
Changing lifecycle prerequisites can clear archive synchronization opt-ins.
Restoring an earlier declaration or tool set does not restore withdrawn consent.

## Revocation and retained history

Disabling an app clears its board grants and blocks its hosted panel assets.
Reenabling it does not restore withdrawn authority. Connection identity, resource
selection, app ownership, policy and current user access remain independent
checks. Card visibility is also enforced separately; see
[card visibility](card-visibility-transitions.md).

Disconnecting an account or deselecting resources retains cards and historical
receipts. Cleanup remains subject to current ownership, board authority and
revision checks; it cannot expose another user's personal connection. Observed
revocation invalidates affected active panels and clears their cached state.
Reopening requires fresh authorization. A transport error alone does not establish
revocation. Revocation cannot undo an operation already accepted by a provider.

Panel drafts are disposable session data, not durable business records. Closing
before a pending draft write is transmitted can discard the latest value, and
browser restart persistence is not guaranteed. Use saved native receipts to
recover uncertain mutations; do not infer delivery from HTTP success without
the documented receipt, or automatically repeat an uncertain mutation.

## Host upgrades and rollback

Choose compatible API, worker, Graph, realtime and UI versions together. Distinct
component versions may be intentional, but each must support the shared API,
event, authentication and retained-data contracts. Graph requires authenticated
backend callers; see [Graph service authentication](graph-service-auth.md).
For bundled and external realtime selection, use
[bundled or external Phoenix](external-phoenix.md).

Preserve database revision identifiers, current data and execution, delivery,
response, consent and review history during upgrades. A migration that creates
tables successfully does not by itself establish compatibility with retained
rows. An existing installation needs an upgrade path for its actual schema and
history; do not stamp an incompatible schema or delete receipts to force it
through a different migration lineage.

Application rollback requires versions compatible with the retained schema and
history. Some schema downgrades deliberately refuse when durable evidence exists.
Keep a compatible schema when returning to an earlier application version;
rollback must preserve readable history and continue denying revoked authority.
Replacing an app declaration with an older version is not supported by the
normal update API; publish a newer compatible correction and obtain any renewed
consent it requires.

Realtime replacement also needs the editor store and its matching application
data. Preserve current editor documents and the original snapshots before
changing owners. Independently initialized CRDT histories can be mechanically
mergeable while duplicating or restoring stale content; compare their meaning
before recovery. Stop the previous owner before activating its replacement, and
verify retained edits and rollback using the owner handoff guidance in
[external Phoenix](external-phoenix.md).
