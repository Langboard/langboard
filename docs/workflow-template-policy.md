# Workflow references in board templates

Board templates store column names, descriptions, translations and the
`workflow_stage` key. They do not snapshot completion, active-queue, overdue or
entry-effect policies. Copying a board preserves the same structural fields.

New boards bind columns to the current stage registry entry. Changing a registry
policy therefore changes the interpretation of existing bindings and boards
created later from templates. Column guidance is combined with the current stage
description for API/MCP readers. Renaming a column does not change its stage key.
Legacy string columns keep a null stage without inferring one from their name.

## Board-specific text overrides

Editing a template-created column's canonical name or description makes that
field board-owned. Only that field's inherited translations are cleared, so an
older template translation cannot hide the edit. The other field's translations
and workflow key remain intact. Clearing a description is an explicit override.
Submitting unchanged text preserves translations. These edits do not rewrite the
source template.

## MCP workflow context

Modern card lists and search results reference `project_column_uid` and stage
keys. Column guidance and registry policies appear once in the response's
`columns` and `workflow_stages` dictionaries. Card state keeps completion,
verification, execution and inconsistency codes separately. Unmapped columns
preserve null completion. Compatibility list responses retain column names;
consult the selected endpoint's [profile](native-mcp-profiles.md) before changing
clients.

## Recovering choice lists

In an open template editor, bot and global-label choice lists each offer an
explicit Retry when loading fails. Retrying reloads only that list and does not
save or reset columns, translations, selected bot/label IDs, description or the
default-template choice. Existing bot selections remain preserved during an
unrelated save even while bot choices are unavailable. Retries are manual.
