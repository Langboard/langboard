# Independently versioned SDK consumption

Langboard consumes reviewed packages built in the separate
[langboard-sdk repository](https://github.com/yamonco/langboard-sdk), just as
external apps do. SDK source and standalone package tests belong there; native
host integration tests belong here. Do not restore parallel SDK implementations
or edit vendored package bytes.

## Current packages

| Package | Consumed version | Host declaration |
| --- | --- | --- |
| Python `langboard-sdk` | 0.2.37 | Root and shared Python manifests and lockfiles |
| Panel `@langboard/app-panel` | 0.2.6 | UI manifest and lockfile |

[`vendor/provenance.json`](vendor/provenance.json) records reviewed source commits
and SHA256 digests. Manifests select the active artifacts; historical files and
digest entries are not alternative dependency candidates. These vendored packages
do not establish public npm/PyPI publication or deployed host compatibility.
Panel protocol v1 and package semver are separate contracts.

## Ownership and permissions

The SDK owns transport, public types, validation and receipts. The host owns
identity, authentication, current permissions, consent, resource visibility and
workflow projections. Installing a package grants no authority.

- Keep human administrator and app connection transports separate. App identity
  cannot impersonate a user or grant execution rights.
- Management operations use explicit current revisions. Preparing a disabled
  consent draft grants nothing; enabling capabilities requires explicit consent.
- Card reads require declared and approved `cards.read`, current app ownership,
  connection, selected resources and visibility. Read bounded Markdown pages and
  compare content revisions across pages. Reads do not grant execution rights.
- Panel `signals.list` and `resources.list` require the host read bridge, current
  consent and resource authority. Older hosts can report unavailable. Panels
  cannot infer provider access from package installation; disposal cancels work.

## Execution and interaction contracts

Execution requests carry the authority version from the preceding lookup. A stale
snapshot returns 409 before request/outbox persistence. Reception acknowledgment,
runtime permission, app-reported start and actual process observation are distinct.

Runtime permits require the accepted request, acknowledgment and a privately
persisted random token. The 120-second lease needs explicit current-authority
heartbeats. Recovery can find an existing permit but cannot create or resurrect
one. Revocation, expiry and authority drift require stopping; stop recovery remains
possible after app credential revocation. Start reports are app attestations, not
host process observations.

`request_review` requires retained start/stopped reports and current owning-app
consent, generation and revisions. `read_review` reconciles its immutable receipt.
Neither method approves work or moves a card to Done/Archive. Interaction creation,
answer validation, signed-response reception and external continuation likewise
remain separate operations. Custom text cannot implicitly approve an action.

Archive synchronization requires explicit user consent and a declared lifecycle
policy. External outcome reports retain receipts; reconcile ambiguous results by
readback. The SDK does not automatically retry mutations, resume executions or
perform external archive actions during app registration. Unknown outcomes require
caller reconciliation with the original request identity and inputs.

See the [external app guide](../../docs/external-app-sdk.md) for native endpoint,
installation, lifecycle, consent and acceptance details. Use SDK Git history for
package change history rather than treating old version notes as current setup.

## Updating and verifying packages

Build and test in the SDK repository, then update artifacts, reviewed provenance,
manifests and lockfiles together in a reviewed host change. Preserve original
package/source provenance; record workflow source revisions separately. A newly
computed checksum cannot replace the reviewed digest.

From the host repository root:

```sh
python3 scripts/sdk/verify_python_artifact.py
python3 -m unittest discover -s scripts/sdk -p verify_python_artifact_test.py
node scripts/sdk/check_panel_artifact.mjs
```

The Python verifier checks the active wheel's identity and digest against both
projects and locks before installation. Panel verification resolves the UI's
artifact, verifies its digest/version and exercises its public exports.

The explicit native HTTP acceptance command is
`scripts/sdk/accept_inbound_lifecycle.py`. Set `LANGBOARD_SDK_EXAMPLES_DIR` to the
separate SDK repository's `examples/python` directory and
`LANGBOARD_ACCEPTANCE_DATABASE_URL` to an empty disposable loopback PostgreSQL DB.
Run with the API Python environment. Client and server use the same verified
project-declared wheel. The script exercises real registration, onboarding,
consent, card creation/replay, update, disable and broadcasts without runtime
patches or injected app state. It removes temporary credentials/server/broadcasts;
the caller owns disposable DB cleanup. Local acceptance does not prove public
canary rendering or actual provider actions.
