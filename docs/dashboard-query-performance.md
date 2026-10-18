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
