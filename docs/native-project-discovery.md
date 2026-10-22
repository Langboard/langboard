# Native project discovery

Modern `get_projects` supports an optional title `query` (trimmed, 1–100
characters, Unicode case-insensitive substring match). Search operates only on
the existing authenticated user's authorized project list.

Agent/Core, Raw and native OAuth results use a compact typed summary: UID,
title, project type, star state, current role actions, `project_url` and
`project_link_markdown`. Board bodies and unrelated internal fields are omitted.
Native OAuth clients do not need a custom ToolGroup. Compatibility retains
its input and output contract; query is a modern-only parameter.

See [native MCP profiles](native-mcp-profiles.md) and
[native MCP OAuth](native-mcp-oauth.md) for transport and authorization requirements.
