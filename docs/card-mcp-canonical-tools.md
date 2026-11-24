# Card MCP tools and compatibility

Use `create_card(column_uid="leftmost")` for first-active-column creation,
`list_project_cards` for bounded project card pages, and `get_card_bundle` for
card details and optional sections. Follow continuation cursors rather than
assuming that one response contains all cards or section items.

Select the appropriate [native MCP profile](native-mcp-profiles.md) and use the
schema discovered on that endpoint. Modern and compatibility schemas differ.
Use [capability diagnostics](native-capability-diagnostics.md) with project and
card identifiers to inspect the current account's scoped permissions.

## Older tool names

| Older tool | Compatibility status | Preferred replacement |
| --- | --- | --- |
| `create_card_in_leftmost_column` | Available | `create_card` with `column_uid="leftmost"` |
| `get_cards` | Retired | `list_project_cards` with cursor continuation |
| `get_card` | Available | `get_card_bundle` |
| `get_card_checklists` | Retired | `get_card_bundle` with `include=["checklists"]` |
| `get_card_attachments` | Available | `get_card_bundle` with `include=["attachments"]` |
| `get_card_bot_scopes` | Retired as a Card read alias | `get_card_bundle` with `include=["automation"]` |

The separate bot-management `get_card_bot_scopes` operation requires project
`update` permission. Compatibility availability does not imply inclusion in every
modern profile. See [client compatibility](mcp-legacy-contract-gate.md).

## Creation, editing and visibility

Card creation requires project `card_write` permission in REST and MCP, including
the older leftmost-column entry. Editing an existing card requires `card_update`.
Read/update access alone does not authorize creation. These permissions do not
expand membership or override card visibility.

On a collaborative board, a current external member classified by the configured
SCIM or OIDC policy receives a `SHARED` creation default. Internal users and bots
receive `INTERNAL`; an active personal-board owner receives `PRIVATE`. Unknown
policies and nonmembers do not receive a shared default. Creation defaults do not
reclassify existing cards or grant access to an existing inaccessible card.

`change_card_details` accepts title and deadline changes. For body edits, read the
current description revision from `get_card_bundle`, then use
`patch_card_description` or `replace_card_description`. Review current content
when a revision conflict occurs. Linked Wiki cards are read-only references;
edit their source Wiki instead.

## Checklist changes and failure handling

`update_card_checklist` and `update_card_checkitem` validate the requested fields
and their ownership within the specified card. A field-change or response
projection failure rolls back the requested field updates. Successful responses
reflect the update without waiting for replica catch-up and retain bounded
section results.

A transport or output-validation failure can still leave a caller with an unknown
outcome. Read current state before deciding whether to retry. The
[native profile guide](native-mcp-profiles.md#mutation-receipts) explains mutation
receipts, revision conflicts and their limits. Checklist completion does not
supply workflow approval.
