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

The product endpoint is `/v1/builds/project-images`; the validation endpoint
remains `/v1/builds/project-validations`. `SOURCE_CHANNEL` is `default` or
`canary`; the current workflow explicitly selects `canary`. The broker verifies
the current actor, repository, workflow, run, branch and approved source. The
client verifies the returned executor commit against `IMAGE_BUILD_COMMIT` and
prints only the credential-free receipt. Executor approval and immutable tag
verification belong to the broker and executor, rather than a second client
using a stored token.

Keep the existing request ID after a timeout, rate limit or server error. Use
GET to reconcile it; do not dispatch a new run merely because observation timed
out. No automatic retry, credential redirect or legacy-token fallback exists.

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
