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
| Python SDK package | The client library's available methods and validation rules. This source pins `langboard-sdk` 0.2.37 for the API and Graph. |
| Panel SDK package | The browser library's available operations. This source pins `@langboard/app-panel` 0.2.6. |
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
