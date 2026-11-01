# MCP input contract transition

The following six tools have updated input schemas:

- `change_card_details`, `create_card`, and `create_card_in_leftmost_column` add
  optional `next_action`.
- `search_project_cards` adds optional filters and pagination and accepts a null
  project UID while retaining the required project and query arguments.
- `update_bot_hook` and `upsert_bot_hook` use the shared `DomainEventType` schema.
  All 40 prior event wire values remain accepted; three new values are present.
  The retired bot-specific type and compatibility aliases are not restored.

New optional arguments and event values may be rejected by the old server after
rollback. Clients must rediscover the server's tool schema and handle rejection
after a rollback.
