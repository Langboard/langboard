# MCP input contract transition

The following five tools have updated input schemas:

- `change_card_details` and `create_card` add
  optional `next_action`.
- `search_project_cards` adds optional filters and pagination and accepts a null
  project UID while retaining the required project and query arguments.
- `update_bot_hook` and `upsert_bot_hook` use the shared `DomainEventType` schema.
  All 40 prior event wire values remain accepted; three new values are present.
  The retired bot-specific type and compatibility aliases are not restored.

New optional arguments and event values may be rejected by the old server after
rollback. Clients must rediscover the server's tool schema and handle rejection
after a rollback.

## Retired card compatibility tools

The migrated gateway uses `get_card_bundle` for card and attachment metadata,
`create_card` with `column_uid="leftmost"` for the first active column, and
`create_project` with `template_name` for template-backed boards. The old
`get_card`, `get_card_attachments`, `create_card_in_leftmost_column`, and
`provision_project` tools are removed from native registration and raw discovery.
Clients must rediscover and use the canonical contracts; no compatibility fallback
restores these retired commands. Read actual image bytes through `read_card_images`.
