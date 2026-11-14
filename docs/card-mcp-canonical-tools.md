# Card MCP tools and compatibility

Use `create_card(column_uid="leftmost")` for first-active-column creation,
`list_project_cards` for bounded project card pages, and `get_card_bundle` for
card details and optional sections. Follow continuation cursors rather than
assuming that one response contains all cards or section items.

Select the appropriate [native MCP profile](native-mcp-profiles.md) and use the
schema discovered on that endpoint. Modern and compatibility schemas differ.
Use [capability diagnostics](native-capability-diagnostics.md) with project and
card identifiers to inspect the current account's scoped permissions.

## Bounded comment content

Comment content carries `content_format`, `content_total_chars` and
`content_truncated`. Check the truncation flag before decoding serialized editor
JSON: a bounded prefix can end inside a JSON string. The 8,000-character inline
limit applies to that serialized value, including escapes and editor structure,
so a shorter visible comment can still be truncated. A section cursor pages
entries; it does not restore the missing tail of one comment.

For evidence cited by a compaction block, use its `compaction_uid` and the
comment's `evidence_ref` with the authorized `get_card_timeline` operation. Follow
`source.next_cursor` as `content_cursor` for that same scope. This path requires
an existing compaction reference; it is not a general full-comment read for
uncovered comments. Use the authorized original comment view when that reference
is unavailable. Tool discovery alone does not establish that the active tool
group permits a read. If denied, preserve the source and report the failed
operation; do not overwrite a comment using its incomplete prefix.

## Older tool names

| Older tool | Compatibility status | Preferred replacement |
| --- | --- | --- |
| `create_card_in_leftmost_column` | Retired | `create_card` with `column_uid="leftmost"` |
| `get_cards` | Retired | `list_project_cards` with cursor continuation |
| `get_card` | Retired | `get_card_bundle` |
| `get_card_checklists` | Retired | `get_card_bundle` with `include=["checklists"]` |
| `get_card_attachments` | Retired | `get_card_bundle` with `include=["attachments"]` |
| `get_card_bot_scopes` | Retired as a Card read alias | `get_card_bundle` with `include=["automation"]` |
| `create_card_checklist` | Explicit Raw/compatibility primitive | `change_card_checklist` with `action="create_list"` |
| `create_card_checkitem` | Explicit Raw/compatibility primitive | `change_card_checklist` with `action="add_item"` |
| `update_card_checkitem` | Explicit Raw/compatibility primitive | `change_card_checklist` with `action="update_item"` or `"set_completed"` |
| `update_card_comment` | Retired | `change_card_comment` with `action="edit"` |

The default native catalog advertises one action command for comments and one for
checklists. Checklist primitives remain discoverable through `search_raw_tools` and callable
through `call_raw_tool` with the same native authorization. The four comment
primitives are retired; use `change_card_comment` on every supported transport.
Default discovery does not grant permission. See the exact
[retirement declarations](mcp-input-contract-transition.md#retired-card-compatibility-tools).

The separate bot-management `get_card_bot_scopes` operation requires project
`update` permission. Compatibility availability does not imply inclusion in every
modern profile. See [client compatibility](mcp-legacy-contract-gate.md).

## Retiring older names

Available compatibility entries are still supported. Their absence from the compact
core catalog, or a compatibility description, does not mean that they have been
removed. Granted Raw access can still expose older entries. The migration table
identifies replacements; it does not prove that consumers have migrated.

Remove an older entry only in a reviewed release after its consumers have migrated,
the announced compatibility period has ended, and legacy usage has ceased. Record
the affected transports and schemas, then compare authenticated catalogs and
create/read/update, unchanged and denied outcomes for supported clients against
the same deployed candidate. Announce the removal and preserve the normal previous
release recovery path. Hiding a name from default discovery alone is not removal.
Keep behavior in the existing domain service throughout the migration; an alias
must not grow a second implementation.

## Creation, editing and visibility

Card creation requires project `card_write` permission in REST and MCP, including
`create_card(column_uid="leftmost")`. Editing an existing card requires `card_update`.
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
