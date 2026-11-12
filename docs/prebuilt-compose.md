# Prebuilt Compose deployment and adoption

`docker/docker-compose.prebuilt.yaml` is a standalone production definition for
Dokploy's native **Docker Compose** mode, or another standard Compose operator.
It consumes verified API, document worker, Graph and UI images. It does not build
on the deployment host, mount application source, create a database/broker/vault,
or start a bundled Phoenix runtime. Do not combine it with the development
Compose stack or `docker-compose.phoenix-external.yaml`; the latter overrides the
development stack, whereas this definition already has no bundled runtime.

Use the existing external-Phoenix contract in [external-phoenix.md](external-phoenix.md).
The server must be a compatible Langboard Phoenix release, not an arbitrary
Phoenix service. Its editor storage, signing/authentication and Kafka ownership
must be handed over independently. This Compose file does not perform that move.

## Inputs and existing state

Set these inputs through the deployment platform's protected environment UI:

| Input | Required value |
| --- | --- |
| `LANGBOARD_API_IMAGE` | Verified API image, including `@sha256:...` |
| `LANGBOARD_WORKER_IMAGE` | Verified document-processing worker image digest |
| `LANGBOARD_GRAPH_IMAGE` | Verified Graph image digest |
| `LANGBOARD_UI_IMAGE` | Verified UI image digest; optional public endpoint overrides below |
| `LANGBOARD_API_ENV_FILE` | Protected native API runtime environment file |
| `LANGBOARD_WORKER_ENV_FILE` | Protected worker runtime environment file |
| `LANGBOARD_GRAPH_ENV_FILE` | Protected Graph runtime environment file |
| `LANGBOARD_LOCAL_VOLUME` | Existing or restore-verified application data volume identity |
| `LANGBOARD_OAUTH_VOLUME` | Existing OAuth state volume identity |
| `LANGBOARD_DEPENDENCY_NETWORK` | Existing network reaching the retained dependencies |
| `SOCKET_URL` | Browser-reachable external realtime URL |
| `SOCKET_INTERNAL_URL` | API/Graph-reachable external runtime base URL |
| `SOCKET_EDITOR_INTERNAL_URL` | API-reachable external editor base URL |

The runtime files contain the existing database/cache/broker/Kafka endpoints,
authentication, instance keys, public URLs and provider configuration. Preserve
those values and their secret ownership. The files are native Compose `env_file`
inputs, not source files or public examples. Dokploy's interpolation `.env` does
not automatically inject runtime variables into containers. Restrict file access
and keep them outside the disposable repository checkout; configure the platform's
native protected file/mount facilities. Do not copy secrets into the public repo.

The application sets internal API/Graph DNS to `api:5381` and `graph:5020` on its
own network. The external dependency network remains unchanged. Preserve the
existing dependency DNS names and access rules in the runtime files; do not
redirect them to similarly named, empty services. No service `container_name` or
fixed Compose project name is assigned. Use the platform's stable application
project name, distinct from the old dependency stack: removing old orphans from
that stack could otherwise remove retained services.

Both application data and OAuth state use `external: true`. Their identities have
no default; missing Docker volumes must cause deployment failure, not automatic
creation. During adoption, inspect the actual old volume/mount sources first.
An operator may pre-create a standard Docker local bind-backed volume pointing
to the **existing** application directory after checking its existence, ownership,
permissions and backup. This definition neither creates that directory nor
copies/restores data. Never point a new volume at an empty directory and call it
a migration. Verify access as the image's UID/GID `10001:10001`; do not apply broad
recursive ownership changes to shared storage.

## Portable UI public endpoints

The UI profile uses `docker/ui-production.Dockerfile`, target `runtime`. Build
arguments `API_URL`, `PUBLIC_UI_URL` and `SOCKET_URL` remain required and provide
backward-compatible defaults. Development continues using the existing local
Vite URLs. Promote the same immutable production UI image by setting optional
Compose inputs `LANGBOARD_UI_API_URL`, `LANGBOARD_UI_PUBLIC_UI_URL` and
`LANGBOARD_UI_SOCKET_URL` to the target environment's public API, UI and realtime
URLs. Each omitted or empty input retains that endpoint's compiled default;
set all three explicitly when isolating staging/canary from production.

The native nginx-unprivileged entrypoint runs `40-ui-runtime-config.sh` as UID
101 before nginx starts. It atomically writes only those three public values to
`/tmp/langboard-runtime-config.js`; no root filesystem ownership change, secret,
additional service or dependency is needed. `/runtime-config.js` is a blocking
same-origin script before the application's module bootstrap. An inline sentinel
rejects missing/broken assets before dependent clients initialize. Existing CSP
policies must permit this fixed inline bootstrap (with its matching hash/nonce).
Legacy SDK module entries without runtime configuration retain build defaults. Its exact nginx
route sends `Cache-Control: no-store, no-cache, must-revalidate`, including error
responses. Hashed assets keep their existing immutable policy. A missing or
invalid configuration stops production client initialization; supplied invalid
endpoints never silently fall back to compiled endpoints. Container startup does
not validate URL syntax; the browser validates before initializing clients.

