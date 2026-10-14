# Federated image requests

The request workflow uses one standard-library client for review validation and
product image requests. Both use the current GitHub Actions OIDC identity; the
source repository does not need a central dispatch token.

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
