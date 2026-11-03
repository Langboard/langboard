# Python canary development runtime

`docker/api-canary-development.Dockerfile` reconciles application source and the
checked-in lock on an existing pinned canary runtime. The default remains the API
runtime with document retrieval and image generation. It is a development image,
not proof that the canary services or schema have been upgraded.

For the broker/document worker, build the same target with both arguments:

- `API_RUNTIME_BASE`: the exact existing worker image digest, retaining its native
  document libraries and CJK fonts.
- `INSTALL_DOCUMENT_PROCESSING=1`: install the locked document-processing extra as
  well as retrieval/image dependencies and verify a Korean Noto CJK font exists.

Keep the worker's existing `uv run --no-sync langboard run:broker` command, runtime
identity, mounts, network, queues and retained data. Do not substitute the API base
for the worker: that would omit its native document runtime. Only the explicit
values `0` and `1` are accepted for the processing argument.

## Coordinated schema transition

Revision `acde123f4567` retires legacy workflow configuration columns and requires
old API, workers and cron to be quiesced. The existing worker queries the same
shared models as the API. Replacing only the API does not complete that transition.

Build and validate the required runtime images before a controlled writer stop.
Use the existing Dokploy services and retained data; do not start a new worker
against production queues during rehearsal. After a fresh consistent backup,
apply the reviewed migration once, then activate compatible services together.
Keep queue payload compatibility and rollback feasibility explicit; a successful
DB restore is not proof that old application images can run on the new schema.

The isolated rehearsal from source `027f2522b2ecfd9bab9519a887ca67ff0b20e501`
upgraded a same-backup clone from `a28196e32054` to `fabc234d5678`. All 145 retained
non-version table projections matched row counts and SHA-256, and legacy entry
effects were checked against their converted rules. This does not replace native
application/bootstrap, queue/cron and authenticated browser E2E validation.
