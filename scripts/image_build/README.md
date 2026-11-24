# Federated image requests

Review validation uses the existing standard-library OIDC client. Product images
use the public company reusable workflow at the immutable commit declared in
`.github/workflows/request-image-build.yaml`; no local product request client or
stored dispatch token is used. The shared workflow resolves the current executor
from the broker, then submits schemaVersion 3 with a profile-scoped request ID
and the resolved If-Match precondition.

The validation job runs directly on `IMAGE_BUILD_REQUEST_RUNNER`, falling back to
`CI_RUNNER`, with standard Python setup and no job container. The public product
workflow uses the standard `BUILD_REQUEST_RUNNER` selector. Configure that value
with an existing trusted runner label when the repository has a dedicated pool.
Validation and image building remain central executor operations.

```sh
# Review validation: v1, current PR required, review ref only.
python3 scripts/image_build/request_validation.py

# Reconcile the same request from the same Actions run identity.
python3 scripts/image_build/request_validation.py --read
```

The public product workflow uses `/v1/builds/project-images`; the validation endpoint
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

These paths require the deployed broker contract. A local passing test
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

The transport-only job runs directly on its selected runner, without a job
container or package-manager bootstrap. Operators may set the non-secret
`IMAGE_BUILD_REQUEST_RUNNER` JSON runner selector to an existing trusted request
runner; `CI_RUNNER` remains the fallback. This changes scheduling only. GitHub
OIDC identity, executor preconditions, PR/source binding and remote build/validation
remain owned by the existing broker. The request job does not build images or
receive product credentials.

Validation admission binds each profile to its approved caller workflow. Use
`request-image-build.yaml` for `default`; use `request-validation-{profile}.yaml`
for `api`, `worker`, `ui`, `graph` and `socket`. These five standard reusable
workflow callers fix the profile and validation-only mode, while sharing the
same OIDC client job. They do not inherit secrets or request product publication.
The OIDC caller workflow identity remains the profile-specific approved path.
