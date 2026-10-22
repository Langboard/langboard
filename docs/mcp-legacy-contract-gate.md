# MCP client compatibility

The compatibility endpoint preserves older tool input and output contracts.
Modern endpoints offer canonical tools and can use different schemas, defaults
and bounds. Read the selected endpoint's discovered schema before moving a
connector to another endpoint. See [native MCP profiles](native-mcp-profiles.md)
and the [Card tool migration table](card-mcp-canonical-tools.md#older-tool-names).

An older alias may retain a different response shape from its preferred
replacement. A matching name or successful discovery does not establish output
parity. Tool discovery is also subject to the current account, active ToolGroup
and grants. A catalog from another account or deployment does not establish your
access.

OAuth login identity, organization membership and application authorization are
separate. SCIM provisioning alone does not imply employee classification; that
requires explicit operator policy. Existing local users and external
collaborators do not require SCIM solely to remain supported.

For OAuth deployment requirements and restart/refresh limitations, see
[OAuth storage and operation](native-oauth-durable-storage.md). For a current
permission snapshot, use [capability diagnostics](native-capability-diagnostics.md).
A successful snapshot does not reserve authorization for a later command.

## Card-list page bounds

Both compatibility and modern profiles advertise integer `limit` values from
1 through 25, with a default of 20. Invalid values are rejected before domain
dispatch. The portable contract fixture records only this intentional input
constraint; all other frozen schemas and annotations remain unchanged.

Compatibility retains the flat card index. Modern profiles retain their tree
default and accept `format="normal"` for a flat index. The transport tests verify
both profiles; source validation does not establish deployed discovery or client
migration.
