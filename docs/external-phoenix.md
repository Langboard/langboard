# Bundled or external Phoenix

Langboard realtime requires the matching Langboard Phoenix application and its
existing authentication, Kafka, editor ownership and recovery contracts. An
arbitrary Phoenix endpoint is not compatible. External mode means one compatible
Langboard runtime per instance; it does not implement shared multi-instance
Phoenix tenancy.

## Deployment selection

`SOCKET_DEPLOYMENT_MODE=bundled` is the default. Empty endpoint values keep the
existing bundled routes. To use a separately deployed compatible runtime:

```dotenv
SOCKET_DEPLOYMENT_MODE=external
SOCKET_URL=wss://realtime.example.invalid
SOCKET_INTERNAL_URL=https://realtime-internal.example.invalid
SOCKET_EDITOR_INTERNAL_URL=https://editor-internal.example.invalid
```

| Setting | Consumer | Bundled empty-value default |
| --- | --- | --- |
| `SOCKET_URL` | Browser realtime transport; HTTP(S) or WS(S) | Local Nginx socket endpoint |
| `SOCKET_INTERNAL_URL` | Shared runtime base URL; HTTP(S) | `http://${PROJECT_NAME}_socket_phoenix:5690` |
| `SOCKET_EDITOR_INTERNAL_URL` | API editor synchronization and document-owner routing; HTTP(S) | `http://${PROJECT_NAME}_server:8010` |

External mode requires all three service base URLs. Missing or incompatible URLs,
credentials in URLs, query parameters and fragments fail configuration selection
before Compose runs. Do not append `/editor-sync`. The environment generator
preserves configured endpoints for API, graph and socket consumers. Each URL must
be reachable from its actual consumer, with a trusted TLS chain. No verification
disable option is introduced.

The standard Compose selector adds `docker-compose.phoenix-external.yaml`, excludes
the bundled Phoenix runtime and collector, removes the server's local runtime
dependency and port 8010 publication, and replaces only the socket Nginx template
with an empty ingress configuration. UI/API routes and their dependencies remain.
`WITH_OTEL=true` does not activate or pull the local collector in external mode.
Remote tracing must be configured in the remote deployment; exclusion of the local
collector is not proof that remote tracing works. Use Docker Compose 2.24.4 or
newer for the override's native `!override` support. The configuration follows
[Compose merge semantics](https://docs.docker.com/reference/compose-file/merge/)
and [service profiles](https://docs.docker.com/compose/how-tos/profiles/).
Do not activate the `bundled-phoenix` profile, wildcard profiles, or explicitly
start the bundled socket service in external mode.

## Ownership, transition and readiness

The deployment operator owns remote DNS/TLS/ingress, origin restrictions, internal
authentication, version compatibility and network reachability. Reuse the existing
ingress/authentication contract. Keep signing keys/audiences, Kafka topics/groups,
callback destinations and editor storage/owner namespaces isolated per instance.
A hostname or namespace prefix alone is not authorization. Provision remote
credentials through deployment secrets, never through public examples or wikis.

Existing startup checks for native feature flags, internal secret, notification
recovery, Kafka offsets and editor restore evidence remain required. External
selection does not waive them. Before activating a replacement owner, explicitly
stop the previous instance's Phoenix owner and confirm no competing consumers or
editor owners remain. Do not start both runtimes against the same instance data.
The override preserves local volumes and does not migrate editor state, secrets,
Kafka offsets or application data. Back up and validate the owner handoff first.
Rollback requires reversing that handoff and verifying the retained editor state;
changing the mode alone does not prove a safe rollback.

After standard startup, `make check_socket_phoenix_ingress` probes both external
internal URLs from the API container. It requires HTTP 204 and the Phoenix runtime
and nonempty version headers; redirects, 401 and incompatible responses fail.
No credentials are added to this readiness probe. Failed readiness returns a
failure without stopping API/UI containers or silently launching a local runtime.
Bundled mode retains its existing host-published ingress check. Readiness headers
do not prove compatible versions, authenticated channel access or successful edits.

Acceptance additionally requires authenticated join, editor save and API callback,
reconnect/resync, access denial across instances and unchanged UI/API behavior.
Exercise unreachable endpoints, invalid CA/auth and version mismatch, then verify
rollback and no duplicate delivery before completing the handoff.

Kafka TLS/SASL settings, certificate mounts and external broker selection are documented in [external-kafka.md](external-kafka.md).
