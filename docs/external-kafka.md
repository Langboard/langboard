# Native external Kafka transport

The Python publisher, operator scripts, Python integration probes and Phoenix
consumer, dead-letter producer and lag inspection use the same environment
contract. Bundled installations retain `PLAINTEXT` and their current bootstrap
addresses when no external override is selected.

| Variable | Contract |
| --- | --- |
| `BROADCAST_URLS` | Comma-separated `hostname:port` bootstrap addresses; use broker DNS names matching certificates. Advertised broker addresses must also be reachable and certified. |
| `BROADCAST_KAFKA_SECURITY_PROTOCOL` | `PLAINTEXT` (default), `SSL`, `SASL_PLAINTEXT`, or `SASL_SSL`; exact uppercase values. |
| `BROADCAST_KAFKA_SSL_CAFILE` | Optional PEM CA bundle inside the container; empty uses native operating-system trust. A supplied bundle replaces default roots. |
| `BROADCAST_KAFKA_SSL_CERTFILE` / `BROADCAST_KAFKA_SSL_KEYFILE` | Optional PEM client certificate chain and unencrypted private key; configure both together for mutual TLS. |
| `BROADCAST_KAFKA_SASL_MECHANISM` | Required for SASL: `PLAIN`, `SCRAM-SHA-256`, or `SCRAM-SHA-512`. |
| `BROADCAST_KAFKA_SASL_USERNAME` / `BROADCAST_KAFKA_SASL_PASSWORD` | Both required for SASL; inject through protected runtime secrets. |
| `BROADCAST_KAFKA_CERTS_DIR` | Compose host directory mounted read-only at `/run/langboard/kafka`. This host setting is separate from the in-container file paths above. |

TLS always verifies the broker certificate chain and hostname. There is no
verification-disable option. `SASL_PLAINTEXT` authenticates without encrypting the
connection; select `SASL_SSL` for encrypted external transport. TLS files with a
plaintext protocol, SASL credentials without a SASL protocol, unsupported
mechanisms, partial credentials and partial certificate/key pairs fail closed.
Python validates when a Kafka client is constructed; Phoenix validates at startup
when Kafka ingress is enabled. Disabled Phoenix Kafka does not read inactive
certificate paths or require inactive credentials.

Python uses kafka-python's native SSLContext and SASL settings. Phoenix supplies
explicit `verify_peer`, trusted roots and HTTPS hostname matching to brod/kpro;
locked kpro supplies each discovered broker hostname as SNI. Never replace these
options with `ssl: true`: the locked kpro version interprets that as
`verify_none`. BroadwayKafka's supported native SASL callback obtains environment
credentials and delegates all PLAIN/SCRAM authentication to `kpro_sasl.auth/7`.
Stored Phoenix client options contain only the mechanism and callback module;
returned authentication errors are generic. Configuration errors do not echo
input values. Python attaches standard-library filters to the locked native
`kafka.conn` and `kafka.protocol.parser` log emitters: authentication wire records
and identity messages omit their contents, and credential values are redacted
from connection diagnostics and exception logs. Other connection diagnostics
remain available. Do not publish protected environment files, expanded Compose
configuration, process environments or debug network traces as evidence.

## Select external Kafka in the development Compose stack

Use Docker Compose **2.24.4 or newer** for the native `!reset` merge tags. Keep the
normal existing Compose selection and append the Kafka override **last**. From
the repository root, after supplying the same protected `.env`/runtime inputs as
the ordinary installation:

```sh
# Values shown are placeholders, not live credentials.
export BROADCAST_URLS=broker.example:9093
export BROADCAST_KAFKA_SECURITY_PROTOCOL=SASL_SSL
export BROADCAST_KAFKA_SASL_MECHANISM=SCRAM-SHA-256
export BROADCAST_KAFKA_SSL_CAFILE=/run/langboard/kafka/ca.pem
export BROADCAST_KAFKA_CERTS_DIR=/absolute/protected/kafka-certs
# Inject BROADCAST_KAFKA_SASL_USERNAME/PASSWORD through the secret owner.
# Generate ordinary runtime env files with the installation's existing step.
# Append this override to the ordinary selection, preserving other selected files.
docker compose $(bash scripts/utils/get-compose-args.sh) \
  -f docker/docker-compose.kafka-external.yaml config --quiet
docker compose $(bash scripts/utils/get-compose-args.sh) \
  -f docker/docker-compose.kafka-external.yaml up -d
```

The override excludes `kafka0`, `kafka1`, `kafka2` and their volume-permission
initializer through an inactive `bundled-kafka` profile and removes their API,
worker, Graph and Phoenix dependencies. Do not enable that profile in external mode.
It forwards the same external transport to all four consumers. Existing Kafka
volumes remain available for an explicit rollback. Selecting external transport
does not stop brokers already running in an earlier deployment: drain ownership
and retire those containers through the existing deployment change procedure.

The standalone `docker-compose.prebuilt.yaml` already contains no bundled Kafka.
Do not combine it with the development override. Put the same transport settings
in each protected API/worker/Graph runtime `env_file` that uses Kafka, and supply
`BROADCAST_KAFKA_CERTS_DIR` to Compose. The API and worker override inherited
volume lists, so each explicitly retains the read-only certificate mount. Graph
inherits the same mount. Configure the compatible external Phoenix deployment
with the identical transport and its own read-only mount at the configured
paths; this application Compose file does not deploy that external runtime.
Operator scripts executed on the host need host certificate paths, or must run
inside the corresponding runtime container with its mounted paths. Restrict
private-key permissions to each runtime's configured UID/GID.

## Cutover and proof boundaries

Transport selection does not create topics/groups, reset offsets, move data,
change event namespaces, alter message-size limits, weaken delivery
acknowledgements, or disable producer idempotence. Preserve instance-specific
topics and consumer groups. Native Kafka ACLs remain the broker owner's
responsibility; prefixes alone do not provide authorization isolation. Retain the
existing `bootstrap-phoenix-kafka-group.py` protections and
`check-phoenix-cutover.py` group/retention checks before transferring ownership.

Focused tests validate native option names, certificate trust loading, rejection
of inconsistent settings, generic error handling, propagation and Compose service
selection. They do not prove an external broker connection, expiry/revocation,
ACL isolation, live reconnect, deployed images, or authenticated application E2E.
Run the existing isolated-topic integration probes against an explicitly
provisioned broker to obtain those receipts, including wrong-hostname and
untrusted/expired-certificate failures. No live broker was contacted or modified
for this source change.
