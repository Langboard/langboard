# LangboardQL read boundaries

Status: implementation preparation, not an available GraphQL API. This document
maps existing read owners before schema/resolver work. Existing REST, MCP and SDK
contracts remain in place.

## Existing owners

Paths below are relative to the repository root. The query layer must use these
owners rather than reimplement card visibility, workflow state or search policy.

| Read | Current owner | Reuse constraint |
| --- | --- | --- |
| Bounded card list | `src/api/langboard/card_workspace/application/queries/__init__.py:list_project_cards` | Use the existing query port, normalized filters and actor-bound cursor. |
| Card context | Same module, `get_card_bundle` | Select only required sections and follow their continuations; do not load all bodies to discard them afterward. |
| Native query authority | `src/api/langboard/card_workspace/infrastructure/native.py:NativeCardWorkspaceAdapter` | Preserve the real actor, collaboration channel and current board/object permissions. |
| Card-context search | `src/api/langboard/card_workspace/application/queries/search.py:search_card_context` | Owns the current MCP search projection, date validation, filter binding, pagination and document excerpt budget. The MCP tool delegates once. |
| Work state | `src/shared/py/langboard_shared/domain/services/CardWorkState.py:project_work_state` | Project native state and reason codes. Preserve unknown, stale and archived independently; do not infer from column labels. |
| Visibility | `src/shared/py/langboard_shared/domain/services/CardVisibilityPolicy.py:CardVisibilityContext` and `factory/CardService.py` | Resolve current authority for roots, nested nodes and cross-board targets. Read access is not execution consent. |
| Relationship pages | `src/shared/py/langboard_shared/domain/services/factory/CardRelationshipService.py:get_api_relation_summaries` | Bounded immediate edges use current endpoint visibility; machine semantics and direction remain native. |
| Document sources | `src/shared/py/langboard_shared/domain/services/ProjectDocumentQuery.py` | Select read/search operations only. Processing requests are mutations and cannot be reached from a Query resolver. |

The extracted search owner intentionally retains `CollaborationChannel.Mcp`.
Calling it from a different transport does not establish that transport's app
consent or visibility contract. A future resolver must establish its actual
channel and bind any new cursor to all authorization-relevant context. Changing
that contract requires separate compatibility and permission tests; this
extraction neither broadens grants nor creates a GraphQL entry point.

## Resolver work still required

Use a maintained GraphQL implementation and standard syntax. No GraphQL runtime
has been selected or added by this change. Introduce a Query-only schema with
explicit board scope and bounded selections; do not add Mutation, Subscription,
arbitrary SQL, a second repository, or another work-state calculator.

Before exposing a root, enforce limits for depth, field complexity, fanout, total
nodes, output bytes and execution time. The current per-owner page/excerpt bounds
are not an aggregate GraphQL cost limit. Aliases, fragments, repeated roots and
nested paths must all count toward the same request budget. Stop during execution
when a hard budget is exhausted. Select measured defaults and record their basis.

A relationship page is one hop. Depth-two expansion still needs a bounded,
request-scoped traversal with cycle/revisit handling, batching and fresh target
ACL checks. Do not disclose hidden endpoints through counts, continuation,
errors or cached metadata. Treat references as references; use native readiness
for blocking policy. Existing cursors have distinct scopes and must not be
interchanged between list, search and relationship fields.

## Acceptance before release

Compare the same authorized scenarios against native reads, including custom
workflow columns, closed-but-unverified cards, stale verification, archive,
PRIVATE/WHISPER/SHARED, external members, revoked access, cross-board targets and
deleted/replaced sources. Exercise relationship types, direction, cycles and two
hops. Verify rejected budgets and prove no indexing, model execution or writes
occur during reads. A read-only resolver name alone is not proof of no side effect.

Measure logical requests, database calls, output bytes/tokens and p50/p95 latency
for equivalent REST/MCP and GraphQL selections. Inspect database query counts to
reject N+1 regressions rather than assuming one HTTP request means one DB query.
Cover SQLite/PostgreSQL and authenticated deployed UI/MCP/SDK consumers. Source
checks do not establish deployed acceptance. Until those gates pass, this is
preparation for LangboardQL, not a delivered query product.
