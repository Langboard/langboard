# Multi-repository development candidate — 2026-11-03

Status: prepared recipe; split-image runtime adoption is unverified. Owner: the
curator of the existing Dokploy deployment. This record does not accept a topology
change or authorize a cutover. The current canary recipes remain usable unchanged.

The seven checkouts are independent feature-branch workspaces, owned by their
respective upstream repositories. Use a private parent directory containing
`api/`, `web-ui/`, `shared-py/`, `core-ts/`, `socket/`, `graph/`, and `deploy/`.
`docker/multi-repo-sources.json` records the exact inspected revisions, artifact
edges and compatibility gates. Record the actual feature branch, HEAD and dirty
state of each checkout before building; the inspected main revisions are evidence
anchors, not claims that those feature branches exist or are compatible.
No forks, permission changes, commits or branch changes are part of this recipe.

## Fast bridge on the existing canary images

`docker/multi-repo-watch-bridge.compose.yaml` is a separate fast-development mode.
It changes only Watch configuration, preserving existing image pins, builds,
UIDs, commands, environment, secrets, networks, ports and retained volumes. Never
combine it with the native split-image candidate below. Default runtime rules
target only `web`: sibling `web-ui/src` syncs to `/work/src/ui/src` for Vite HMR;
`web-ui/vite.config.ts` syncs to `/work/src/ui/vite.config.ts` and restarts Web.
The source baseline must already be compatible with the pinned canary: raw upstream
Web/Core contracts differ and initial synchronization is not an integration test.

Python rules are inert data under `x-python-watch-opt-in`. They do nothing unless
the operator explicitly extracts and appends the opt-in overlay. API and worker
map `api/langboard` to `/app/src/api/langboard`; Graph maps
`graph/langboard_graph` to `/app/src/graph/langboard_graph`; all three map
`shared-py/langboard_shared` to `/app/src/shared/py/langboard_shared`. Each uses
native `sync+restart`, with initial sync disabled. The baseline must already match
the running images; Watch is for subsequent reviewed edits, not initial branch
replacement. Exclude migrations, shared `domain/models`, bytecode and compiled
extensions. These exclusions are guardrails, not a schema-change detector.

Before opting into Python restarts, inspect the actual retained commands and image
imports, writable target ownership and Watch prerequisites (`stat`, `mkdir`, `rmdir`).
The current Dockerfiles copy API/shared source with UID 1000, run as 1000:1000,
install editable packages from root `pyproject.toml`, and use `UV_NO_SYNC=1` with
`uv run --no-sync`. Graph has its own `/app/src/graph/.venv`, working directory and
editable shared dependency. Confirm actual `langboard.__file__`,
`langboard_shared.__file__` and `langboard_graph.__file__` resolve to the targets
above; file presence alone is insufficient. Do not assume the split native
`/opt/venv` images can use this bridge.

**API startup currently runs database upgrade and bootstrap code:** inspected
`src/api/langboard/CLI.py` calls `DbUpgradeCommand` when `Env.WORKER == "main"`.
Every Python restart can re-enter startup hooks and scheduler setup. Therefore API,
worker and shared-source restart rules are deliberately non-default. Before
enabling them, prove the matched baseline has no pending migration/bootstrap,
review scheduler ownership, drain/coordinate workers and validate restart safety.
Stop Watch before any schema/model/migration, dependency manifest/lock, generated
artifact, SDK, registry configuration, protocol or startup-hook change. Review the
diff across all sources, perform the single coordinated migration/build gate with
writers stopped as needed, and establish a new coherent baseline before resuming.
Independent Watch events do not atomically restart API, worker and Graph.

Use this exact invocation pattern on the existing owner's machine. Set the first
three paths to its recorded private paths, and retain **all** existing owner
`--env-file`, `--project-directory`, `-p` and `-f` arguments in `OWNER`; the example
names below are placeholders, not a second deployment definition. If Web and
backend have distinct existing owner projects, use one matching array per owner
and select only that owner's services; never combine their runtime identities.

```sh
export MULTI_REPO_ROOT=/absolute/private/sibling-root
BRIDGE=/absolute/recipe-checkout/docker/multi-repo-watch-bridge.compose.yaml
# Reproduce the owner's recorded CLI arguments exactly, without changing its project.
OWNER=(docker compose --project-directory /absolute/existing-owner -p EXISTING_PROJECT
  --env-file /absolute/protected/owner.env -f /absolute/existing-owner/compose.yaml)
"${OWNER[@]}" -f "$BRIDGE" config --quiet
# After baseline/path/permission review; no build or service creation is requested:
"${OWNER[@]}" -f "$BRIDGE" watch --no-up web
```

Optional Python mode, only after its restart/migration gates pass, extracts just
the inert rule data using Compose and Python's standard library. It reads no owner
environment or runtime secrets. Keep the generated file outside source control:

