# External Phoenix routing

Langboard realtime requires the matching Langboard Phoenix application and its
authentication, Kafka, editor ownership and recovery contracts. An arbitrary
Phoenix endpoint is not a compatible replacement.

## Endpoint settings

The standard Docker environment generator preserves these optional values:

| Setting | Consumer | Empty-value default |
| --- | --- | --- |
| `SOCKET_URL` | Browser realtime transport | Local Nginx socket endpoint |
| `SOCKET_INTERNAL_URL` | Shared runtime base URL; fallback when no editor ingress variable is supplied | `http://${PROJECT_NAME}_socket_phoenix:5690` |
| `SOCKET_EDITOR_INTERNAL_URL` | API editor synchronization and document-owner routing | `http://${PROJECT_NAME}_server:8010` |

For a compatible external deployment, set the browser endpoint and the two
server-side ingress URLs explicitly as required by that deployment. For example:

```dotenv
SOCKET_URL=https://realtime.example.invalid
SOCKET_INTERNAL_URL=https://realtime-internal.example.invalid
SOCKET_EDITOR_INTERNAL_URL=https://editor-internal.example.invalid
```

These values are service base URLs, without credentials, query parameters or
`/editor-sync` suffixes. They must be reachable from their respective consumers.
The editor ingress must preserve the existing document-owner route header and
current authentication checks. HTTPS endpoints require certificates trusted by
the consuming runtime; do not disable verification to bypass certificate errors.

This change only preserves configured endpoints during environment generation.
It does **not** disable the bundled Phoenix container, reconfigure the bundled
Nginx upstream, provision remote credentials, establish multi-instance channel
isolation or migrate editor state. Full bundled/external Compose selection and
authenticated deployment acceptance remain separate unfinished work.

## Verification

Run the existing configuration regression using Bash 4 or newer:

```sh
python3 src/socket/test/integration/test-phoenix-config.py "$(command -v bash)"
```

The test executes the real environment generator with disposable files, checking
blank/default endpoints and explicit HTTP/HTTPS endpoints in API, graph and
socket environments. It also retains the existing short-secret rejection and
request-size checks. PowerShell uses the same templates and is exercised when
its executable is installed; source inspection alone is not runtime proof.
No live service or application database is used by this regression.
