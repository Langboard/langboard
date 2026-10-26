# Read performance diagnostics

Langboard exposes optional HTTP diagnostics for selected authenticated workspace
reads. Use them to investigate a slow request without collecting its content.
These diagnostics do not change access permissions or the response body.

## Capture a slow request

1. Open your browser's Network panel and reproduce the slow operation.
2. Record the endpoint template, status, request time and response size.
3. Record `X-Request-ID` and `Server-Timing`, when present.
4. Give the request ID and approximate time to your instance operator. Avoid
   sharing cookies, authorization headers, query text or card content.

The API exposes these headers through its CORS configuration. Availability
depends on the endpoint and deployed version; clients must tolerate their absence.

## Interpret the timing headers

| Metric | Measurement |
| --- | --- |
| `app` | Server elapsed time until response headers are sent |
| `dashboard_projects` | Dashboard project lookup and visibility resolution |
| `dashboard_columns` | Dashboard column lookup |
| `dashboard_serialize` | Dashboard response construction |
| `project_details` | Board detail lookup and response data preparation |
| `project_bots` | Board bot scope and schedule lookup |
| `project_visit` | Current user's board visit update |
| `project_serialize` | Board response construction |
| `board_context` | Authorized board context and column preparation |
| `board_cards` | Authorized card projection |
| `board_checklists` | Checklist projection |
| `board_bots` | Board and column bot configuration |
| `board_serialize` | Board response construction |

Durations use milliseconds. Named phases describe application boundaries; they
are not isolated database, connection-pool or scheduler measurements. `app` is
not the complete download duration. Network transfer, browser rendering and
streamed body completion can add time after response headers arrive. Do not sum
`app` and its component phases.

The middleware measures selected dashboard, board, project metadata,
notification and notification-schedule GET routes. Individual routes can add
more specific metrics. Other requests may have no timing headers.

## Operator correlation

The API writes an `http_read` log record containing the request ID, optional
trace ID, route template, status, header latency, completed duration, response
bytes and completion flag. Route templates replace user-provided URLs in these
records. The diagnostic record does not include request queries, authorization
headers or card bodies.

Compare slow and normal requests for the same endpoint and workload. Correlate
available traces before assigning the delay to SQL, worker queues or an external
service. A completed HTTP request alone does not establish successful browser
rendering or completion of a background document job.

Apply your instance's log access and retention policies. These diagnostics do
not define a latency guarantee or replace deployment and user-flow checks.

## Optional server telemetry

Configure `SENTRY_DSN` through deployment secrets to use a compatible telemetry
service. `SENTRY_ENVIRONMENT` and `SENTRY_RELEASE` distinguish environments and
releases. Error events retain exception type, source coordinates and permitted
correlation identifiers, while application request bodies, user information,
breadcrumbs, source context, local variables and exception text are excluded.

Performance transactions are opt-in through `SENTRY_TRACES_SAMPLE_RATE`, from
`0` to `1`, with default `0`. Invalid, non-finite or out-of-range values disable
sampling. An incoming sampled trace cannot override the local sampling setting.
Transactions retain timing, correlation and bounded span categories; they omit
SQL text, request paths, arbitrary span data and application content. These
records support category timing rather than statement-level SQL attribution.

After configuring a destination, verify event ingestion and the intended
operator's ability to retrieve the matching event separately. A successful
transport response alone does not establish that the event is visible in the
selected project or that an end-to-end user operation succeeded.

Short-lived document, app-event and notification recovery scripts set
`SENTRY_AUTO_ENABLING_INTEGRATIONS=false`. This keeps the SDK's default error,
logging and exception integrations while avoiding discovery imports of optional
frameworks and AI provider SDKs. The API and other processes retain the default
`true` behavior. Recovery scheduling, worker leases and business delivery do not
change. Framework-specific automatic tracing is not enabled in these three cron
processes; Celery workers retain their existing configuration.

A bounded read-only probe in the running canary API container on 2026-10-26
measured app-event module import at 22.439 seconds wall / 14.130 seconds CPU /
237904 KiB maximum RSS. An isolated child with only optional integration
discovery disabled completed at 9.377 seconds wall / 7.651 seconds CPU /
153596 KiB RSS. These are single samples under contention, not throughput or
incident-resolution evidence. No running process, environment or cron entry
was changed. Verify the exact deployed source and repeat representative runtime
measurements after release before claiming an operational improvement.

The notification entry point uses the same logger initialization and was also
observed as an active child in the canary API container. Its recovery wrapper
uses the same discovery setting; the existing `flock`, task order, delivery
retry rules and email behavior remain unchanged. No separate notification import
timing or deployed improvement has been established.
