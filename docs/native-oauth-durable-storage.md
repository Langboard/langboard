# Native OAuth storage and operation

Native OAuth is optional and requires operator configuration. The public and OIDC
discovery URLs must use HTTPS. Configure the provider client, a stable signing key
of at least 32 characters, and scopes including `mcp:access`. Authorization consent
is required. Provider-specific authorization parameters and identity policy belong
in configuration.

The default provider uses FastMCP's encrypted persistent file storage. Preserve
the signing key and persistent FastMCP home across restarts. The signing material
also determines storage encryption and isolation; changing the key can make
existing registrations and token mappings inaccessible. Treat loss of either the
key or stored mappings as a credential-recovery event. Local persistence alone
does not establish live provider login, consent or refresh availability.

## Multiple instances and refresh

A multi-server deployment needs an operator-selected shared encrypted storage
backend and concurrency validation. Refresh exchange is serialized within one
provider process; this does not provide a shared atomic refresh claim between
replicas. Do not assume that sharing disk alone makes concurrent refresh safe.
A refresh request cannot expand the approved scope grant. An expired or consumed
refresh token is rejected.

## Current access and readiness

OAuth requires a verified issuer/subject linked to a current active, non-deleted
Langboard user. Identity is not inferred from a name or email. User status is
checked again for authenticated MCP requests, and domain permissions still apply
at invocation. Persisted OAuth credentials do not preserve revoked board access.

The OAuth storage readiness check exercises a temporary write/read/delete round
trip. A ready result does not establish identity-provider availability, user
consent, successful refresh or domain authorization. Storage faults and timeouts
are reported as not ready.

See [native MCP profiles](native-mcp-profiles.md) for endpoint and scope behavior.
