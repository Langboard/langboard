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

Before asking another product team for the host version, read the target host's
existing public resource manifest:

```sh
curl --fail --silent --show-error https://langboard.yamon.io/panel-sdk-resources.json
```

For a bounded read and comparison against this checkout's reviewed provenance:

```sh
python3 scripts/sdk/read_host.py https://langboard.yamon.io
```

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

Keep one existing integration card/comment as the handoff record. Update that
summary instead of creating repeated status comments or parallel compatibility
cards. Include the target host, UTC observation time, manifest version/digest,
app declaration version, selected board and the exact action/result. Keep source
checks, served package readback and authenticated browser acceptance separate.
Retain the original request identity for uncertain writes and reconcile its
receipt before retrying. A handoff needs a specific failed operation or missing
proof, not a generic request to repeat the other team's entire investigation.

Use the following compact record in that existing comment. Keep the latest
summary at the top and retain links to earlier evidence:

```text
Target / observed UTC:
Original operation / request ID:
Owner / exact write scope:
Source PR + SHA / deployed image digest:
Host readback / app declaration version / board:
Current result / evidence link:
Missing acceptance / next action / responsible owner:
Existing run or deployment ID / condition for checking it again:
```

Assign one writer per overlapping source or deployment scope. Independent work
can proceed in parallel, but a handoff must identify the existing branch, PR and
running operation before starting another. On timeout, reconcile that operation
using its saved ID; observation failure does not prove execution failure. If
access is unavailable, record the exact denied operation and required access,
then continue independent work. Do not send repeated unchanged handoffs or
create a replacement build while the existing build's outcome is unknown.
Close the product card only after its deployed version passes the required
authenticated canary acceptance; source tests and merge receipts stay separate.

The native permission lookup is `GET /board/{project_uid}/apps/{app_key}/panel`.
Open the app from that board's app rail for authenticated acceptance. Check the
actual handshake, explicit consent, revocation and board switching with the
intended user; a package or resource manifest alone cannot complete those checks.

### Resource packaging and failure ownership

A fresh development image must provide the compiled widget manifest, JavaScript
and semantic CSS before promotion. Run `yarn build:panel-widgets` during image
construction; a source-only Vite server does not generate those files. Verify
HTTP 200 for `/panel-sdk-resources.json` and both referenced assets from the
candidate image, and record their hashes. A healthy process or successful source
build does not establish that those routes work. The host owner fixes a 503
resource-packaging failure; app teams must not work around it with source-module
URLs or change consent settings.

Keep failure categories separate:

| Observation | Supported conclusion and next owner |
| --- | --- |
| Public resource manifest 503 | Resources unavailable; host owner checks image packaging and asset routes. |
| Native MCP operation 404 | Tool, route or resource may be unavailable or inaccessible; preserve the operation and request ID for host diagnosis. Do not assert tool removal or hidden resource existence. |
| App consent or capability denial | Inspect that user's existing board/app grant through the native permission lookup. Successful unrelated reads do not establish authorization. |
| Build, deployment or write observation timeout | Outcome unknown; original operation owner reconciles the saved run/request before replacement or retry. |

Read the complete server configuration before editing deployment settings.
Virtualized editor DOM text can omit offscreen lines and must never be used as
an entire Compose document. Compare the complete proposed declaration with its
Git source, save through the authorized interface, and read back the complete
server declaration and hash before deployment. A source Watch also needs every
new configuration import in its declared sync paths. Verify the actual container
file hashes after recreation; a running watcher alone is insufficient.

At handoff, name the next observable condition, not merely "waiting": existing
run terminal state, required access becoming available, candidate asset routes
passing, or authenticated acceptance completing. Recheck that same operation
when the condition changes. Do not ask another team to repeat completed checks
without changed input or contradictory evidence. Only an explicit reassignment
transfers an overlapping write scope.

### Resolve the operation before assigning an adapter

Use the existing endpoint and exact failed operation to assign ownership. An app
name alone does not imply that Langboard needs a new provider adapter.

| Operation | Existing contract | Evidence still required |
| --- | --- | --- |
| Read resources already bound to a registered inbound app | Panel `resources.list`; registered declaration, host connection identity and board consent | Current user/board authorization, app and binding revisions, selected resources and revocation |
| Discover or fetch live business data from an external service | The app's service integration; a list of saved host bindings does not fetch upstream data | Service authority and actual returned data |
| Read native provider signals | Panel `signals.list` with explicitly declared sources and an installed supported adapter | Source support, current grants and provider authority |
| Submit observations or execute work | The documented signed ingestion or execution lifecycle | Original request ID, saved receipt, actual operation and authorization outcome |

Source support is not deployed support. Reproduce against the recorded host/image
before requesting an adapter or claiming compatibility. On an uncertain write,
the caller owns receipt reconciliation; the host owner investigates a specific
host error. Transfer only that bounded failure with its existing card, request ID
and evidence. A timeout, repeated question or incoming status message does not
transfer write ownership. Reassign explicitly before another worker starts the
same source edit, deployment or mutation.

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
