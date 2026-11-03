# Fork-first company development

## Ownership and branch model

Haseong Kim's split `Langboard/*` repositories own the upstream architecture.
Company changes belong in matching private `yamonco/langboard-*` forks, retaining
upstream paths and Git ancestry. The [repository map](upstream-repositories.json)
records the owners and inspected baselines. It is an inventory, not proof that a
fork, upstream sync, migration, or deployment has completed.

Use these branch roles in each established fork:

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

Keep canary-only deployment configuration in the deployment fork. Keep company
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
5. Send an upstream-ready feature PR from its company fork to the owning upstream.
   Remove an obsolete local patch through a reviewed change once upstream provides
   it, retaining card and PR traceability.

The integration branch is an assembly surface, not evidence of upstream parity.
No whole-tree copy or unrelated-history merge may silently overwrite product
features. Do not add permanent path-conversion wrappers or compatibility layers
just to preserve the old monorepo layout.

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
unrelated unmerged runtime changes. While forks are unavailable, use scoped branches
in the existing repository targeting its integration branch; this is temporary,
not a reason to return to one universal development branch.

## Fork establishment status

On 2026-11-03, all seven upstream repositories were private and had
`allow_forking=false`. GitHub rejected the native `Langboard/web-ui` fork request
with HTTP 403, `The repository exists, but forking is disabled.` No company forks
were created by that request. Repository-specific permission approval is pending;
do not change organization-wide policy or publish these repositories.

After authorized creation, verify each fork is private, its `parent.full_name`
matches the intended upstream, and its default branch head matches the recorded
upstream baseline. Update the map only after readback. Do not replace a blocked
private fork with a public mirror or unrelated repository copy.

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
