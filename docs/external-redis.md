# External Redis compatibility

## Current product contract

Python cache consumers use `CACHE_URL`; Celery uses `BROKER_URL` (falling back
to `CACHE_URL` for legacy installations). Credentials belong in deployment
secrets, never in source. Redis client URL parsing also supports `rediss://`;
this change adds no certificate-validation bypass.

Set `CACHE_KEY_PREFIX` to a unique, server-owned instance namespace such as
`langboard:example:cache:`. The value must end in `:`, contain only ASCII
letters, digits, colon, underscore and hyphen, start with a letter or digit,
and contain at most 128 characters. Redis glob syntax is rejected.

The prefix applies to cache GET/SET/EXISTS/DEL, atomic SET NX claims, and
board-chat attachment lock keys. Callers keep supplying logical keys. Empty
prefix preserves legacy key names; it does **not** permit `clear()`.

`clear()` scans only the configured literal namespace and deletes matching
keys in batches of at most 100 using UNLINK. It never calls FLUSHDB, FLUSHALL
or KEYS. This is an explicit maintenance operation, not a background scan.
Concurrent writes can survive a clear; it is not a transactional reset.

### Server permissions

Use an instance-specific Redis ACL user with a key pattern matching the exact
namespace. Cache commands are GET, SET, EXISTS, DEL, SCAN and UNLINK. Attachment
locks additionally use the official redis-py Lua lock implementation (EVALSHA
and SCRIPT LOAD). Do not grant database-wide flush or administrative commands.

SCAN exposes key names even when key-access ACLs restrict commands. A prefix
and application filtering are not a hostile-tenant security boundary. Use a
separate server/security boundary when key names themselves must be hidden.

## Optional Compose deployment

The existing Compose selector enables `docker-compose.redis-external.yaml` when
either external URL is set. Supply all four values in `.env`:

```dotenv
REDIS_EXTERNAL_CACHE_URL=rediss://cache.example:6380/0
REDIS_EXTERNAL_BROKER_URL=rediss://queue.example:6380/0
CACHE_KEY_PREFIX=langboard:example:cache:
BROKER_KEY_PREFIX=langboard:example:queue:
```

Use modern Docker Compose with native `!reset` support. The override puts bundled
Redis services behind an inactive profile, removes their dependency edges, and
sets API, graph and Celery worker URLs and namespaces together. Existing volumes
remain declared; configuration rendering creates or deletes no data. Do not
enable `bundled-redis` alongside external mode. PowerShell selects the same
override; its runtime was not exercised in the macOS verification.

The Make entry point must stop when selection fails (for example, a missing
external broker URL). A failed selector must never become an empty argument list
for a later `docker compose` build/run/up/down command. The guard supports GNU
Make 3.81 without relying on newer `.SHELLSTATUS` behavior. Correct the reported
setting before retrying; it does not silently fall back to bundled services.

Celery uses Kombu's native `global_keyprefix` through `broker_transport_options`,
including its queue keys and Redis pub/sub channels. `BROKER_KEY_PREFIX` has the
same literal namespace validation as the cache prefix. A blank prefix retains
legacy broker names. Prefixes coordinate names; server ACLs enforce access.
Broker command and channel ACLs must cover the configured namespace, including
Kombu control/event traffic; cache command grants alone are not a broker ACL.

Phoenix has no Redis consumer in its current Elixir runtime. Its unused
`CACHE_URL` and Redis readiness dependency have been removed.

## Rollout boundaries

- Keep evictable cache and durable Celery queues on separate Redis services.
  The existing bundled cache uses volatile-lru; the queue uses noeviction and
  AOF. Separate logical database numbers do not separate memory/eviction policy.
- Roll out the prefix consistently to every API/worker/graph cache consumer.
  An altered prefix makes prior keys and locks unreachable; no automatic
  cross-namespace fallback or migration runs. Drain active attachment operations
  before changing it. Preserve the previous service for rollback.
- Drain old Celery workers and outstanding tasks before changing the broker
  prefix. Existing queued tasks are not copied to the new namespace. Preserve
  the previous queue service and prefix for recovery; do not purge them.
- Verify TLS trust, queue delivery, namespace isolation, capacity and restart
  recovery against the selected external services before retiring the previous deployment.
