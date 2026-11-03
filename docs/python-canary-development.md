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

## Verified development image pair

The [development Compose overlay](../docker/python-canary-development.compose.yaml)
records the two source pins without copying retained storage, network or secret
configuration into a second owner. API source `027f2522b` and worker source
`cf973dde5` have identical `src/api`, `src/shared/py`, `pyproject.toml` and `uv.lock`
contents. Their distinct source pins reflect the later build-recipe change.

The worker was built through the existing Dokploy backend service with the native
command ending in `--profile worker build worker`. Image index digest:
`sha256:c0f32900e00634381894fc346ed9a755712ae31c811d98822d60288dc947a314`;
size: 859,735,908 bytes. Locked dependency installation, CJK font check and Python
compilation passed. A non-root probe with `--network none --memory 1g --cpus 1`
imported `DocumentConverter`, `VlmPipeline` and `HierarchicalChunker`, exit 0.
`torch`, `transformers`, `tokenizers` and `safetensors` were absent. Installed
Docling Slim/Core versions were 2.144.0/2.105.0.

This proves importability and dependency isolation, not actual document conversion,
external VLM responses, broker processing, service cutover or authenticated E2E.
The running production worker was not replaced by the build command.

## Graph source and lock

Graph has its own lock and virtual environment. Use
`docker/graph-canary-development.Dockerfile` on its existing pinned Graph runtime;
copy the current Graph/shared sources and SDK vendor wheel, then reconcile
`src/graph/uv.lock`. Do not use the API virtual environment or a source-only copy
to infer Graph runtime compatibility. Keep the existing non-root runtime identity,
network, environment and retained storage. The recipe does not start the service
or establish Graph/API resume and authorization compatibility by itself.
