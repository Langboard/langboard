# Native card reminder tools

Reminder scheduling, persistence, visibility and revision checks belong to the existing card reminder application. MCP uses that owner with the MCP collaboration channel.

- `list_card_reminders(project_uid, card_uid, cursor?, limit=20)` returns at most 50 rules visible to the caller and a continuation cursor. Other users' personal rules are excluded.
- `change_card_reminder(project_uid, card_uid, change)` accepts an action-specific object:
  - `create`: `schedule`, original UUID `request_uid`, optional `shared`, `external_delivery`, and `recipient_overrides`.
  - `update`: `rule_uid`, `schedule`, current `expected_revision`, explicit `enabled`, optional external delivery and recipient overrides. Set enabled to false to pause; it does not erase history.
  - `change_occurrence`: `rule_uid`, current `expected_revision`, observed timezone-aware `expected_due_at`, and optional timezone-aware `moved_to`. Omit moved_to to skip that observed occurrence.

Use discovered schemas for schedule fields and the selected action. Booleans and revisions reject string/integer substitutes. Natural-language parsing is not provided. The tools remain outside the compact Agent catalog; native default/Raw discovery can find them, subject to current explicit grants. Existing grants are not migrated.

External native clients require current board read consent for listing and current write consent and role for changes, including personal changes. This preserves the existing MCP boundary; it does not expand the REST permission model where read-only board members can create their own personal rules. Shared changes independently require the current card update role. Personal ownership, private-card visibility and current native connection consent remain native checks.

Creation replay uses the supplied original request UID and unchanged content and returns the stored item with replayed=true. Changed content with that UID is rejected. Updates and occurrence changes retain the existing revision and observed-time fences. Generic MCP mutation receipts remain conservative: rejected changes may be reported as an unknown mutation outcome. Read current state before reviewing a retry; these adapters do not add a durable receipt lookup or automatically retry. Listing does not create occurrences, invoke providers or mark notifications read.

These tools do not implement recipient read/complete/snooze adapters, Web Push subscription management, or SDK rule methods. A rule command response is not delivery or workflow-completion evidence.
