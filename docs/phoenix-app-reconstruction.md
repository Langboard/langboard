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

The current PR 684 is a focused bridge, not the full reconstruction: Graph
service authentication and signed chat-session history isolation. The combined
Python ASGI plus SQLite/PostgreSQL suite passes 83 tests in 16.26 seconds on the
main lock environment. It does not prove app/HITL acceptance, Phoenix compilation
or operational deployment. All existing review PRs and the local canary union
remain preserved; no merge or protected deployment-reference update is made.
