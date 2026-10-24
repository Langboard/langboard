# Client IP trust boundary

API key and IP-restricted bot authentication use one resolved client IP. They do
not accept a match from arbitrary X-Real-IP or X-Forwarded-For entries.

## Configuration

`TRUSTED_PROXY_IPS` is a comma-separated list of exact proxy IPs or CIDRs.
The default is empty. Hostnames and `*` are rejected. Use the smallest actual
proxy network; do not trust client networks. The native server passes this same
list to Uvicorn, overriding `FORWARDED_ALLOW_IPS` so a separate wildcard cannot
rewrite the peer before authentication. Trusted HTTPS forwarding remains enabled.

A direct untrusted peer ignores forwarded headers. A trusted peer requires a
valid X-Forwarded-For chain; the first untrusted hop from the right is the client.
Missing peers, malformed forwarded IPs, and duplicate X-Forwarded-For field lines
cannot authorize an IP-restricted key. Duplicate fields are rejected because
Uvicorn and Starlette select different entries when duplicates are present.
Existing development and explicitly exempt bot-platform behavior is unchanged.

## Deployment acceptance

Before enabling proxy trust, verify the real public edge and every internal hop.
The public edge must sanitize incoming forwarding headers. Configure exact trusted
hops in the deployment environment and preserve the existing data and services.

Test valid and invalid credentials from allowed and denied clients, through direct,
single-proxy and multi-proxy routes, including forged X-Real-IP and X-Forwarded-For.
Confirm the route's native rejection response and the logged resolved client IP.
Source tests do not prove the deployed proxy topology or runtime authorization.

## Regression checks

`src/api/tests/mcp_integration/test_client_ip_boundary.py` exercises native credential
validation with database/key-vault doubles, IP resolution, configuration validation,
and the real Uvicorn app wrapper. The existing MCP legacy CI job includes this file.
