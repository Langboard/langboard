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
- Assign one writer per overlapping source or deployment scope. An incoming
  message or timeout does not transfer ownership; reassign explicitly.
- Before a handoff, record target/UTC, original operation/request ID, owner/write
  scope, source PR/SHA, deployed image digest, host readback/app declaration/board,
  evidence, missing acceptance, and the next action/owner. Include the existing
  run/deployment ID and the observable condition for checking it again.
- For SDK incidents, attach the JSON from `python3 scripts/sdk/read_host.py
  <host> --check-assets` to the existing evidence location and link it from the
  existing card summary. Record the exact native operation separately from the
  facade action (for example, `work(record_verification)` calls
  `record_card_verification_evidence`). A tool listing is not proof that the
  active tool group permits that operation. Keep HTTP failures distinct from
  SDK incompatibility, app consent and verification acceptance. Reuse an
  existing receipt when source, host, actor and operation have not changed.
- On observation timeout, reconcile that same operation before retrying. Do not
  replace a build whose outcome is unknown or ask a peer to repeat completed
  checks without changed input or contradictory evidence. With unavailable access,
  record the exact denied operation and continue independent authorized work.
- For reviewer evidence, a compact card profile can hide a stale record. Do not
  interpret its null verification as an empty record fence. Read the same card
  with `profile="full"` and use its latest record UID plus current source sequence
  before a reviewed submission. Preserve a conflict; never replay it unchanged.
- Resolve SDK incidents from the existing receipt before contacting another
  owner. Invalid verification input rejected before persistence is
  `VERIFICATION_INPUT_REJECTED` (HTTP 400, `not_applied`): correct the input after
  a fresh read. A revision conflict requires the full record and current source
  sequence. An unknown/5xx write requires reconciliation of that original request;
  neither SDK replacement nor a new request is a recovery step by itself.
- On workspace resume, recheck the Pod UID, retained source/lock hashes, native
  helper identity, registry and required test modules before installation or a
  build. If the managed registry changed, preserve the two receipts and report
  that difference to its owner once; do not override it or repeat installation.
  A missing test dependency or snapshot file is preparation failure, not a
  failing product assertion. Use the offline SDK artifact check without Vite,
  TypeScript, Playwright or a registry; its success proves only that artifact.
- Handoffs should request one next action against the existing operation and
  link the durable evidence. Avoid copying full logs or repeatedly asking for
  status. Check again on a terminal event, changed input, an agreed observation
  deadline or contradictory evidence. Keep the exact denied operation visible
  while continuing independent work; do not turn a scope denial into a platform
  outage claim.
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
