# Native card links

Modern Agent/Core, Raw and OAuth providers return `card_url` and
`card_link_markdown` from `get_card_bundle`, including continuation reads.
Independent MCP clients can display the same browser shortcut without a plugin
or a user-created ToolGroup on the native OAuth transport.

Links use the deployment's public UI URL and encoded project/card path segments.
They are generated only after the existing authorized read succeeds. A URL does
not grant access. Compatibility providers retain their previous output schema.

For transport configuration and discovered result schemas, see
[native MCP profiles](native-mcp-profiles.md) and
[native MCP OAuth](native-mcp-oauth.md).