```sh
PYTHON_WATCH=$(mktemp /tmp/langboard-python-watch.XXXXXXXX)
docker compose -f "$BRIDGE" config --no-consistency --format json |
  python3 -c 'import json,sys
rules=json.load(sys.stdin)["x-python-watch-opt-in"]
print("services:")
for name, service in rules.items():
    print("  "+name+":\n    develop:\n      watch: !override")
    for rule in service["develop"]["watch"]:
        print("        - "+json.dumps(rule))' > "$PYTHON_WATCH"
"${OWNER[@]}" -f "$BRIDGE" -f "$PYTHON_WATCH" config --quiet
# Select only services actually belonging to this recorded owner:
"${OWNER[@]}" -f "$BRIDGE" -f "$PYTHON_WATCH" watch --no-up api worker graph
# Remove the temporary overlay after stopping Watch:
rm "$PYTHON_WATCH"
```

Stop an existing watcher before changing modes; run one watcher per runtime owner.
No `up` is needed for already-running compatible canary services. Do not issue
these invocations until the owner-specific render and all gates pass. They were
not executed against live services by this task.

Core TypeScript source sync is intentionally absent: build and validate the
packed client/server artifact, reconcile the canary's `file:../shared/ts`
dependency and lock, rebuild its image, and validate Web imports before resuming
HMR. Socket sources require the native Elixir compile/release and runtime gate.
Python dependency/lock and native library changes similarly require the current
canary Dockerfiles and independent Graph lock reconciliation; this bridge installs
nothing. Deploy configuration changes require manual render and curator adoption.

Bridge trap version 2 (2026-11-03): fast sibling edits can bypass integration or
re-enter migration hooks. Mitigation is Web-only default, inert Python opt-in,
reviewed coherent baseline, excluded schema paths and mandatory stop/rebuild/
migration gates. Revisit when startup hooks, source layouts, dependency installation
mode or runtime pins change. Structural merge proof does not establish live HMR,
safe worker draining, actual import paths or authenticated runtime acceptance.

Bridge structural receipt: Compose 5.5.1 rendered the bridge and extracted the
inert extension using the documented standard-library conversion. A complete
synthetic owner fixture passed normal `config --format json` in default and
opt-in modes: Web has two rules, Python has none by default and six opted-in
`sync+restart` rules in total, with initial sync disabled and migration/model
exclusions retained. Image pins, UID, commands, builds, environment, networks and
retained volumes were identical before and after merging. A separate render of
the existing canary overlays plus bridge changed only Web `develop` configuration.
Whitespace checks passed. No runtime invocation, source sync or restart was run.

Follow-up source inspection used local revision
`7dfebdd51e49f884da4524491344e627f9dc6df6`: the API and Graph canary Dockerfiles,
their Python manifests, `src/api/langboard/CLI.py:33`,
`src/api/langboard/commands/DbUpgradeCommand.py:38`, Graph CLI source path setup,
and existing UI Watch target configuration. Actual deployed pins/import paths
remain an operator verification gate. `docker compose watch --help` confirmed
the native `--no-up` invocation locally; server support must also be checked.

## Native build assumptions

| Source | Native input and output | Watch consequence |
| --- | --- | --- |
| API | Root `langboard/`, `pyproject.toml`, `alembic.ini`, `scripts/`; `Dockerfile.local` installs into `/opt/venv` | Rebuild API and worker from the same source and shared wheel; preserve their different owner commands |
| Shared Python | Root `langboard_shared/`; Hatch wheel version `0.2.0`, required as `langboard-shared==0.2.0` by inspected API and Graph | Build a fresh wheel first; completed wheel changes rebuild API, worker and Graph |
| Graph | Root `langboard_graph/`, own `pyproject.toml`; `Dockerfile.local` installs into `/opt/venv` | Rebuild Graph with the completed shared wheel |
| Core TypeScript | Rollup builds explicit client/server exports under `dist`; package version `0.2.0` | Build core, then build Web against the linked package; raw core changes alone do not update the Web runtime |
| Web UI | Private parent Yarn workspace links `core-ts` and `web-ui`; `Dockerfile.local` copies existing `dist` into Caddy `/srv`, port 80 | Build core and Web first; completed Web `dist`, Caddyfile or entrypoint changes rebuild Web |
| Socket | Root `mix.exs`, `mix.lock`, `lib`, `config`, `rel`, `priv` and native storage probe; native Dockerfile compiles a release | Rebuild; syncing Elixir sources into the release does not compile them |
| Deploy | Definitions and local builder; not application code | Review and render configuration manually; no automatic restart on topology or secret changes |

The upstream native local builder also requires **MCP**, an eighth repository,
for its full local stack. MCP is outside this seven-source assignment. Do not run
that full topology as a substitute for the existing deployment, create a second
scheduler or infer that the existing MCP responsibility has migrated.

