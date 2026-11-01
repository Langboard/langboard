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

Cooperating API workers must use the same **primary SQLAlchemy database**, native
provider configuration, encrypted token backend, and stable signing material.
PostgreSQL refresh exchange now holds a transaction advisory lock from the
current grant reread through FastMCP's completed token rotation. The namespace is
a stable SHA-256-derived signed 64-bit key over the native base URL, upstream
client ID and token endpoint, within that database. Different instances have
different namespaces; hash collision can only cause extra serialization.
Explicit refresh and FastMCP 4.0.10's transparent access-token refresh use this same
fence. Transparent refresh acquires it before FastMCP's authoritative upstream
reread. An explicit loser rereads the consumed grant and rejects it without a
second upstream exchange. This uses FastMCP's existing token protocol and stores;
it does not add schema, dependencies or a KV compare-and-swap claim.

SQLite remains supported using an owned connection with `BEGIN IMMEDIATE`.
For SQLite `StaticPool` (including the supported in-memory configuration), a
per-engine in-process guard also prevents cooperating OAuth checkouts from
sharing and rolling back the same DBAPI connection. This guard never replaces
the writer fence and is not a database-error fallback. A contender cannot close
or roll back the owner's transaction. In-memory support remains single-process;
each independent process has its own database.
Its writer fence serializes the entire database, including unrelated writes,
while the upstream exchange is pending. It does not rely on a process-local lock.
The connection's busy timeout is temporarily zero for each nonblocking attempt
and restored before returning it. Other SQL dialects fail closed. An in-memory
SQLite database cannot coordinate independent processes; multi-process SQLite
requires the same database file and normal SQLite-supported local filesystem
locking. Do not use independent in-memory databases or unsupported network-file
locking for shared workers.

Lock contention is retried asynchronously for at most five seconds. Database
connection checkout, pre-ping, connect and driver I/O retain the existing engine's
pool and driver timeouts; the five-second contention deadline is **not** a total
wall-clock bound on those operations. Database work runs off the event loop.
Acquisition handoff and transaction close are shielded and awaited to completion,
including direct asyncio task cancellation, so an acquiring thread cannot be
abandoned with a live lock. Close rolls back the transaction, releasing the
advisory/writer fence rather than returning a session-level lock to a pool.
Database failure, contention timeout or cancellation makes no upstream call
while waiting. There is no local-lock fallback. Transparent refresh fails closed
when FastMCP catches a fence error; failure state stays within that access-token
validation. A definite upstream failure before stored-state mutation leaves the
original grant retryable. A refresh request cannot expand its approved scopes.

Sharing the fence alone is insufficient: independent or stale token stores still
break the contract. The default encrypted persistent FileTreeStore is tested
across distinct processes on a shared local directory. Deployments on different
hosts need an operator-selected shared encrypted backend with consistent reads
and writes. Storage readiness alone does not verify this requirement. Every
cooperating worker must run this version; older workers do not honor the fence.
The prebuilt Compose default of two API workers therefore requires a shared
primary database, persistent OAuth volume and unchanged signing configuration.
No deployment or mixed-version rollout was performed by these source tests.

This is cooperating-worker serialization, **not strict exactly-once refresh**.
An upstream response lost before persistence, process crash after upstream
consumption, or partial failure of FastMCP's multiple KV writes remains an
unresolved recovery boundary. The SQL transaction does not include the IdP or
KV writes. No eager credential deletion or bespoke recovery/claim protocol is
introduced; such failures may require client reauthorization.

Source acceptance uses deterministic overlap with two actual providers and with
two subprocesses sharing encrypted temporary FileTreeStore. An upstream barrier
allows both old process-local exchanges to enter (two calls); the database fence
allows one call and the explicit loser observes `invalid_grant`. Both SQLite and
an owned disposable PostgreSQL 17 cluster are covered. Separate provider tests
exercise explicit/transparent contention, scope denial, retry after definite
failure, timeout/database failure/cancellation cleanup and normal access after a
failed transparent attempt. These are fixture-based protocol/storage proofs,
not live IdP login, gateway, client or production-replica acceptance.

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
