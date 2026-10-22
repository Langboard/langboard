# Default native MCP discovery

Native OAuth now exposes Agent/Core tools plus `search_raw_tools` and
`call_raw_tool` on its default connection. No per-user tool-group assembly is
needed. Other native commands remain available through bounded search or direct
calls; discovery reduction does not grant or remove domain permissions.

The provider uses FastMCP's native RegexSearchTransform with core tools pinned.
Search returns at most five command definitions and excludes pinned duplicates.
The proxy uses the normal command pipeline, preserving input validation, native
identity, ownership and project/card ACL checks. Tool annotations describe search
as a query and the generic proxy conservatively as a mutation.

Legacy transport and the existing explicit agent/raw transports are unchanged.
Resources and workflow prompts remain available. OAuth login, consent and
persistent storage require operator configuration; selecting a smaller discovery
catalog does not configure authentication or grant access.

Official implementation reference:
https://gofastmcp.com/servers/transforms/tool-search