## Artifact preparation

Use the native upstream build order, without introducing another source tree or
rewriting package manifests into monorepo paths. In the private parent, upstream
Web expects a `package.json` with `private: true` and `workspaces` including
`core-ts` and `web-ui`. Preserve the reviewed parent Yarn lock and existing
protected registry configuration. A frozen install must succeed; an invented or
missing workspace lock is a blocker, not a reason to silently resolve new versions.

Before starting Watch:

1. In `shared-py`, run `uv build --wheel --out-dir <fresh-empty-private-directory>`.
   Verify the wheel name, package version, source revision and SHA-256. Publish
   that completed directory as `deploy/local/shared-wheels`, keeping exactly the
   intended wheel and retaining its provenance outside source control. Do not
   leave multiple same-version wheel candidates. API and Graph native local
   Dockerfiles consume it through the `shared_wheels` BuildKit context.
2. From the private parent, run `yarn install --frozen-lockfile --non-interactive`;
   then `yarn build` in `core-ts`, followed by `yarn build` in `web-ui`. Require the
   owning artifact/export and type checks; record both source revisions and the
   resulting Web artifact digest. Keep Watch stopped during artifact generation
   so partial `dist` output cannot be deployed.
3. Stop Watch before repeating either artifact pipeline. Replace only completed
   artifacts, rebuild the affected images, then restart Watch after the shared
   source set is coherent. Watch is not a transaction coordinator across API,
   worker and Graph. If schema or protocol compatibility changes, stop writers
   and perform the separately reviewed transition before any recreation.

These commands are preparation instructions. They were not executed by this
documentation task; dependencies were not installed and no repositories cloned.

## One runtime owner and build-only review

The overlay `docker/multi-repo-development.compose.yaml` reuses service keys
`api`, `worker`, `graph`, `socket`, and the current Dokploy UI key `web`. It changes
only image/build/Watch definitions. It adds no runtime secrets, source binds,
networks, volumes, environment values, schedulers, ingress or Compose project.
Append it to the exact retained owner model. Do not append upstream
`compose.services.yaml`, `compose.local.yaml` or `compose.web-ui.yaml`: they would
introduce different runtime assumptions and service names.

Build and Watch collections use native `!override` to remove old remote contexts,
build stages, build args and Vite sync rules. Require Compose **2.24.4 or later**
and a Docker builder supporting named contexts. Parsing was checked with Compose
5.5.1; the target server version remains a gate. Resolve the absolute
`MULTI_REPO_ROOT` on the machine running Compose Watch, with all checkouts and
completed artifacts available there. A remote Git build context alone cannot
provide those local Watch paths.

Using the existing owner's protected environment and complete original `-f`
arguments, append the absolute overlay path and first run `config --quiet`.
Do not save or publish a fully rendered real runtime model: it can contain secrets.
Then use the same exact arguments with `build api worker graph socket web` for
build-only validation. Neither command authorizes service recreation.

After all runtime gates below pass and the curator performs the coordinated
transition, the same owner may use `up --watch api worker graph socket web` or
`watch api worker graph socket web` for already prepared services. Keep one Watch
process per owner. Native `rebuild` recreates services automatically; do not start
Watch before the transition is approved and safe for writers/consumers.

## Sync, restart and rebuild boundaries

For the existing monorepo Vite development image only, the unchanged
`docker/ui-canary-development.compose.yaml` synchronizes UI `src` to
`/work/src/ui/src`, and synchronizes plus restarts for `vite.config.ts`. That image
contains Node/Vite and writable source targets. Dependency manifests, locks,
packed core changes and Dockerfile changes need a rebuild.

The split Web image contains Caddy and a static build, so this overlay uses
rebuild for completed assets and server configuration. It cannot synchronize raw
TSX and obtain HMR. Split Python images contain installed packages rather than the
canary's editable `/app/src` tree; use rebuild for source and dependency changes.
Do not copy Python source to `/app/src` and infer that `/opt/venv` imports it.
Socket requires compilation. There are deliberately no `sync` or `sync+restart`
rules in the split overlay until a separately owned native development image
establishes writable import targets and reload behavior.

## Compatibility trap and adoption gates

Trap version: 1. Investigated 2026-11-03 against the immutable revisions below.
Trigger: appending split builds to the retained monorepo canary and immediately
running Watch. Impact: Caddy may inherit a Node healthcheck/port-8080 ingress;
Python may inherit `/app/.venv` commands or monorepo paths; source sync can appear
successful while importing stale installed code; concurrent recreation can mix
incompatible writers. Mitigation: build-only candidate and explicit runtime review.

