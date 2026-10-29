# MCP input contract transition

The immutable `legacy-native-v1.json` snapshot remains the baseline for the
legacy catalog and tool-group grant checks. The integration candidate introduces
six intentional input-schema changes:

- `change_card_details`, `create_card`, and `create_card_in_leftmost_column` add
  optional `next_action`.
- `search_project_cards` adds optional filters and pagination and accepts a null
  project UID while retaining the required project and query arguments.
- `update_bot_hook` and `upsert_bot_hook` use the shared `DomainEventType` schema.
  All 40 prior event wire values remain accepted; three new values are present.
  The retired bot-specific type and compatibility aliases are not restored.

The CI transition file records a SHA-256 pair for each exact baseline and
candidate input schema. A missing tool, different old or new hash, other input
change, changed output schema or annotation, registry mismatch, or changed
checked tool-group grant still fails. Updating the transition file requires
review of the actual schema change; its hashes alone do not prove semantic
compatibility or authorize a release. The checker without the transition file
continues to demand exact equality.

The catalog CI check validates sample legacy requests, including every prior
event value in both hook tools, against both schemas. It also confirms that two
new optional arguments are rejected by the old schema. This is not authenticated
client or handler verification. New optional arguments and event values may be
rejected by the old server after rollback. Clients must rediscover the server's
tool schema and handle rejection after a rollback; test this with real clients
before accepting the canary. The separate deployed group-grant check and the
original fixture provenance manifest remain required.
