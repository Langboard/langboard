# Card MCP canonical tools and compatibility

The Card MCP surface uses `create_card(column_uid="leftmost")` for first-column creation, `list_project_cards` for bounded project card pages, and `get_card_bundle` for card details and optional sections.

The following older names have canonical replacements. Three compatibility
entries remain registered because existing clients still depend on their input
and output contracts; do not mistake the retirement target for current source
or deployed discovery.

| Older tool | Current registration | Canonical replacement |
| --- | --- | --- |
| `create_card_in_leftmost_column` | Compatibility entry | `create_card` with `column_uid="leftmost"` |
| `get_cards` | Retired | `list_project_cards` with cursor continuation |
| `get_card` | Compatibility entry | `get_card_bundle` |
| `get_card_checklists` | Retired | `get_card_bundle` with `include=["checklists"]` |
| `get_card_attachments` | Compatibility entry | `get_card_bundle` with `include=["attachments"]` |
| `get_card_bot_scopes` | Retired | `get_card_bundle` with `include=["automation"]` |

`BotMcp.get_card_bot_scopes` remains available to callers with project Update permission; the retired CardMcp read alias is gone. Direct MCP callers must migrate to the bounded tools before deploying this release. REST and UI routes keep their existing contracts.

`change_card_details` now accepts only title and deadline changes. For body edits, use `patch_card_description` or `replace_card_description` with the current description revision from `get_card_bundle`.

## Current inventory and shared mutation ownership

The 2026-10-17 source inventory has 55 registered `CardMcp` tools, including the
three compatibility entries above, and no `CardWorkspaceMcp` module. Removing
those entries requires the explicit client migration and compatibility decision
described in [the legacy contract gate](mcp-legacy-contract-gate.md); zero public
duplicate semantics is still an unfinished retirement requirement.

Checklist reconciliation now passes checklist/checkitem identities directly to
the existing native mutation services, which resolve current records and validate
ancestry. It no longer performs four extra ancestor/item reads per unchanged
projected item. Native mutation failure still aborts before committing the final
receipt. Projection ownership, item identity checkpoints and stale-item deletion
remain unchanged; this is not a new mutation layer or transaction protocol.

For the same feature scope, `CardMcp.py` + native adapter + ports decreased from
1,807 + 660 + 123 = 2,590 to 1,807 + 637 + 123 = 2,567 physical source lines.
That 23-line reduction is not comparable to earlier inventories with fewer
features. The regression first failed on redundant reads, then passed for a
25-item unchanged projection, an accepted change and a rejected native mutation.
The combined adapter/projection suite passed 47 cases. Source checks do not prove
deployed client discovery, query latency, or REST/UI/MCP acceptance.
