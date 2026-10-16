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
The integration regression uses a restricted real Redis user without FLUSHDB
permission and verifies that unrelated values survive a clear.

## Rollout boundaries

- Keep evictable cache and durable Celery queues on separate Redis services.
  The existing bundled cache uses volatile-lru; the queue uses noeviction and
  AOF. Separate logical database numbers do not separate memory/eviction policy.
- Roll out the prefix consistently to every API/worker/graph cache consumer.
  An altered prefix makes prior keys and locks unreachable; no automatic
  cross-namespace fallback or migration runs. Drain active attachment operations
  before changing it. Preserve the previous service for rollback.
- The current Compose Phoenix endpoint still points to bundled Redis, and
  API/graph retain bundled service dependencies. Optional external Compose and
  Celery transport/key isolation are unfinished follow-up work. Do not disable
  bundled services or describe an entire deployment as external-ready based on
  this cache boundary alone.
- This document records source compatibility. It does not establish deployed
  canary behavior, external TLS verification, service capacity or restart safety.

## Focused verification

Against an explicitly disposable Redis server:

```sh
LANGBOARD_CACHE_TEST_REDIS_URL=redis://127.0.0.1:6379/0 \
PYTHONPATH=src/shared/py:src/api PROJECT_NAME=langboard \
uv run --no-sync pytest -q \
  src/shared/py/langboard_shared/core/caching/RedisCache_test.py \
  src/api/tests/runtime/test_board_chat_attachment_upload.py
```

Never point this suite at an application database. It creates a temporary ACL
user and removes only its randomly prefixed fixture keys. Baseline cache code
fails three isolation/clear regressions; the fixed related suite passes 26
checks, including real Redis locking and existing attachment lifecycle checks.
