# Native MCP agent guidance

Langboard supplies client-independent guidance through MCP server instructions
on its compatibility, Agent/Core, Raw and configured OAuth transports. Clients
receive instructions through initialization or metadata discovery. Clients that
skip metadata discovery must request server metadata to obtain this guidance.

The guidance covers respecting workflow gates, using native checklists, changing
timers only when requested, preserving assignees, choosing local labels first,
creating local labels only with explicit intent, revision conflicts and reading
current state after ambiguous mutation failures. Global-label creation is not
available through MCP. Directory identity and notification-read actions also
require attention to the user's intent.

Server instructions guide client behavior. Current identity, domain permissions,
ToolGroup grants and command validation enforce access; instructions themselves
grant no permissions. OAuth availability depends on operator configuration.

Use the selected endpoint's discovered schemas and
[native MCP profile guide](native-mcp-profiles.md) for tool availability,
mutation receipts and approval boundaries.