API/UI accept absolute HTTP(S); realtime also accepts WS(S). Localhost HTTP
remains supported. Overrides reject malformed URLs, credentials, queries,
fragments, whitespace, control characters, backslashes and raw quotes/angle
brackets. Percent-encode path characters when needed. Values are byte-encoded
before inclusion in JavaScript, so operator input cannot execute script. Error
messages name inputs without exposing their values. Empty Compose inputs mean
no override; do not use empty inputs to disable an endpoint.

Match public API/CORS settings and compatible Phoenix releases to the target
origins; isolate internal realtime/editor endpoints and application state too.
Public inputs are not credentials and do not prove isolation, reachability or
authorization. After deployment inspect actual browser API, public-origin and
realtime destinations, CORS, and authenticated realtime/editor behavior through
your ingress. The same image digest with different runtime values requires no
rebuild. Existing external volumes, profiles and owner handoff still apply.

## Optional native overlays

- `docker-compose.prebuilt-vault.yaml` mounts an existing file-based vault
  credential at `/app/.vault-credentials`, read-only through Compose secrets for
  API, worker and Graph. Set `LANGBOARD_VAULT_CREDENTIALS_FILE`. Omit this overlay
  for providers using their configured AppRole or other environment credentials.
- `docker-compose.prebuilt-identity.yaml` mounts an existing identity-proof
  signing key and sets its native `IDENTITY_PROOF_SIGNING_KEY_PATH`. Set
  `LANGBOARD_IDENTITY_SIGNING_KEY_FILE`. Do not rotate or generate a replacement
  during adoption. Compose file secrets retain source-file access permissions;
  verify readability by the non-root API user.
- `docker-compose.prebuilt-ports.yaml` preserves an existing ingress/tunnel
  origin without introducing another TLS proxy. Set `LANGBOARD_UI_BIND` and
  `LANGBOARD_API_BIND` to the verified `address:port` pairs. Defaults are absent.
  A loopback bind works only for an ingress on the same host. A separate tunnel
  host needs the existing permitted private address and firewall policy. Never
  stop an existing listener merely because the new platform normally uses it.

Dokploy Domains may instead route directly to UI port `8080` and API port `5381`.
Do not expose Graph or create a second public TLS entry without a deployment
requirement. Retain MCP streaming timeout/buffering and forwarded-host/protocol
settings on whichever ingress is actually used. Readiness alone does not prove
OAuth, upload or editor access through that ingress.

## Ownership, staging and cutover

All runnable services require explicit profiles. The `application` profile starts
API, Graph and UI; `workers` additionally starts the broker consumer. Default
`compose up` does not start an application owner. Explicitly starting a service
still starts it: profiles are an operator guard, not an authorization mechanism.
Use Dokploy's supported Compose command/profile configuration, not another
activation controller layered on top.

For Dokploy v0.30.8, its native custom Compose command replaces the entire default
command. Retain the displayed application project name, project directory,
interpolation env-file and all selected `-f` files. Add the explicit profiles and
use `up -d --no-build`; do not leave the default `--build` in place. For example,
with your real protected paths and platform-assigned application name:

```sh
compose -p <application-name> --project-directory <checkout-directory> \
  --env-file <interpolation-env-file> \
  -f <checkout-directory>/docker/docker-compose.prebuilt.yaml \
  -f <checkout-directory>/docker/docker-compose.prebuilt-ports.yaml \
  --profile application --profile workers up -d --no-build
```

The platform prefixes this native command with `docker`; the placeholders are
not executable configuration. Include the vault/identity `-f` overlays when the
old installation uses those files. Do not use the old dependency stack's project
name or add `--remove-orphans` against that stack.

On v0.30.8, the platform's isolated-deployment option also attempts to connect a
`dokploy-traefik` container. For an existing tunnel origin with no such proxy,
leave that platform option off and use this definition's separate bridge and
explicit dependency network. This does not make either network public or grant
access to other projects. Verify the rendered and actual network membership on
the installed version. Do not install another proxy merely to satisfy that
platform option.

The existing API startup with `WORKER=main` **runs database upgrades/bootstrap and
starts the scheduler/recovery jobs**. This behavior is retained. The worker uses
`WORKER=broker` and `run:broker`; it does not run API bootstrap. Do not run a staging
API against the live writable DB, broker, OAuth store or application directory.
Use restored isolated state and separate queues/keys/remote editor ownership.
Graph can initiate external model/tool effects when called; isolate those too.

A database restore can retain pending deliveries, schedules and external
destinations even with a separate broker. There is currently no instance-wide
supported pause/disable flag for all recovery jobs. `NOTIFICATION_EMAIL_OUTBOX_ENABLED`
only controls its email path. `run --lifespan off` does not prevent the scheduler:
`ServerRunner.run()` registers and reloads cron before Uvicorn starts. Do not use
`ENVIRONMENT=development` to alter authentication/security behavior for staging.