Ready: concrete sibling layout; native Dockerfile inputs; complete source map;
generated artifact edges; Compose merge parsing; no added runtime mounts/secrets;
current canary files preserved. Unverified gates before runtime adoption:

- Inspect the complete retained owner model and compare service identities,
  commands, entrypoints, user IDs, storage permissions, environment file paths,
  networks, ingress and healthchecks. The overlay intentionally leaves them
  inherited; **they are not presumed compatible**. Preserve each protected secret
  in its existing owner location and retained storage identity.
- Reconcile Caddy port 80 with the current Vite port 8080 and Node healthcheck.
  Review Caddy entrypoint/runtime public configuration against existing canary
  environment names before changing the owner configuration. Confirm no duplicate
  Web service or ingress owner is introduced.
- Confirm API/worker/Graph executable paths and the selected image's actual
  imports, optional document dependencies, scheduler behavior and configuration.
  The native API/Graph local Dockerfiles use pip and do not consume the monorepo
  `uv.lock`; shared version equality alone does not prove dependency equivalence.
- Reconcile Socket native release probes, runtime identity, editor-sync storage,
  realtime contract, Graph/API authorization and task-stream behavior. Do not
  replace or reinitialize a retained editor volume to satisfy a native layout.
- Pass owning API/shared/Graph tests, packed core client/server export checks,
  Web type/build checks, Socket compile/runtime tests and isolated image startup.
  Validate schema compatibility and writer ordering before migration/recreation.
- Require authenticated board/card/editor checks, two-account authorization and
  realtime behavior, worker delivery and Graph resume/checkpoint tests on the
  actual coordinated image set. Build success and healthy containers alone do
  not satisfy these acceptance gates. Record image digests and source/artifact
  provenance. New pins make prior runtime evidence historical.

Rollback retains the original owner model, images, configuration and data. If a
schema change cannot reverse safely, image rollback alone is insufficient; use
the separately approved backup/restore or forward recovery plan. This task did
not run a migration, cutover, rollback or live service mutation.

## Immutable source evidence and checks

- [Deploy local builder and sibling assumptions](https://github.com/Langboard/deploy/blob/f15feda1ac4e7c7f57ed999f9dd0f09f08a3ad10/scripts/build_local_images.py)
  and [legacy continuity](https://github.com/Langboard/deploy/blob/f15feda1ac4e7c7f57ed999f9dd0f09f08a3ad10/LEGACY_STACK_CONTINUITY.md).
- [API native local build](https://github.com/Langboard/api/blob/9c49c3c17b3ac567de780c1f38804fb434240e1a/Dockerfile.local),
  [Graph native local build](https://github.com/Langboard/graph/blob/b6ad7ea761f111553a821851d1cb80dd89358527/Dockerfile.local),
  [shared packaging](https://github.com/Langboard/shared-py/blob/c3cb30e65f447e8d6ebe3ad5fad1fb252a6d365a/pyproject.toml).
- [Web native local build](https://github.com/Langboard/web-ui/blob/f74415d037d7426b26b7d533aec10712254e96ec/Dockerfile.local),
  [core package exports](https://github.com/Langboard/core-ts/blob/f9c681cc950f59a6ec44b4d4278f83b938414629/package.json),
  [Socket release build](https://github.com/Langboard/socket/blob/afac2aaca3b22d0c1d1304aabaff39284f67937e/Dockerfile).
- Local baseline `Langboard/langboard` revision
  `5bd7bcd5c2042a0390af979510fe3806bbcc00db`: `docker/ui-production.Dockerfile`,
  `docker/ui-canary-development.compose.yaml`, `docker/api-canary-development.Dockerfile`,
  `docker/graph-canary-development.Dockerfile`, root/Graph Python manifests and UI/core
  package manifests were inspected. This is source evidence, not a new deployment receipt.
- [Docker Develop specification](https://docs.docker.com/reference/compose-file/develop/)
  and [Compose merge override semantics](https://docs.docker.com/reference/compose-file/merge/#replace-value)
  define rebuild/recreation and collection replacement. Official capabilities were
  checked on the investigation date; upstream documentation may subsequently change.

Structural verification uses fake absolute source paths, without starting Docker
services or reading runtime secrets. With `MULTI_REPO_ROOT` set, Compose rendered
the existing UI and Python canary overlays plus this overlay using
`config --no-consistency --format json`. Assertions confirmed exactly the five
expected service keys, native contexts, removal of inherited targets/build args,
and rebuild-only Watch actions. This deliberately incomplete fixture does not
establish complete retained-owner validity. A second full synthetic owner fixture
passed normal `config --format json`,
preserving all five commands, environment mappings, network identities and retained
volume sources while replacing old build/Watch collections. The JSON manifest
parses and whitespace checks pass. A full documentation-site build was not run;
this standalone Markdown file is outside the existing site content tree. No ADR
approval status was changed.
