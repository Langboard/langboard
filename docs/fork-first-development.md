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
