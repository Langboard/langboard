# Background recovery scheduling priority

Native canary acceptance reproduced intermittent realtime authorization and
readiness timeouts during CPU contention. The non-root cron scheduler can run
four cold recovery subprocesses concurrently alongside the API. A process
snapshot confirmed active recovery subprocesses consuming CPU while requests
exceeded the existing realtime response budget. This does not establish the
cost of each import or claim that scheduler priority creates physical capacity.

After acquiring the existing scheduler ownership lock, apply the standard
POSIX `nice(10)` to that dedicated scheduler process. Recovery children inherit
that priority; the separate API process does not. Keep the existing cron tab,
file locks, four-job limit, refresh interval, per-job overlap protection and
recovery behavior. Introduce no new scheduler, queue, dependency or retry loop.

The regression launches the real scheduler and a real scheduled child, verifies
the inherited priority, and confirms the calling process priority is unchanged.
It fails against the original implementation and passes with the change. All
five existing and new scheduler tests pass; the API image profile includes them.

Deployment acceptance remains separate: verify API priority remains unchanged,
new background jobs inherit the lower priority, and actual authenticated UI
changes remain stable under representative load. Lower priority can delay
recovery during sustained foreground demand; monitor recovery latency and
backlogs. It does not replace capacity or cold-import optimization.
