# Federated image requests

The request workflow uses one standard-library client for review validation and
product image requests. Both use the current GitHub Actions OIDC identity; the
source repository does not need a central dispatch token.

The request job uses the operator-configured `CI_RUNNER` in the official
`python:3.12.15-slim-bookworm` container pinned to an immutable image index digest.
It is skipped when no runner is configured; it does not fall back to GitHub-hosted
compute. Python and CA certificates are included in the image, so the job installs
no packages at runtime. A preflight verifies Python 3.12, the default trusted CA
store, and the image's `tar`/`gzip` tools before checkout. The request client uses
only the Python standard library.

The pinned official checkout action uses its native REST archive fallback when
Git is absent, selecting the triggering commit and extracting it with `tar`.
Credentials are not persisted. This behavior is documented in the
[pinned checkout README](https://github.com/actions/checkout/blob/3d3c42e5aac5ba805825da76410c181273ba90b1/README.md)
and implemented in its
[archive helper](https://github.com/actions/checkout/blob/3d3c42e5aac5ba805825da76410c181273ba90b1/src/github-api-helper.ts).
Validation and image building remain executor operations.

```sh
# Review validation: v1, current PR required, review ref only.
python3 scripts/image_build/request_validation.py

# Product image: schema3, explicit SOURCE_CHANNEL, no sourcePr.
python3 scripts/image_build/request_validation.py --product

# Reconcile the same request from the same Actions run identity.
python3 scripts/image_build/request_validation.py --read
python3 scripts/image_build/request_validation.py --product --read
```

Pushes to `review/canary-*` automatically request validation with the full
`default` integration profile through the same OIDC broker. Changes to
`scripts/image_build/**` also trigger this workflow. The validation step uses
only the ephemeral Actions `GITHUB_TOKEN` with `pull-requests: read` to query the
standard GitHub REST API. Before requesting OIDC identity, the client requires
exactly one open PR with the same source repository, branch and current commit;
missing, ambiguous, stale, fork, incomplete and failed API responses fail closed.
An open PR must already exist when the push runs; opening it later requires a
new push or the existing manual validation dispatch. Manual dispatch preserves
the selected profile and explicit PR number. Product requests remain on
`deploy/**` and use the separate product endpoint.

PR discovery does not grant release authority: the broker still checks the
protected canary base, exact source authority and approval. Automatic review
validation publishes no product image. Candidate promotion and deployment remain
central GitOps operations, without a per-deployment chat request.

Automatic `review/canary-*` pushes replace older request transports in the same
branch/profile concurrency group, so an obsolete job waiting for a runner does
not prevent validation of the current PR head. Deployment pushes and manual
dispatches do not cancel preceding transports. A later automatic review push
can supersede a manual validation in the same review branch/profile group;
product requests use deployment refs and remain serialized. Cancelling a validation transport
does not revoke a request already admitted by the broker: retain that run's
request ID and receipt for reconciliation instead of resubmitting it. Shared
runner admission, exact-source verification and product release gates still apply.

The product endpoint is `/v1/builds/project-images`; the validation endpoint
remains `/v1/builds/project-validations`. `SOURCE_CHANNEL` is `default` or
`canary`; the current workflow explicitly selects `canary`. The broker verifies
the current actor, repository, workflow, run, branch and approved source. The
client verifies the returned executor commit against `IMAGE_BUILD_COMMIT` and
prints only the credential-free receipt. Executor approval and immutable tag
verification belong to the broker and executor, rather than a second client
using a stored token.

Review validation retains schema1 and the three-part Actions request ID. Product
requests use schema3 only and append the selected profile to that ID. The exact
product envelope contains schemaVersion, sourceRepository, sourceCommit,
sourceRef, sourceChannel, profile and requestId. No schema2 fallback remains.
Migration identity is a separate signed producer/promotion contract, not another
transport field.

Product POST is sent at most once. Ambiguous admission, HTTP 429/5xx or a pending
receipt is reconciled through GET on the same request ID with a fresh Actions
OIDC assertion for every lookup. The entire operation has a 90-second deadline,
individual HTTP timeouts stay within the remaining budget, and backoff is
0/2/4/8/16/30 seconds. Success requires a dispatched receipt with a positive
integer downstream run ID and the exact central FractalOps Actions run URL.
Null IDs, exhausted deadlines and unresolved outcomes fail with the retained ID;
invalid receipts, pin mismatches, explicit rejections and other 4xx responses
fail immediately. No ambiguous POST is repeated. Review validation preserves its
existing one-GET admission reconciliation contract.

The caller remains project-owned public code using the existing approved
workflow path. GitHub rejects a public repository calling the company's private
reusable workflow; no private source is vendored and no repository visibility or
access policy is changed. The public client implements the same strict product
API contract, HTTPS/OIDC identity, no-redirect policy and executor pin checks.

Product requests require the matching deployed schema3 executor, broker and ledger contract. A local passing test
does not establish broker deployment, image publication, scan/signature success,
or application deployment. Product requests must be admitted for their exact
source and profile before release.

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
