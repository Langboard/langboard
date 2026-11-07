# Python development images

The [API development Dockerfile](../docker/api-canary-development.Dockerfile)
updates application source and locked dependencies on a compatible runtime image.
Set `API_RUNTIME_BASE` to an image digest available to your installation. The
resulting development image does not migrate or activate your deployed services.

## Document-processing workers

Build workers from a compatible worker runtime rather than an API-only image.
Workers that process documents need their document libraries and CJK fonts.
Set `INSTALL_DOCUMENT_PROCESSING=1` to install the locked document-processing
extra alongside document retrieval and image generation. Only `0` and `1` are
accepted; the processing option also checks for a Korean Noto CJK font.

Retain the worker's broker command, queue configuration and persistent storage.
Image construction does not start a worker or process queued documents.

## Graph

The [Graph development Dockerfile](../docker/graph-canary-development.Dockerfile)
uses `GRAPH_RUNTIME_BASE` and Graph's own lock and virtual environment. API and
Graph images must use compatible shared Python packages. A source copy into an
unrelated environment does not update the package imported by a running service.

## Updating an existing installation

Review release and migration requirements before replacing services. Back up
persistent data, coordinate database writers and consumers, and migrate once
before starting compatible API, worker and Graph versions together.

Keep the existing service identities, networks, OAuth state and persistent
storage. An older image may be incompatible with a migrated database; image
rollback alone does not restore the earlier schema.
