# App platform compatibility and release gates

Verified source snapshot: 2026-10-11. Package availability, source tests and a
deployed release are separate evidence. No production compatibility is claimed
from this table. Update it when an artifact, declaration or migration changes.

## Reviewed host and SDK combination

| Component | Reviewed identity | Evidence and limitation |
| --- | --- | --- |
| Host API integration source | `878541385a6779591224acd7f75344267a042d54` | Public app creation to independent HITL exercised on PostgreSQL; not a deployed image |
| API migration head | `d3c6fd51a7e9`, parent `c2b5ec40f6d8` | One Alembic head; archive sync defaults disabled, and archive/response history is durable. Active sync and stored history protect downgrade boundaries |
| Python SDK | `0.2.27`, source `05440591f1b3d03b9d4037f0b38d67a336012bc6` | Reviewed wheel, both host Python projects pinned; `scripts/sdk/verify_python_artifact.py` passes. Includes archive outcomes/reads and renewed signal consent on resource type changes |
| Host panel SDK | `@langboard/app-panel@0.2.2`, source `2ad4ea3c5277393e52c5f5d3b1b8b6c59006ceed` | Vendored tarball and UI lockfile. Message protocol remains v1 |
| Example Monitor | package `0.1.1`, source `5d4d312b2c8e709cfc455d0cfb65eafa47a8a832` | SDK `0.2.23`; baseline declaration schema 2, app version `1.0.0`, signals only. HITL/card creation require a separately approved definition and consent |

Exact reviewed artifact digests:

| Artifact | SHA-256 |
| --- | --- |
| Python SDK `0.2.23` wheel | `eab73a2ca2b1ec47946000cafddfe5ada484d86db44de89823c89cb4ecbb423e` |
| Python SDK `0.2.26` wheel | `57e31b39c2bea7c09fddd47617e23726e9b472984d0ad856ee24d159f7e3be69` |
| Python SDK `0.2.27` wheel | `afee6ebe8a8ef4426960c0d3cb5bca6f9be977cde10664385c2981d8dd06bd07` |
| Panel SDK `0.2.2` tarball | `b63dda02bb181aa6ca8c19d8246bd977529a748f429d212d2bfbae937d9a58ea` |
| Panel SDK `0.2.0` tarball used by existing apps | `6e232e4e055760a6dd30d1e07a86de491af60e36471d6da4705512d6aeb3661b` |
| Example Monitor `0.1.1` wheel | `7ce265d8009f346263d3f157a8406d981da71b45b5dcd3dd6d1e4d2ac21b6cf9` |

Host provenance is in `src/sdk/vendor/provenance.json`; independent Monitor
provenance is in `src/api/tests/apps/vendor`. Package imports validate SHA-256
and module location. Python package semver, panel protocol version, declaration
schema, app version and database revision must not be treated as one version.

## Existing independent apps

All three packages are `0.1.0`; their default declaration is app version
`0.1.1`, schema 1, with `panels.render` and `signals.read`. Explicit
`--workflow-cards` selects app version `0.2.0`, schema 2, card creation/read and
mandatory lifecycle policies in CLI and HTTP declarations. GitHub interactions
are a separate optional profile. GitHub/GlitchTip server helpers consume
`@langboard/app-server@0.1.0`; Dokploy retention requests no external action.
All three retain panel SDK `0.2.0`.

| App | Reviewed source | Native external resource | Fresh source test result |
| --- | --- | --- | --- |
| GitHub Panel | `98ebc3ed23c776f3da772670446846d3423e6587` | `repository` | 16 passed |
| GlitchTip Panel | `f0c43017c0cc8e7af50e4008b526e0078b28bc8a` | `project` | 13 passed |
| Dokploy Panel | `d0766448164b31636610d8e01f207fa7ad8c1dfb` | `application`, `compose` | 7 passed |

These local tests cover static service/manifest, host widget rendering, signal
requests, cancellation and optional adapter behavior. They do not prove the
`0.2.0` app to `0.2.2` host combination in an authenticated deployed browser.
Keep that cross-version gate open; do not force a package upgrade solely to hide
the untested combination.

## Native integration evidence

`test_signal_inbox.py`, `test_app_card_interactions.py` and the independent
Monitor case in `test_app_card_creation.py` pass together: 7 cases, 9.93 seconds.
The card creation suite passes 26 cases, with 25 SQLite combinations skipped
because native sequence/readiness uses PostgreSQL. The actual PostgreSQL cases
run against a disposable database. No live provider mutation is performed.

Signal, Inbox and app-owned cards remain independent. Inbox-created cards do not
acquire app ownership. Native app creation stores ownership and selected-resource
audit records in the card transaction. HITL response, webhook delivery, reception
ACK, explicit workflow transition and runtime attestation remain separate facts.

## Deployment and rollback gates

`Check App Contracts` is a separate native API contract workflow. It uses the
configured runner and a disposable PostgreSQL service, verifies the reviewed
SDK against both Python project lockfiles before installation, and preserves
JUnit results. It includes signed delivery, bounded retries, trust changes and
revocation, alongside card and HITL contracts. The same selected cases passed
locally after `uv sync --frozen`, without a wheel import-path override:
117 passed, 25 skipped, including installed SDK 0.2.25 public HTTP archive consent, delivery, outcomes and migration
guards. SDK artifact verification tests: 7 passed.
That result does not establish a successful remote workflow run. Draft PRs
remain skipped; a ready same-repository PR or explicit dispatch is required.

Before release approval, record immutable API, worker, socket and UI image
digests with their source revisions; record the installed database head and the
consuming app versions. An older running source revision is not evidence for
this candidate. Different API/worker images may be expected, but their contract
and source compatibility must be verified explicitly.

Required remaining evidence:

- Approved build/release job for the exact preserved candidate, including its
  result and image provenance. Draft or Skipped checks are not passing CI.
- Existing database upgrade with retained ownership, response and transition
  history. Destructive downgrade is not a rollback strategy; retain a compatible
  schema and verify the old application before choosing rollback images.
- Authenticated FHD/mobile host panels with existing and current app packages,
  current consent, trust-target changes and connection/resource revocation.
- Independently deployed receiver, real provider observations and explicit
  action readback, including duplicates, timeout/unknown outcomes and recovery.
- Image rollback with readable history and denied revoked authority. No test
  may merge a review PR or silently mutate external resources.

Runner topology, private build endpoints, credentials and organization-specific
deployment configuration belong in the operator's delivery system, not this
public repository. The host uses generic runner configuration. A missing runner
or product release workflow remains an explicit delivery blocker.

The installed SDK 0.2.26 archive API subset passed 13 PostgreSQL cases, including receipt reads after unknown and applied reports and rejection after consent withdrawal. The full 117/25 gate above records the preceding 0.2.25 run; it has not been rerun for 0.2.26.

Installed SDK 0.2.27 panel signal scope subset: 14 authenticated HTTP cases on SQLite/PostgreSQL, including resource-type filtering, scoped cursor rejection and renewed consent on resource scope expansion. SDK standalone: 82 tests. Artifact verifier: 7 tests. Earlier full-gate results remain historical until rerun.
