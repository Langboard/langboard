# Development with separate repositories

Langboard components can be developed in sibling checkouts: `api`, `web-ui`,
`shared-py`, `core-ts`, `socket`, `graph` and `deploy`. Use compatible component
versions and keep credentials outside the source tree.

Use Docker Compose 2.24.4 or newer for the overlays' `!override` tags. Native
component builds also require a builder that supports named build contexts.

## Source updates with Compose Watch

The [Watch bridge](../docker/multi-repo-watch-bridge.compose.yaml) adds source
synchronization to compatible monorepo development images. Set `MULTI_REPO_ROOT`
to the absolute parent directory containing your sibling checkouts. Retain your
installation's Compose project, environment file and existing service definitions
when adding the overlay.

The bridge synchronizes Web source to the Vite development container. Python
restart rules are disabled by default. API startup can run migrations and
bootstrap tasks, so enable restart rules only for a compatible, coordinated
development installation. A shared-source change can affect several services;
independent Watch events do not restart them atomically.

Dependency manifests, locks, database models and migrations require a coordinated
build or migration rather than source synchronization. Core TypeScript changes
require rebuilding the package consumed by Web. Elixir socket changes require
compilation; copying source into a release image does not compile it.

## Native component images

The [native image overlay](../docker/multi-repo-development.compose.yaml) uses
the component repositories' own build inputs. It is an alternative to the Watch
bridge and must not be combined with it.

The native overlay is a build-only candidate; runtime compatibility is unverified.
Parsing the Compose configuration or building images does not establish safe
activation. Before starting services, verify compatible entrypoints, healthchecks,
ports, package imports, shared protocols and database migrations for the selected
images. Preserve storage and coordinate existing writers and consumers during a
transition.

Build the shared Python wheel before API, worker and Graph. Build Core TypeScript
and Web assets before constructing the static Web image. The static Web image
rebuilds completed assets; it does not provide Vite hot reload for raw TSX.

Match commands, ports, healthchecks, import paths and storage to the selected
runtime images. Validate the merged Compose configuration before activating it.
Do not publish a fully rendered installation configuration: it can contain secrets.

Some complete deployment configurations also require the separate MCP component.
Use the deployment repository's component requirements when choosing which
services to run.
