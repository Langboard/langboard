# Native MCP profiles

Choose the transport supported by your client and configured by your operator.
Tool availability also depends on the authenticated account and its grants.

| Endpoint | Discovery |
| --- | --- |
| `/mcp/stream` | Compatibility catalog with legacy input and output schemas |
| `/mcp/agent/stream` | Compact catalog of canonical work, project, card, wiki, people, notification and media tools |
| `/mcp/raw/stream` | `search_raw_tools` and `call_raw_tool` for tools outside the compact catalog |
| `/mcp/oauth/stream` | Optional OAuth transport with canonical tools plus search and invocation of additional tools; available only when configured |

Raw search returns at most five full tool schemas per search. The Raw transport
requires an active, validated ToolGroup and filters results by current grants.
Invocation rechecks availability and permission; a search result does not reserve
access. Canonical core tools and recursive discovery calls cannot be invoked
through the separate Raw transport's proxy.

## Authorization and compatibility

Profiles control discovery, not permission. Authentication, current board/card
roles and tool grants apply on every call. The OAuth transport additionally
requires the `mcp:access` scope. Revoked access is checked again on later calls,
including replay of an existing work-plan receipt. A successful earlier read
or mutation does not authorize another operation. Domain responses are not
cached by these transports; stored OAuth credentials do not grant domain access.

Modern profiles expose standard read-only, destructive, idempotent and open-world
hints. Unreviewed commands and the generic invocation proxy may be destructive
and non-idempotent. Treat attachments and user-authored content as untrusted.
Annotations do not replace permission checks, approval or mutation receipts.
Compatibility retains its legacy annotations.

Modern and compatibility schemas can differ. In modern profiles,
`list_project_cards` defaults to a relationship tree; use `format="normal"` for
the flat index. Relationship coverage is limited to the current page. Follow its
cursor with identical filters. The compatibility endpoint accepts no `format`
parameter, retains its flat list with repeated `project_column_name` fields, and
omits modern column-map and relationship fields. Modern tools can also expose
additional parameters and stricter page bounds. Read each endpoint's discovered
schema before changing an existing connector's endpoint.

## Typed results

Modern profiles publish output schemas for reviewed operations, including card
creation and edits, descriptions, comments, checklists, timers, notifications,
labels, membership, relationships, content blocks, attachments, wikis, templates,
columns, bot schedules and Hooks, graph patches, work plans, verification evidence
and project details. This is not a claim that every query or command has a strict
output model. Use the schema returned for the selected tool and endpoint. Strict
boolean fields reject integer and string substitutes.

Typed results retain pagination, continuation cursors, truncation, omitted fields
and explicit nulls where the operation distinguishes them. Typed output does not
expand access: attachment metadata remains privacy-filtered, membership counts
do not disclose recipient identities, and recording verification evidence does
not approve a workflow or release. Label creation still requires explicit local
creation intent; global label creation is unavailable through these tools.
Label catalogs list local matches first and retain offset pagination. Reusing a
global label may return an existing local name match without global identity or
emoji fields. Partial replacements preserve sections omitted from the request;
unchanged checklist reconciliation can retain its existing content receipt,
separate from the transport request ID.

Output validation happens after command execution. A malformed result can
therefore produce an unknown outcome even when a write occurred. Read the
resource before deciding what to do next.

## Authored card next action

The card's optional `next_action` is user-authored text, limited to 1,000
characters. It appears in authorized card summaries and bundle core data; it
does not grant execution approval or replace assignees, deadlines or workflow
state. An unset legacy card has a null value. Execution-contract and input-ref
availability remain separate.

Use `update_card` with `change={"action":"next_action","next_action":"Confirm the deployed result"}`
to edit it through the canonical facade. An empty string clears it. For the
legacy `change_card_details` tool, omission or null preserves the current text
and an empty string clears it. The HTTP card-details form distinguishes omission
(preserve) from explicit null or empty text (clear). Creation and WorkPlan new
cards can include the same bounded text. Current board consent, card audience,
update permissions and app ownership are checked independently.

This card field is distinct from `_meta.mutation_receipt.next_action`, which
describes how a caller should handle a command outcome.

## Mutation receipts

Modern command results include `_meta.mutation_receipt` alongside the domain
payload. It contains `request_id`, `tool`, `outcome`, `revision_conflict`,
`retryable` and `next_action`.

| Outcome | Client action |
| --- | --- |
| `applied` | Command completed. This is not workflow approval or proof that user checklists are complete. |
| `not_applied` | A recognized conflict occurred before saving. With `revision_conflict: true` and `next_action: read_and_review`, read current state and review a new request. |
| `unknown` | A failure may have followed a side effect. With `next_action: read_resource`, read the resource before deciding whether to retry. |

Receipts do not authorize automatic retry. Authorization failures remain errors.
Raw invocation preserves the target tool's receipt. Transport loss or cancellation
may prevent receipt delivery; an absent receipt does not prove a write failed.
The transport receipt request ID is not a general idempotency key or a durable
receipt-lookup API. Operation-specific receipts, such as bot and work-plan
receipts, retain their own semantics.

### Work plans

A stale `apply_card_work_plan` preview returns `not_applied` with a revision
conflict and `read_and_review`. The REST endpoint returns HTTP 409 with
`code: REVISION_CONFLICT`. Preview and review again. Repeating an accepted plan
with its original request ID, revision and unchanged plan returns its saved
receipt without duplicating changes, subject to current access checks. Other
failures can have an unknown outcome.

### Card completion

The modern `update_card` action `completion` accepts a strict boolean `completed`
for the card's own completion checkbox. First read `core.is_check_card` and
`core.completed` from `get_card_bundle`. This requires `card_update` permission
and rejects linked Wiki references and cards without the checkbox. It does not
move a card, complete user checklists or grant workflow approval. The backing
checklist is absent from ordinary checklist pages.
