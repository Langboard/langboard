# Native extension composition

Extensions select existing native command names explicitly. They do not register
replacement business handlers, a principal, role policy or repository. An empty,
duplicate or unknown selection is rejected before a provider is populated.

Native command composition is configured by the host. Selected tools retain the
same authentication, current role checks, schema and response contract as the
core catalog. A separate catalog must preserve the transport's authentication
and, where applicable, legacy tool-group restrictions. Selecting a command does
not grant permission to execute it.

Extensions do not receive caller credentials or ownership of the authenticated
client session. Do not expose duplicate command names on the same server.

This facility composes trusted native commands only. It does not install or
execute arbitrary extension packages or provide an in-process sandbox. External
business integrations should use the standalone transport SDK and run in their
own service boundary.

## Board Apps declarations

Board Settings Apps discovery combines enabled instance-approved `AppDefinition`
records with the built-in provider catalog. GitHub, GlitchTip
and Dokploy declare resource types, supported capability names, native read and
configuration permission actions, workflow requirements and the v1 signal
provenance fields. The compact signal declaration describes required envelope
fields; it is not a payload validator or an installed event consumer.

Declarations are catalog metadata. They do not register Python handlers, grant
capabilities, enable transitions, create connections or resolve credentials.
The catalog combines these declarations with current board-owned bindings after
its existing primary database authority check. Declared capabilities and saved
`granted_capabilities` remain separate. Unapproved App keys cannot mutate bindings. External definitions are approved and
versioned through the native admin registry.
Returned lists are copies; clients cannot edit the registry through responses.

GitHub supports native manifest registration, authenticated installation
authorization and repository selection. GlitchTip and Dokploy support native
connection registration and resource selection with provider access checks.
Use Board Settings Apps to configure the connection and grant only the required
capabilities. A catalog declaration alone does not establish a connection or
verify credentials. Dokploy capabilities are read-only; this interface does not
authorize deployment or redeployment writes.

The [external service app contract](external-app-sdk.md) documents registry, panel
consent and current support boundaries. For client transports, use
[native MCP profiles](native-mcp-profiles.md) and [native MCP OAuth](native-mcp-oauth.md).
Registration alone does not install a signal adapter or automation principal.
