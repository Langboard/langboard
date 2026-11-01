# Company BuildPlane callers

The six validation workflows and five canary runtime profiles call the public company workflows directly. Product code owns Dockerfiles, profile definitions, tests and runtime health. The shared caller owns OIDC, exact PR discovery, executor resolution, strong ETag/If-Match, receipt validation, retained request identity and failure guidance. No product-side request transport or executor pin is retained.

The workflow files are the source of truth for the published reusable caller revisions. Review requests and runtime publication use separate callers; do not copy a historical onboarding pin or HOLD state from an old receipt into a new request. Before publication, verify the currently deployed central API/worker, approved project/profile contract, exact-source results and existing deployment gates. A central source merge or successful request submission does not establish deployed capability. Keep current blockers and execution receipts on the shared support card linked below.

- Automatic review pushes submit default, api, worker, ui, socket and graph through six independent source workflows. Current PR discovery belongs to the shared caller; no PR number is hardcoded.
- Manual runtime validation uses its fixed request-validation-<profile>.yaml workflow. The default validation is request-image-build.yaml with validation_only=true and profile=default. Unsupported combinations fail before a request.
- Deploy pushes submit api, worker, ui, socket and graph in that order through native needs dependencies. This serializes admission, not asynchronous remote execution. Each shared product request retains its profile-suffixed correlation and outputs. Failed or cancelled dependencies stop further submissions.
- Manual publication requires a deploy ref, validation_only=false and a runtime profile. It submits only that profile. The default profile is validation-only.
- Concurrency is ref/profile scoped and never cancels an admitted uncertain request. Follow the same retained central execution; do not rerun to escape an observation timeout.
- Source request success proves admission only. The exact per-profile yamon/remote-validation/<profile> statuses, source SHA, validation artifacts, image scans, signatures and digest must all satisfy the existing gates before GitOps/Dokploy deployment. Authenticated in-app E2E and data/rollback acceptance remain separate.

Onboarding uses the centrally provided BUILD_REQUEST_RUNNER and IMAGE_BUILD_BROKER_URL. No new token, private workflow access, runner or builder is configured by the product. Support: https://langboard.yamon.io/board/6v0q6uY44jn/hmD5UBzCv6O .

Run python -m unittest discover -s scripts/image_build -v for product event/profile/serialization/packaging boundaries. The company repository owns the transport and authorization regressions. The local passing contract tests do not establish deployed API availability or canary completion.

The API profile also declares an isolated PostgreSQL integration stage. It reuses
the locked API validation dependencies and runs the Phoenix card authorization
and subscription batch/revocation tests, plus the 1,000-item timer projections
for checklist, card, and My Work views. Timer fixtures live under
`src/api/tests/card_workspace/timers/`, with isolated PostgreSQL schemas and
foreign-key enforcement on SQLite. The central fixture supplies
`LANGBOARD_FILE_TEST_DATABASE_URL` through `postgres.sqlalchemy-url`; a missing
fixture fails before tests instead of accepting skipped PostgreSQL cases.
The stage preserves the child exit code in both its process status and result
report. This requires a separate diagnostic image and the central ephemeral
database, never an application database or a product-image publication.

The `default` profile runs the Phoenix integration image with disposable
PostgreSQL services. Its `compactor-contracts.xml` report exercises the dedicated
profile, explicit board grants, source snapshots, compaction persistence and
commands, automatic archive scheduling, and their migrations. This report is
separate from the workflow, graph and app reports. All declared commands must
succeed for the integration result to pass; a prior image signature alone does
not establish these tests or an application rollout.


## Signed migration input identity

The five runtime profiles declare the same versioned `migrationIdentity` input
selection in `.fractalops/image-build.json`. The central producer hashes exact
committed Git blobs and signs the resulting identity in its existing schema2
manifest. This allows an independently approved consumer to reuse compatibility
review across commits whose database inputs are unchanged. It does not establish
a live schema, backup, drain, deployment, or application acceptance result.

The selection covers the entire Alembic graph and environment, ORM models and
custom DB types, database configuration, model discovery, migration CLI, startup
bootstrap definitions, dependency locks, and the runtime Dockerfile. Runtime
profiles share one selection because they are deployed against the same retained
database. Ordinary UI and service/task changes are outside this identity.
The Dockerfile and shared CLI/bootstrap/type roots deliberately invalidate the
baseline even for some non-schema edits; changing the runtime dependency or
startup boundary requires renewed review. No source commit is hardcoded.

A new migration, selected model/type/loader change, dependency update, or selection
change must renew the genuine compatibility baseline with the existing backup and
drain evidence. The producer never creates that baseline automatically. The
consumer must verify the original signature and the complete version, selection
hash, tree hash, and file count before reusing it. Changes that introduce another
schema loader or migration input must extend this explicit selection in review.
