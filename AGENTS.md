# Langboard company development

Read [CONTRIBUTING.md](CONTRIBUTING.md) and the
[component development policy](docs/fork-first-development.md) before changing code.

- Haseong's split `Langboard/*` repositories own the architecture and source layout.
  Prefer their implementation; document a concrete requirement and evidence when a
  company change must differ. Do not treat the old monorepo main as current parity.
- Use one bounded feature branch/PR per owning component. Link dependent PRs for a
  cross-component feature. Do not accumulate unrelated tasks in one integration PR.
- Preserve existing work and the migration register. Do not delete, reset, rebase,
  force-push, close, or migrate another session's branch/PR without its authority.
- During this transition, keep existing integration PRs as the recovery and canary
  assembly path. Do not merge into `main`. Use owning-repository feature branches when push access is verified. Forks are
  optional; verify the repository map and actual GitHub state before using them.
- Push recoverable changes to their scoped PR. Record upstream baseline, local
  deviations, dependencies, validation, and canary evidence in that PR.
- Source merge, image build, deployment, and authenticated canary E2E are separate
  states. Close/archive a Langboard card only when its acceptance has been verified
  on the deployed canary; update its summary instead of adding routine comments.
- Before app/SDK compatibility handoffs, follow the
  [host readback contract](docs/app-release-compatibility.md#host-compatibility-readback).
  Keep the existing card, request/run identity and exact failed operation; an
  observation timeout does not justify starting a replacement operation.


## SDK and deployment collaboration

Keep internal execution instructions here, outside public SDK guides. Public
contract docs describe externally usable operations, responses and permissions;
they must not contain company deployment coordination or agent status logs.

- Use one existing card summary for the current state; retain links to earlier
  evidence instead of creating routine comments or duplicate compatibility cards.
  Before decoding editor JSON, check `content_truncated`; a partial value is not
  parseable original content. Keep summaries within the serialized response
  bound, not just the visible text length, and verify an untruncated readback.
- Assign one writer per overlapping source or deployment scope. An incoming
  message or timeout does not transfer ownership; reassign explicitly.
- SDK incident recovery has one decision table below. Read the native schema
  before submitting a request. A tool listing does not establish permission in
  the active tool group. Record the native operation separately from its facade
  action, for example `work(record_verification)` versus
  `record_card_verification_evidence`.

| Observed result | Required next action | Do not infer or repeat |
| --- | --- | --- |
| Local snapshot/provenance/test dependency missing | Restore the reviewed inputs; retain a preparation-failure receipt. | No product assertion ran. Do not install unrelated dependencies or query the host again first. |
| Public manifest invalid/unavailable | Retain `read_host.py --output` JSON; inspect host resources or connectivity. | No SDK mismatch, app consent failure or authenticated acceptance established. |
| Input rejected before persistence (`VERIFICATION_INPUT_REJECTED`, HTTP 400, `not_applied`) | Read the schema and current card, then let the mutation owner correct the request. | Do not replace the SDK or replay the invalid payload. |
| Revision conflict | Read the same card with `profile="full"`; retain latest record UID and source sequence. | Compact-profile null is not an empty verification fence. Never replay the conflict unchanged. |
| Permission/tool-group denial | Retain the exact denied operation and inspect the current grant with its owner. | An unrelated successful read does not authorize this operation; denial alone is not a platform outage. |
| HTTP 5xx/transport failure on a read | Retain the status/correlation and route the observed boundary to its owner. | A host/proxy failure does not prove SDK incompatibility or expired consent. |
| Write timeout, HTTP 5xx or unknown outcome | The original mutation owner reconciles the same operation/request before deciding any retry. | Another observer must not replay it or create a replacement build/deployment. |
| PostgreSQL lock timeout | Route the database/test receipt to the API transaction owner. | SDK reinstallation or a larger timeout does not fix self-contention. |

- For Python HTTP failures, retain `NativeApiError.status_code`, or inspect that
  error as `MutationOutcomeUnknown.__cause__` (SDK 0.2.39+). With SDK 0.2.40+,
  retain optional `code` and `request_id` from `NativeApiError` or
  `NativeCommandError`, plus the caller's original request identity. Missing
  fields remain unknown; these fields are not retry/idempotency instructions.
  Do not print response bodies or private payloads.
- Use `python3 scripts/sdk/read_host.py <host> --check-assets --output
  <new-receipt.json>` for public SDK diagnostics. Existing output paths stop
  another read. Reuse the retained receipt while host, source, actor and operation
  are unchanged; retain both receipts when the observation changes. This command
  does not exercise a native MCP call or an authenticated app operation.
- Use this single handoff format in the existing card summary or authorized peer
  message. Link evidence instead of copying logs. Reading a handoff never
  transfers mutation ownership; change the writer explicitly.

```text
Card / target / observed UTC:
Original operation / caller request ID / server request ID (or unavailable):
Current writer / exact write scope:
Source PR + SHA / deployed image digest (or unverified):
Observed result / durable receipt link:
Missing acceptance:
One next action / responsible owner / event or deadline for recheck:
```

- Recheck on a terminal event, changed input, agreed observation deadline or
  contradictory evidence. An observer timeout alone is not such evidence. Reuse
  the original operation/run/deployment ID; do not ask another owner to repeat
  completed checks. Continue independent authorized work when access is missing.
- Project tool results before printing: prefer `structuredContent`, retain the
  complete receipt privately, and display relevant outcome/correlation/continuation.
  Do not print duplicate text envelopes or truncate away failure evidence.
- Follow the [reviewer evidence bounds](docs/card-work-state.md#reviewer-evidence)
  and its native example. `environment` is not a test narrative. A read error is
  not necessarily JSON. Pass plain text to comment writes, never pre-wrapped
  editor JSON; check serialized size and untruncated readback. Independently read
  back the saved verification after its original owner submits it.
- On workspace resume, recheck the Pod UID, retained source/lock hashes, native
  helper identity, registry and required test modules before installation or a
  build. If the managed registry changed, preserve the two receipts and report
  that difference to its owner once; do not override it or repeat installation.
  A missing test dependency or snapshot file is preparation failure, not a
  failing product assertion. Use the offline SDK artifact check without Vite,
  TypeScript, Playwright or a registry; its success proves only that artifact.
- Before promoting a fresh UI development image, verify its compiled manifest,
  JavaScript and semantic CSS routes return HTTP 200 with recorded hashes. Use
  the existing widget-only build. Test the actual default command as UID 1000;
  Vite's generated config cache must remain writable. A healthy process, source
  build or public asset read does not establish authenticated app acceptance.
- In temporary UID 1000 QA containers, check that the test-tool path is writable
  and executable before installation. Stage source with numeric UID/GID 1000
  and `docker cp --archive`; default copy can leave root-owned files that the
  formatter cannot update. Preserve dropped capabilities instead of adding
  privilege to repair test preparation. Preparation failure is not a test result.
- Before deployment edits, obtain the complete server declaration. Virtualized
  editor DOM text can omit offscreen lines and must not replace a whole Compose
  document. Compare the full proposal with Git, save through the authorized
  interface, and verify full server readback/hash before deployment. Source Watch
  declarations must include newly imported config helpers; verify actual container
  file hashes after recreation instead of inferring sync from a running watcher.
- Check actual free disk space before another canary build. When old development
  images accumulate, preserve every running or stopped container's image plus the
  explicit current and rollback image IDs. Limit cleanup to the owning product's
  development image repositories, unreferenced tags older than 24 hours, and
  recoverable Git source commits. Save the exact plan, recheck references and tag
  identity immediately before each removal, and use normal image removal without
  force. Do not remove containers, volumes or global build caches as part of that
  cleanup. Verify protected images and running identities afterward; report actual
  free-space change rather than adding image sizes that include shared layers.

## Internal recovery records and canary acceptance policy

The following company recovery references, access observations and agent acceptance
instructions are internal working guidance, preserved from the contribution guide.
They are not public developer documentation. Historical access observations do not
establish current permission.

## Existing work recovery

Existing monorepo PRs remain the durable recovery source until their contents have
been accounted for. Do not delete branches/worktrees or close a superseded PR until
its replacements and unique changes have been verified and pushed.

| Existing PR | Current role | Treatment |
| --- | --- | --- |
| [#11](https://github.com/Langboard/langboard/pull/11) | Earlier broad product integration | Preserve; recover unmigrated behavior by component and card. |
| [#125](https://github.com/Langboard/langboard/pull/125) | Current integration-to-canary candidate | Preserve as the existing assembly route; do not merge into `main`. |
| [#127](https://github.com/Langboard/langboard/pull/127) | Development runtime and first split-upstream imports | Preserve; separate later work by owner instead of adding every task here. |
| [#126](https://github.com/Langboard/langboard/pull/126), [#128](https://github.com/Langboard/langboard/pull/128) | Another session's DevTron work | Link dependencies where needed; do not rewrite or migrate its work unilaterally. |

For each recovered feature, record its card, original commit/PR, destination PRs,
upstream baseline, retained differences, test evidence and canary acceptance. Start
that record in the card and scoped PR; do not create a competing task database.
Shared Python changes move with their schema revisions and all required consumers.
Preserve deployed migration history; use new reviewed migrations for corrections.

Create migration/policy PRs from the existing integration base. Do not stack them on
unrelated unmerged runtime changes. For unmigrated work, use scoped branches
in the existing monorepo targeting its integration branch; this is temporary,
not a reason to return to one universal development branch.

## Verified repository access and optional forks

On 2026-11-03, the current account had push and admin permission on all seven
private upstream repositories. Each still had `allow_forking=false`; the earlier
fork request returned HTTP 403. No fork or permission change was made. Use
feature branches in those upstream repositories and submit reviewable PRs to
upstream `main`; the curator must not merge into `main` during this transition.

Do not wait for fork-policy changes. Do not create public mirrors. If a fork is
explicitly authorized later, verify its privacy, parent and baseline before
updating the repository map. The current map records direct feature-branch access,
not completed component migration or deployment.

## Delivery and completion

A scoped PR reports actual checks, outstanding dependencies, and rollout order.
The canary assembly records exact component SHAs and image digests; deployed state
must be read back. Test the deployed version through the authorized in-app browser.
A green unit check, merged source, successful image build or healthy container is
not authenticated product E2E.

A Langboard card is complete only when its acceptance passes on canary. Update its
existing summary with the PR/revision, deployment, E2E evidence and remaining
limitations. Archive only evidence-backed completed cards. A policy change or fork
creation alone does not close implementation cards.


## Contributor access and company integration record

## Company development and canary integration

Company development follows the [component contribution policy](docs/fork-first-development.md).
Haseong's split `Langboard/*` repositories own upstream. Current push permission
allows independent feature branches directly in those repositories; forks are
optional and their disabled setting does not block PR contribution. Submit each
component change for upstream review. Canary integration assembles selected
revisions and is not the base for every new feature. Preserve existing monorepo
integration PRs while their unique changes are recovered; do not merge `main`.



## Original contribution step with internal transition instructions

3. For an upstream contribution, submit a pull request to that upstream repository's target branch with a clear title and description. Company canary work targets the integration branch described above and must not merge into `main` during this transition. Reference any issues fixed, for example `Fixes #1234`. Ensure your PR title follows the [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) format.


## Original company component and canary workflow

# Component branches and upstream contribution

## Ownership and branch model

Haseong Kim's split `Langboard/*` repositories own the upstream architecture.
Company changes use bounded feature branches in the owning upstream repository
when push access is available. Optional company forks must retain upstream paths
and Git ancestry; fork creation is not a prerequisite for contribution. The [repository map](upstream-repositories.json)
records the owners and inspected baselines. It is an inventory, not proof that a
fork, upstream sync, migration, or deployment has completed.

Use these branch roles in each owning repository or established fork:

| Branch | Purpose |
| --- | --- |
| `main` | Track upstream `main`; no company feature commits. Advance only when the update is a verified fast-forward. |
| `feat/<card-or-topic>` or `fix/<card-or-topic>` | One reviewable behavior, based on an exact upstream revision or an explicitly linked dependency branch. |
| `integrate/canary` | Assemble selected feature PRs for canary validation. Record component revisions and image digests together; do not use it as every feature's base. |

Independent work can proceed on independent branches. A feature spanning API,
shared models, UI, and Socket uses linked component PRs with explicit contract and
rollout dependencies. Do not split a required invariant into independently shipped
changes. Additive contract changes normally land before their consumers; atomic
changes must be deployed and verified as one recorded combination.

Keep canary-only deployment configuration in the deployment repository branch. Keep company
application integrations in their existing application repositories. Do not mix
company hosts, credentials, evidence captures, or unrelated application features
into upstream product patches.

## Upstream synchronization and reconciliation

1. Fetch the owning upstream repository. Record the exact source SHA, not just a
   branch name or the old monorepo revision.
2. Check whether upstream already solves the card requirement. Prefer its
   implementation and tests. Do not maintain a second implementation by default.
3. Keep each local difference reviewable. State the required behavior, the upstream
   behavior, and the failing/passing evidence that makes a difference necessary.
   Security, authorization, draft preservation and data integrity cannot be dropped
   just to make a patch apply.
4. Reconcile a feature branch against current upstream before proposing it back.
   Published/shared branch history remains intact; use a reviewed merge or a new
   replacement PR rather than force-pushing another contributor's work.
5. Send an upstream-ready feature PR from its feature branch (or authorized fork) to the owning upstream.
   Remove an obsolete local patch through a reviewed change once upstream provides
   it, retaining card and PR traceability.

The integration branch is an assembly surface, not evidence of upstream parity.
No whole-tree copy or unrelated-history merge may silently overwrite product
features. Do not add permanent path-conversion wrappers or compatibility layers
just to preserve the old monorepo layout.

## Existing work recovery

Preserve published history and original evidence while recovering earlier work.
Identify the source revision, destination and compatibility requirements before
retiring a superseded change. Keep applied schema migrations unchanged and use
reviewed follow-up revisions for corrections.

## Repository access and optional forks

Use a feature branch in the owning upstream repository when its maintainers grant
push access. Otherwise follow that repository's contribution and fork policy.
Verify the intended repository and target branch before submitting a pull request;
do not assume another contributor's permissions or publish a private mirror.

## Delivery and completion

Report the checks actually performed, compatibility limits and remaining release
requirements. A source review, successful build and verified deployment provide
different evidence. Follow the destination project's release and acceptance
process before describing a change as delivered.


## Image generation acceptance record

Authenticated canary migration, real provider generation, cross-user runtime
updates, durable restore after deployment and artistic readability remain
separate acceptance gates. Controlled component previews or source tests do not
prove them.
