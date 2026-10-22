# Federated image requests

The request workflow uses one standard-library client for review validation and
product image requests. Both use the current GitHub Actions OIDC identity; the
source repository does not need a central dispatch token.

The request job uses the operator-configured `CI_RUNNER` in a standard Ubuntu
job container. It is skipped when no runner is configured; it does not fall back
to GitHub-hosted compute. The container supplies Python, Git and CA certificates
for the request client. Validation and image building remain executor operations.

```sh
# Review validation: v1, current PR required, review ref only.
python3 scripts/image_build/request_validation.py

# Product image: v2, explicit SOURCE_CHANNEL, no sourcePr.
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

The product endpoint is `/v1/builds/project-images`; the validation endpoint
remains `/v1/builds/project-validations`. `SOURCE_CHANNEL` is `default` or
`canary`; the current workflow explicitly selects `canary`. The broker verifies
the current actor, repository, workflow, run, branch and approved source. The
client verifies the returned executor commit against `IMAGE_BUILD_COMMIT` and
prints only the credential-free receipt. Executor approval and immutable tag
verification belong to the broker and executor, rather than a second client
using a stored token.

After a timeout, rate limit, server error or pending admission receipt, the
client makes one GET using the same request ID. It never repeats POST. If that
read remains inconclusive, retain the request ID for inspection rather than
dispatching another run. Explicit broker rejections and invalid receipts fail
closed; cancellation propagates. This bounded recovery does not wait for remote
validation or image publication. No credential redirect or legacy-token
fallback exists.

This client requires the deployed product broker contract. A local passing test
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
