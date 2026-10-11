# Phoenix main compatibility audit

Authoritative snapshots: main `45171c109`, fixed PR 683 base
`bdcc1eeb943235024e17d08aebd86f01e547695d`, candidate
`6a4ece1d4f2d83f178730df5af7c1b8fab30da2b`. The fixed candidate is preserved.

## Reuse and reconstruction boundaries

| Candidate-owned area | Reuse | Required work on current main |
| --- | --- | --- |
| SDK wheel/tarball/panel packages and provenance | Preserve exact published versions and hashes as contract inputs | Restore the SDK facade and dependency wiring; none of the eleven SDK delta paths exists on main |
| App manifest, governance, resource binding and consent models | Reuse contract semantics and test cases | Reconstruct prerequisite tables/services and ordered Alembic migrations before applying the twelve app-model and eleven app-service delta paths missing from main |
| App cards, human interaction and execution review/outbox | Reuse permission, revision, lease and receipt requirements | Reconnect to current Python GraphApprovalRequestService/InternalBotRunService; preserve native card/comment/checklist behavior and current authority checks |
| App MCP catalog/consent and settings review routes | Reuse public API contracts | Rebuild registration and dependency wiring; all five route delta paths are absent on main |
| React app panels, card interactions and consent settings | Reuse components after dependency audit | Restore native API/SDK controllers and map wire events to Phoenix; only twelve of sixty UI delta paths exist on main |
| Graph transport | Keep response/schema semantics | Use Phoenix Graph.StreamClient/ResumeClient and current Python claim endpoints, not Node request/event implementations |
| Socket run, approval and recovery adapters | Retain domain acceptance requirements | Keep BoardChat/Editor RunWorker, ResumeWorker and AcceptedRecovery, current run leases, project/user checks and Kafka ownership |

Main does not contain the earlier AppRegistry/CardVisibility/CardWorkState
foundation. Applying only the candidate delta is therefore insufficient.
Copying the whole legacy candidate would delete Phoenix source and restore Node
package, Docker and workflow paths. Neither action is an accepted reconstruction.

PR 684 contains the Graph authentication/history bridge and the initial
app database foundation described below. It remains an incomplete
reconstruction, rather than full app/HITL acceptance. It does not prove app/HITL acceptance, Phoenix compilation
or operational deployment. All existing review PRs and the local canary union
remain preserved; no merge or protected deployment-reference update is made.


## App database foundation reconstructed

The first dependency slice reuses these models from fixed candidate
`6a4ece1d4`: Organization, AppDefinition, AppConnection, BoardAppBinding,
AppGovernancePolicy, AppComposition and AppCompositionMember. The original
AppRegistry name denotes a service; the persisted declaration is AppDefinition.
No provider-specific identifiers, adapters or company defaults are added.

Migration `a7c9e31b6204` follows current main head `0cc6abc4b020`. It adds seven
new tables and does not rewrite existing card, run, approval or Phoenix tables.
Fresh model metadata and the migration both enforce connection ownership/state,
instance/organization policy scope and one app binding per board/app key.
Bindings default to disabled, connections to pending. Registry declarations
remain administrator approval records; importing their model does not install
an app or grant any execution permission.

For new uniquely keyed fields, redundant `index=True` flags are removed: the
UNIQUE constraint already provides an index in PostgreSQL. Foreign-key/query
indexes are retained. This avoids duplicating write/storage overhead while
preserving lookup and uniqueness behavior. The shared ORM is unchanged.

The PostgreSQL migration regression applies upgrade on the native user/project
prerequisites, persists personal and organization connections, rejects invalid
ownership/state/policy and duplicate board bindings, checks index duplication,
then downgrades without deleting existing user/project rows and upgrades again.
This is the new migration delta; it does not claim a replay of all historical
migrations or a migration from the old canary schema.

Remaining dependencies include organization membership/project boundaries,
CardVisibility/CardWorkState, SDK dependency/registry services and ordered app
consent/resource/execution/receipt migrations. App registration endpoints,
policy execution and second-app integration are not enabled by this schema.
Phoenix workers, Kafka ownership, fixed PR683 and old union remain preserved.

Validation: the combined 133-test regression passed in 31.89 seconds before
the final board binding-state constraint. The final migration delta, including
that constraint and independent negative fixtures, passed its PostgreSQL
integration test in 1.28 seconds. Ruff and diff checks pass.