When restored integrations cannot be safely isolated, preserve the restore for
migration/data checks without starting the normal API against it. Start functional
staging with a separate clean database, clean application/OAuth directories and
isolated dependencies and credentials instead. The normal production-mode
`run` command with `WORKER=main` upgrades the schema, bootstraps common workflow
and relationship definitions, initializes default internal bots and creates an
initial administrator when none exists. Supply staging-only `ADMIN_EMAIL` and
`ADMIN_PASSWORD` through protected runtime configuration; then create test boards
through the existing UI/API. This is the existing CLI bootstrap, not an additional
seed service. `db:seed` is marked as a development command, can be omitted from
executable distributions and requires an actual registered seed; do not assume
it provides a production test tenant. A clean staging instance does not prove
restored credentials, attachments or OAuth continuity; verify those separately.

Before production activation:

1. Record all current image digests, database revision, data mounts, OAuth/signing
   identity, dependency endpoints and scheduler/consumer/editor owners.
2. Back up and restore into disposable isolated state. Verify database/card and
   attachment integrity, key decryption, OAuth continuity and editor recovery.
3. Render the selected files with `docker compose config --quiet`, then compare
   `docker compose config --images` to the immutable release receipts. Avoid
   printing a fully resolved config because it contains runtime secrets.
4. Rehearse the intended API image's migration against the isolated restored DB.
   An explicit `docker compose run --rm --no-deps -e IS_CLI=true api uv run
   --no-sync langboard db:up` runs the upgrade command without starting the API
   scheduler. Set `IS_CLI` before process import because CLI dependencies can
   initialize KeyVault before the command dispatcher sets the CLI flag. This
   selects the existing local CLI vault under `DATA_DIR/vault-data`; use disposable
   isolated application data and do not pass this flag to the API runtime.
   The upgrade writes to the database and must target the isolated DB during
   rehearsal. It does not validate full API bootstrap or production key access.
5. Drain writes and document jobs, stop old API/scheduler and broker consumers,
   finish the final state synchronization, and verify no competing editor owner.
   Then explicitly activate `application` and `workers` on the final state.
   Keep the old deployment and data available for rollback. Never run two API
   schedulers against the same state during cutover.
6. Verify authenticated internal/external card and attachment access, SSO/MCP
   without forced identity replacement, editor save/reconnect, timers, signals
   and performance through the original public URLs. Confirm one recovery and
   consumer owner, not just healthy container probes.

Rollback is an explicit data/owner decision, not merely an older image selection.
Inspect schema compatibility; do not automatically downgrade or delete data.
Drain and stop the new owners before restoring the retained deployment's owners.
Verify current card/attachment/editor state and queues after the reversal.
Never use `down --volumes`, fresh-volume rebuilds or destructive removal as a
release step. The operator owns restore evidence and platform removal settings.

See [Dokploy Compose](https://docs.dokploy.com/docs/core/docker-compose) for native
environment, file mounts and volume handling. Inspect the installed version's
server onboarding and cleanup behavior before adopting a live Docker host;
registration and Docker/Swarm/proxy installation have different effects.

Before rollout, verify the selected immutable image, injected credentials, file
permissions, external network reachability and retained volume identities on the
target host. Validate backup restoration and owner handoff before retiring the
previous deployment. Rendering Compose configuration alone does not validate
those runtime conditions.

## Retained legacy Kafka maintenance

The retained dependency stack has a separate Compose owner from the prebuilt
application. Identify it from the running containers' Compose project, working
directory and config-file labels before preparing maintenance. Reuse that exact
project and ordered file set; an application deployment does not establish
ownership of the brokers. Preserve all three broker volumes and the existing
KRaft cluster ID, node IDs, advertised listeners and network aliases.

Preserve the original interpolation inputs as well as container runtime values.
In particular, `PROJECT_NAME`, `KAFKA_CLUSTER_ID` and `DOCKER_NETWORK_MTU` must
match the installed dependency stack. The base Compose file already supports
`DOCKER_NETWORK_MTU`; omitting an installed value of `1450` renders its default
`1500` and can request replacement of the shared network. Do not assume that
the default matches a running installation. Keep protected interpolation files
outside public logs and source control.

Before changing a broker, use the installed Compose version's native `--dry-run`
with the exact intended command and inputs. Check its plan alongside a protected
render comparison and actual network inspection. A one-service `up --no-deps`
can still reconcile shared network configuration: `--no-deps` does not make
network recreation safe. Stop if the plan replaces the network or changes
unrelated services, images, environment, mounts or identity. Correct the existing
inputs before retrying; do not introduce a replacement network to hide drift.

If a failed Compose operation has already stopped a broker, inspect the original
container's network endpoints before treating `docker start` as recovery. A
container may start with its former endpoint detached. Recovery belongs to the
dependency operator and must restore the verified original network identity and
aliases. Confirm broker readiness, controller quorum and replica ISR recovery
before any further broker replacement. A running container or healthy application
probe alone does not establish Kafka recovery. Retain the failed plan and recovery
receipts with the maintenance record.

Kafka TLS/SASL settings, certificate mounts and external broker selection are documented in [external-kafka.md](external-kafka.md).
