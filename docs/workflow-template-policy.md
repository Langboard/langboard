# Workflow references in board templates

Board templates store column names, descriptions, translations, and the immutable
`workflow_stage` key. They do not snapshot completion, active queue, overdue, or
entry-effect policies. `ProjectTemplateService.save_columns` persists this
allowlist; copying a board uses the same structural fields.

New boards bind each column to the current registry entry. Changing a registry
policy therefore changes the interpretation of existing bindings and later
template-created boards. Column guidance remains separate and is combined with
the current stage description for API/MCP readers. Renaming a column does not
change the key. Legacy string columns retain a null stage without name inference.

## Board-specific text overrides

Changing a template-created column's canonical name or description makes that
field board-owned. The native column service clears only that field in the
inherited translation map, so a previous template translation cannot hide the
user's edit. The other field's translations and the workflow key remain intact.
Clearing a description is an explicit override too. Re-submitting unchanged text
does not invalidate translations. The source template is never rewritten.

Existing name/description events carry the updated map; the board consumes it
through the existing model metadata update path. Persistence/event regressions
cover rename, description replacement and description clearing. They do not
replace authenticated deployment, reconnect or multi-client acceptance.

The structural integration regression exercises database save/readback, a
registry edit, native column creation, guidance resolution, board-to-template
copy, and another registry edit. It also supplies policy fields at the service
boundary and checks they are not persisted. Project creation, notification
effects, and unrelated automation providers are replaced in this test; it is
not evidence of a deployed browser or live PostgreSQL roundtrip.

## Compact MCP workflow context

Modern card lists and search results reference `project_column_uid` and stage keys.
Column guidance and registry policies are supplied once in the response's
`columns` and `workflow_stages` dictionaries. Card state retains completion,
verification, execution, and inconsistency codes independently. Null completion
is preserved for unmapped columns. Legacy list transport restores column names
and emits a JSON object conforming to `ProjectCardListResponse`.

The regression gate covers native adapter/query projection, list transport,
search, query-stage filtering before limits, and the native 1,000-item transition
test. That transition requires one summary event and at most three timer reads;
the fixture substitutes publishers and exercises SQLite by default. Authenticated
live list/search readback confirms the compact contract for returned cards; it
does not establish all board ACL combinations or live mass-event delivery.

## Recover template choice inventories without discarding drafts

Bot and global-label choice failures are independent of template-list or save
failures. Each choice inventory now offers an explicit, guarded retry inside an
open editor. The retry control stays outside the disabled fieldset so it remains
usable when choices failed. It only reloads that inventory, without resetting
columns, translations, selected bot/label IDs, description or default template.
Existing bot snapshots remain preserved during unrelated saves even if bot
choices are still unavailable. No automatic retry loop is added.

The original desktop/mobile browser regression failed because the retry controls
were absent. The complete editor suite now passes16cases, including recovery at
1280/390px, preserved selections/unsaved description, zero writes during retries,
legacy bot fallback, translation order and duplicate save/default guards. These
browser fixtures use intercepted API responses; they do not establish live API
save or deployed canary acceptance. Application TypeScript, targeted ESLint,
Prettier and the existing UI-string baseline pass. Existing translated labels and
base UI widgets are reused with no additional resource or dependency.
