# Development and resource boundaries

Use the existing company FractalOps BuildPlane and shared BuildKit for dependency
installation, compilation, container images, browser fixtures, and full test suites.
Local work is limited to source edits, Git, and inexpensive checks that require no
dependency installation. Do not download browsers, create local test databases, or
start local service stacks as a fallback for a queued or failed remote request.

Use the existing callers documented in `scripts/image_build/README.md` and the
profiles in `.fractalops/image-build.json`. Preserve the exact source SHA, request
ID, execution number, and result receipt. Observe the same admitted request;
an observation timeout does not authorize a duplicate build. Do not introduce
another builder, hosted-runner fallback, or product-owned transport. GitHub Actions
orchestrates the company execution; migrating legacy runner installation steps
must preserve their test coverage and use the approved central execution contract.

Use the existing authenticated in-app browser for deployed canary acceptance.
Local fixtures, admission success, image builds, and deployment health do not
replace this acceptance. Close a card only after its deployed behavior is verified.

Keep one working checkout for the active integration. Before removing a worktree,
verify that its committed source is reachable from an existing remote ref and
inspect tracked changes, untracked files, ignored files, and nested worktrees.
Preserve uncommitted source and unique acceptance evidence. Remove generated
dependencies and transform caches, then remove disposable worktrees without force.
Retain compact receipts and remote run/artifact links instead of copied dependency
trees or repeated full logs. Never delete application data, credentials, or another
active session's work as part of build cleanup.

Deliver source through review and the existing canary GitOps path. Do not merge
product `main`, bypass checks, or change central runner, OIDC, capacity, or approval
policy to make a product request pass.
