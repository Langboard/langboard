# Standard container job contract

Status: reviewed source preparation; live runner acceptance pending.

Four workflows now declare an `ubuntu:24.04` job container and Bash run steps: app contracts, workflow stages, panel SDK, and immutable image requests. Standard tools are installed in the job rather than assumed from a runner host. Image request execution also installs Python and allows five minutes for bootstrap and dispatch.

## Database networking

Docker container jobs expose a network in `job.container.network`; the PostgreSQL service is addressed by its declared `postgres` name. For supported hooks using shared Pod networking without this Docker network field, the job uses `127.0.0.1`. Both cases use port 5432. Host port context is not used as a portable service discovery contract.

The database-bearing workflows perform an actual `SELECT 1` with a ten-second connect timeout before tests. They fail when the service is unavailable, instead of presenting skipped database coverage as success.

References:

- [GitHub job context](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts#job-context)
- [GitHub PostgreSQL services](https://docs.github.com/en/actions/tutorials/use-containerized-services/create-postgresql-service-containers)
- [ARC container modes](https://docs.github.com/en/actions/how-tos/manage-runners/use-actions-runner-controller/deploy-runner-scale-sets)
- [Official Kubernetes prepare-job hook](https://github.com/actions/runner-container-hooks/blob/main/packages/k8s/src/hooks/prepare-job.ts)

The app-contract workflow includes the external-member card-creation regression from PR #686. The image request test selects its named request step so adding bootstrap steps does not replace the tested dispatch implementation.

## Verification

- `actionlint` 1.7.7 on all four changed workflows: passed, with shellcheck disabled because it is unavailable locally.
- Seven immutable executor request tests: passed. Network and credentials are test doubles; no image request was submitted by this test.
- Changed-file diff check: passed.

Live acceptance must establish service startup, checkout, JavaScript actions, dependency installation, PostgreSQL access, actual regressions, artifact upload and the final image-request receipt. Local checks do not establish these outcomes.

Runner implementation, organization access and deployment approval remain separately managed. This source change does not modify branch protection, runner access or deployment approvals, and does not include private infrastructure configuration.
