# Optional document processing

The API stores attachments and queues supported documents. Conversion runs in the
broker worker, whose image must install `document-processing`. The API itself
intentionally omits Docling; API-side package inspection must not disable a
separately provisioned worker. The repository compose configuration selects the
`with-document-processing` image target for that worker.

```sh
uv sync --locked --no-dev --extra document-processing
docker build --target with-document-processing -t langboard-document-worker .
```

A worker without Docling records a clear failed document-indexing state before
downloading the attachment or starting a converter. The attachment remains
stored and accessible. Installing the package does not silently retry prior
failures. A deployment without any worker cannot consume queued tasks; worker
availability and queue monitoring remain deployment responsibilities.

A loaded package is only a prerequisite. Conversion can still fail because of
invalid documents, parser dependencies, size limits, timeouts, or unavailable
resources. Existing metadata failure handling and temporary-file cleanup apply.
No company-specific runtime, identity service, or cloud storage is required by
this feature.

## Remote document vision

Docling remains the conversion engine and owns PDF rendering, page images,
Office/Web parsing, DoclingDocument construction, and Markdown export. PDF and
image inputs use its official `VlmPipeline` with `ApiVlmOptions`; only vision
inference runs at the configured OpenAI-compatible provider. Office/Web inputs
continue to use Docling's native format backends without a vision request.

In Settings → Internal AI, create a **Document Vision · document-vision** binding,
choose an OpenAI-compatible provider, set `base_url`, `model_name`, and its API key,
and select it as the default for that type. This global document binding is not
assigned to project chat bot lists. A model such as `glm-5.3-flash` is deployment
configuration, not a built-in vendor dependency. The endpoint must support images
and Chat Completions; text-only models cannot convert PDF/image pages.

Approve the exact provider base URL in `MODEL_PROVIDER_ALLOWED_BASE_URLS` on the
worker. This explicit deployment boundary also allows local Ollama, LM Studio,
or vLLM endpoints. The endpoint receives document images, so use only trusted
providers. The worker reads the binding from the existing internal AI store;
credentials are not passed in subprocess arguments. Missing bindings fail clearly
instead of falling back to downloading local inference weights. Partial/failed
conversion is not recorded as a successful document index.

The worker resolves the current Document Vision binding from the primary database
before downloading the source and again before publishing converted content. A
deleted binding, a different binding type, or a changed provider base URL rejects
the queued result. Upload-time model and keyword settings remain fixed; credentials
are read from the current binding. These checkpoints do not cancel requests already
sent to the provider or hold a database lock throughout conversion. Worker failures
publish a fixed troubleshooting message instead of provider/storage exception text
that could contain credentials or source content.

The `document-processing` extra selects `docling-slim`'s conversion, PDF, Office,
and Web extras. It excludes `standard`, local model/OCR extras, Torch, torchvision,
CUDA/NVIDIA packages, and Docling IBM model weights. Transformers currently
provides import-time types required by the official VLM module; no local model
is loaded. CPU/RAM are still needed for rendering and parsing. Rebuild the worker
image to remove packages already installed by the old `standard` dependency;
applying source files alone does not shrink an existing container.

## Korean/CJK font fallback

The document worker image installs `fonts-noto-cjk` and `fontconfig`, refreshes
its font cache, and verifies a Korean font match during the build. This supplies
Korean, Japanese, and Chinese fallback glyphs when a PDF does not embed its
original font. It is scoped to the document worker, not every API image.
Embedded source fonts remain authoritative; installing fallback fonts cannot
repair already corrupted text mappings or glyphs saved into the source file.
Validate the page image sent to the model as well as the returned transcription.

### Deleted board fencing

Embedding tasks reject a deleted board before resolving providers. Publication
locks the current board before the card and attachment and refuses a deleted or
mismatched board. If deletion occurs during inference, publication fails and the
task removes its staged vectors; previously committed source history is retained.

### Optional converter upload regression

The optional-converter integration test runs with Docling genuinely absent. It
uses native JWT authentication, the HTTP upload endpoint, SQLite repositories,
and local file storage. An authenticated upload returns 201, preserves the file
and attachment, and emits one captured indexing task after commit. The native
worker records a failed conversion with installation guidance before downloading
the source; the attachment remains available. An unauthenticated request is 401.

The test substitutes card visibility and captures the Celery publisher. It does
not establish board ACL correctness, a live broker delivery, or deployed worker
readiness. Its PostgreSQL variant requires an available test database.

### Disabled retrieval editor

The retrieval form rejects submit events while disabled, before collecting its
fields or calling `onSave`. This also covers programmatic submit events, which
can bypass a disabled fieldset's buttons. The parent internal-bot editor and
server still apply their own current permission checks. The UI regression uses
a character splitter because disabled fields do not participate in FormData;
relying on separator validation to stop a disabled submission would leave that
mode unguarded. This guard does not establish deployed authorization behavior.

### Text-only SQLite embedding and explicit splitter reindex

The official `langgraph-checkpoint-sqlite` store uses `text_fields`, not `fields`,
to select indexed properties. Configure `text_fields: ["text"]` so only each
split chunk is embedded. The wrong option silently selects the entire serialized
record, adding source identifiers and metadata to inference input and diluting
the semantic content. Source metadata remains stored for filtering and readback.

The worker regression runs the actual snapshot resolver, LangChain splitter,
SQLite vector store and generation replacement. It indexes multilingual text
with a 128-character chunk limit, explicitly reindexes with a 64-character limit
under the same model fingerprint, and reopens the store to verify smaller chunks
and removal of the prior generation after publication. Later global model and
splitter edits do not change a queued request; credential rotation is retained.
Captured embedding input must equal persisted chunk text without source metadata.
The original transcription remains unchanged and duplicate delivery does not
repeat inference. Existing attachments are not automatically reindexed.

Inference is a recording test implementation and the metadata publication fence
is a test double. This establishes the local worker/splitter/store contract, not
live provider inference, PostgreSQL publication, broker delivery or deployed UI
acceptance. Previously indexed records require the user's explicit reindex action
to receive text-only vectors.

### Preserve readable vectors during reindex

An embedding pointer carries its own non-secret configuration snapshot in
`embedding.config`. The document's `embedding_config` describes the requested
next generation. Queuing explicit reindex preserves the previous pointer and
its settings; a successful worker publishes the new pointer and settings
together. Failure retains the prior pair. For older metadata without a pointer
snapshot, the explicit request captures the previous document configuration
before replacing it, and reads retain the legacy fallback.

Native document vector search resolves the pointer's settings, not the pending
request's new model or dimensions. It still rechecks current read permission,
attachment lifecycle, source generation, pointer/configuration equality,
provider endpoint and global retrieval enablement after querying. Retaining
old vectors never bypasses revocation or enables retrieval when switched off.
The local regression searches persisted vectors while the request is pending
or failed and rejects both flows when read permission is revoked during search.
Inference and ACL services are test doubles; PostgreSQL publication and live
provider acceptance remain separate gates.

### Card-level embedding queue indicator

The existing card document progress area includes pending embedding requests,
even when transcription has already completed. It shows the localized embedding
queue label instead of suggesting that an indexed transcription has no remaining
work. Attachment detail retains its separate transcription and embedding labels.
Completed or failed embeddings do not keep the card's active progress indicator
visible. This uses existing metadata subscriptions and translation keys without
adding polling, scanning attachments or triggering processing.

### Board-scoped transcript results in the command palette

The command palette queries the existing authorized board card-context API after
300 ms of idle typing, only while open and with 2–1000 query characters. It
displays matching cards, their workflow column, and the bounded attachment
filename/snippet projection. A server result replaces the same board's local
card entry, and selecting it opens the original card. Results from the previous
query are hidden immediately; superseded requests carry a cancellation signal.
Closing the palette disables reads. Search does not enqueue processing or call
an embedding provider, LLM or LangGraph. The existing PostgreSQL search includes
transcribed text and extracted multilingual keywords without embeddings.

The physical browser fixture exercises a transcript-only match, source display,
an out-of-order response and navigation, alongside the existing mobile, focus
and four-language command checks. It is not deployed acceptance. Optional
vector result composition, precise source-page navigation and live multilingual
retrieval quality remain separate requirements.

### Current workflow source validation for keyword search

Both board card-context search and transcript snippet projection validate that a
card's workflow column belongs to the same board. Removed columns are excluded
before result limits and excerpt ranking. An inconsistent or stale card/column
relationship must not expose another board's column name, consume the result
budget, or return a removed workflow's transcript. These checks use the current
primary SQL source and preserve the existing card visibility policy, literal
matching, two-source/500-character excerpt bounds and optional embeddings.
They neither repair operating records nor create/rebuild an external index.

The SQLite/PostgreSQL regression creates inconsistent and removed workflow
sources beside a readable current card. The unmodified implementation fails all
four cases; the fixed query returns the current card even with a limit of one
and excludes invalid sources from transcript excerpts. This is source regression
evidence, not Elasticsearch integration or canary acceptance.

### Page-scoped non-destructive document reads

`read_card_document` accepts an optional positive `page`. It loads only the
current attachment's generation-fenced structural artifact and uses official
Docling `export_to_markdown(page_no=page, traverse_pictures=True)` serialization.
Offsets and the 128–8000 character excerpt limit apply within that page. The
response includes the source page and generation; subsequent reads must retain
both the page and `expected_generation`. Without `page`, existing Markdown
offset behavior is preserved. Missing structure or a missing source page returns
an unavailable error rather than fabricated provenance or whole-document text.
Current card/attachment authority, generation and content hash are checked again
after serialization. No VLM, embedding, conversion or reindex task is started.

The focused SQLite/PostgreSQL run exercised 44 tests, including actual Docling
multilingual page serialization, page-local continuation, invalid/missing pages,
and permission, deletion, generation and hash changes during serialization.
Current primary binding checks for vector reads also passed. This supplies the
agent's page follow-up read; optional board-wide vector composition and UI
page-link navigation remain separate incomplete requirements.
# Current source read regression evidence

Attachment byte reads revalidate card visibility and the attachment on the primary
database after storage IO. The native service regression makes any replica read
fail, then commits deletion, file replacement or membership withdrawal while IO
is in progress. The attachment MCP suite passes 18 cases across SQLite and a
disposable PostgreSQL database, including unchanged-source success. This does
not prove deployed provider processing or multilingual transcription quality.
Keyword language labels reuse the existing locale resources while retaining the
stored `ko`, `en`, `ja` and `zh` values. Standard-library `re` is imported once;
optional LangChain/Qdrant integrations retain deferred loading.


## Shared external vector collections

Qdrant retrieval settings include `collection_prefix` (default
`langboard_documents` for existing installations). Set a unique prefix per
installation when sharing an endpoint. Names consist of 1–63 ASCII letters,
digits, underscore or hyphen, beginning with a letter or digit. The official
LangChain/Qdrant integration uses `<prefix>_<embedding-fingerprint>`; no custom
vector protocol or database adapter is introduced.

The upload snapshot freezes the prefix. Published source pointers record it;
MCP retrieval checks the recorded prefix and uses that snapshot. Cleanup uses
the pointer's original prefix even if current settings changed. Pointers predating
this field retain `langboard_documents`. Explicit reindexing to a different prefix
must publish the new source pointer before cleanup of the previous generation.
Settings changes trigger no background scan, migration or destructive purge.

Prefixes prevent accidental name collisions; they do not authorize access. Use
Qdrant server credentials restricted to the intended collections, and separate
security boundaries where stronger tenant isolation is required. Endpoint
allowlisting, current board/card ACLs and source-generation fences still apply.
Local integration tests exercise the official Qdrant client's persistent local
mode, independent searches and deletion across namespaces, and legacy cleanup.
They do not prove a deployed shared server's authentication/TLS or canary E2E.

### Private certificate authorities

Use the official client's native HTTPX trust settings for REST-based Qdrant
connections. The standard Docker environment generator forwards `SSL_CERT_FILE`
and `SSL_CERT_DIR` to API, worker and graph environments. Set a container-local CA
bundle path or an OpenSSL hashed CA directory, mounted read-only by the operator.
Empty values retain the client's default trust. These are process-wide HTTPX
settings, not per-board fields, credentials or a certificate-verification bypass.

The custom bundle replaces default trust. Include the public CAs required by other
HTTPS providers in the same process. Keep `KEY_PROVIDER_VAULT_CA_CERT` for the
existing vault-specific client; not every network library uses HTTPX settings.
No CA file, private key or company endpoint belongs in the public source tree.
See [HTTPX environment variables](https://www.python-httpx.org/environment_variables/)
and the pinned [Qdrant 1.19.1 REST client](https://github.com/qdrant/qdrant-client/blob/v1.19.1/qdrant_client/qdrant_remote.py).

Regression tests use the real official Qdrant client against a disposable HTTPS
wire fixture: a trusted CA permits a read-only collection-existence request and an
untrusted certificate rejects it before HTTP handling. This exercises certificate
validation and the common connection factory, not a real Qdrant server, server
authorization, gRPC, persistence or deployment acceptance. A real shared-server
check remains required before promotion.

### Disposable external-server acceptance

An opt-in test exercises the common connection factory against an actual Qdrant
REST server. Provision a disposable HTTPS server with API-key authentication and
permission to create/delete test collections. Supply its URL and secret through
`LANGBOARD_TEST_QDRANT_URL` and `LANGBOARD_TEST_QDRANT_API_KEY`. For a private CA,
set `SSL_CERT_FILE` to the trusted bundle as described above. Run:

```sh
PROJECT_NAME=langboard PYTHONPATH=src/shared/py uv run --no-sync pytest -q \
  src/shared/py/langboard_shared/tasks/docling/DocumentVectorStore_external_test.py
```

The existing `document-retrieval` dependency extra is required. Without explicit
test credentials the test skips; an unsecured server fails the authentication
check. It writes only to random `langboard_acceptance_…` namespaces and deletes
those collections in `finally`. It checks official-client persistence across
connections, multilingual content, similarity/MMR, exact source-generation and
board filters, independent installation namespaces, dimension mismatch rejection
and idempotent generation deletion. Embeddings are deterministic test vectors;
no provider calls or local inference models are used. Do not target production.

Local acceptance on 2026-10-17 passed against official Qdrant 1.19.1, image
`qdrant/qdrant@sha256:12364fe851b9f17356fc88189fc06d1b521262e04659ec7345975b00c9246a10`,
with a temporary private CA and the pinned 1.19.1 Python client. This is not
server-side collection RBAC, browser/API/worker acceptance, gRPC verification or
a performance benchmark. The opt-in test is separate from the simulated-wire
CA regression above and does not start services during ordinary unit tests.

### Recorded external vector cleanup authorization

Qdrant cleanup resolves its recorded binding from the primary database before
connecting. Deleted or repurposed bindings and replaced endpoints reject cleanup
without contacting either the old or replacement server. Credentials are refreshed
from that binding; dimensions, collection prefix and source pointer remain those
of the recorded generation. Cleanup opens existing collections only and does not
recreate a missing collection or infer a replacement endpoint. This checkpoint
does not cancel a deletion already sent to Qdrant. Broker delivery is recovered through the transactional cleanup outbox described
below. The connection check itself does not authorize a replacement endpoint.

### Transactional vector cleanup recovery

Migration `a31f9e7b402c` adds `document_vector_cleanup`. Replacing a committed
embedding pointer or deleting its attachment document records the previous pointer
in the same primary database transaction. Rollback removes both changes. An
after-commit broker notification is only an optimization: a failed notification
leaves the request in the database. The outbox contains identifiers and recorded
storage coordinates, never model/vector API keys or document content. It has no
attachment foreign key, so deleting the attachment cannot cascade away the request.

The existing API cron runner registers `run:document-cleanup:recover` every minute
via `scripts/run_document_cleanup_recovery.sh`. Each run reads at most 100 due
requests using the due-time index and stops dispatching on broker failure. It does
not scan attachments or trigger conversion, embedding, or reindexing. External
schedulers may invoke the same command when the bundled scheduler is disabled.

The worker atomically claims each request for 120 seconds, closes the transaction
before filesystem/network deletion, and acknowledges only its own claim token.
Duplicate delivery while leased does nothing. A lost worker becomes recoverable
after lease expiry. Failures retain the exact pointer with a fixed error code and
exponential backoff, capped at one hour. Successful idempotent deletion removes the
outbox row. No model is invoked. Missing/revoked original bindings retain failed
requests for operator repair rather than guessing another endpoint.

Apply the API migration, roll workers with the new cleanup task registered, then
roll API images that produce cleanup requests. Downgrade refuses
to discard pending cleanup rows; do not empty them merely to force rollback. The
legacy queued cleanup task remains registered for messages accepted by older
workers. This outbox covers pointer replacement, attachment-document deletion,
explicit card/board deletion, and unpublished generations whose immediate
cleanup fails. Deployed broker/worker acceptance requires separate verification.

### Opt-in real broker cleanup acceptance

`src/api/tests/card_workspace/test_document_cleanup_broker.py` uses a dedicated
Redis endpoint, a disposable PostgreSQL schema and a separate native Celery worker
process. Enable it only with `LANGBOARD_TEST_CLEANUP_BROKER_URL` and
`LANGBOARD_FILE_TEST_DATABASE_URL` pointing to test services, using a Python runtime
with the existing document-retrieval extra. Run:

```sh
pytest -q -s src/api/tests/card_workspace/test_document_cleanup_broker.py
```

The test confirms a real connection failure leaves the committed request pending,
checks task registration, recovers through Redis/Celery into PostgreSQL and deletes
the actual recorded SQLite vector generation while retaining the new one. A
completed event is replayed through the queue and both task completions are
observed. SQL, broker dispatch and vector deletion are not mocked. Embeddings are
deterministic fixtures; the worker explicitly registers the cleanup task rather
than loading every unrelated application task. This is not full worker bootstrap,
remote Qdrant or deployed canary acceptance.

The test uses random queue/key prefixes, schema and vector filename. It removes
only that namespace, schema and file, and shuts down its worker. The operator owns
the test Redis/PostgreSQL lifecycle. Without the explicit URLs it skips. A local
run used the official Redis 7.4-alpine image at digest
`sha256:858f009f9709ce576febc734aa78b8f6d624b82571f9ddb6bda4377c833b3499`,
bound to loopback with 0.5 CPU/96MiB limits. Do not use production endpoints.

The Phoenix deployment preflight now requires
`langboard.document_vector_cleanup` on every responding worker, alongside existing
required tasks, so a mixed/stale worker deployment cannot silently pass that gate.

### Cleanup of existing namespace generations

The transactional cleanup request preserves `chunk_count` as well as the recorded
namespace for generations created by the earlier official LangGraph store path.
Without the count, the deletion worker rejects the request and retains it for
retry indefinitely. Current `chunk_ids` generations retain their existing behavior.
The SQLite integration test covers both pointer formats through the committed
outbox and actual store deletion, checking that a newer generation and its
vectors survive and a repeated cleanup is harmless. No provider inference,
credential storage, source document mutation or database conversion is required.

```sh
PROJECT_NAME=langboard PYTHONPATH=src/api:src/shared/py python -m pytest \
  src/api/tests/card_workspace/test_document_cleanup_outbox.py -q
```

This is source regression evidence. Existing pending requests that already lost
their count are not repaired automatically, and this change does not scan or
delete production indexes. Deployed acceptance remains separate work.

### Card and board deletion

Explicit card or board deletion records the source's existing embedding pointers
in the same transaction as the source deletion. Rolling back preserves both the
source and the absence of cleanup requests. The service uses the same project,
then card lock order as embedding publication, and reads only the selected
scope's document metadata in batches of 100. A new inference result cannot
publish after its project or card is deleted. Other boards remain untouched;
Wiki reference deletion does not delete the Wiki's resources.

A single card retains immediate after-commit dispatch. Board-wide deletion
records requests without synchronously contacting the broker for every document;
the existing minute-based recovery cron dispatches at most 100 due requests per
run. Worker acknowledgement, retry fencing and recorded external connection
validation retain their existing behavior. No full attachment reconciler, source
file deletion, new scheduler or database migration is introduced.

```sh
PROJECT_NAME=langboard PYTHONPATH=src/api:src/shared/py python -m pytest \
  src/api/tests/card_workspace/test_document_scope_cleanup.py \
  src/api/tests/card_workspace/test_document_cleanup_outbox.py \
  src/api/tests/wiki_linked_cards/test_wiki_linked_resource_card.py -q
```

The scope regression exercises the actual source repositories and transactional
outbox, with unrelated notification/readiness systems stubbed. It checks rollback,
a board of 101 documents crossing the read-batch boundary, bounded later dispatch,
late publication rejection and repeated deletion without new cleanup requests.
PostgreSQL variants require `LANGBOARD_FILE_TEST_DATABASE_URL` pointing to a
dedicated disposable test database. With PostgreSQL 17, this suite passed 46 tests;
two SQLite variants skip because SQLite cannot exercise PostgreSQL row locks.
The concurrent publication cases use two actual connections and
`pg_blocking_pids()` to verify that publication waits for the deletion transaction,
then rejects the deleted source after commit. Both card and board deletion are
covered. A skipped PostgreSQL variant is not concurrency proof. This is a source
transaction regression, not deployed worker/provider or canary acceptance.
The tests do not delete production data.


### Unpublished generation cleanup failures

If a vector store partially accepts staging and then fails, the worker first
attempts to delete the requested chunk IDs. The same cleanup applies when the
authoritative publication fence rejects a staged generation or publication raises
an error. A failed immediate deletion carries the server-owned pointer in a typed,
sanitized error; the worker commits that pointer to the existing cleanup outbox
before publishing the generic embedding failure. The previous searchable pointer
remains unchanged. Broker dispatch failure leaves the committed request available
for the existing bounded recovery cron. No attachment scan, new scheduler or
provider credentials in cleanup records are introduced.

The `test_unpublished_vectors_survive_delete_failure_as_durable_cleanup` regression
in the outbox suite exercises all three paths with real SQLite vector writes and
SQLite/PostgreSQL outbox transactions. After a broker failure it processes the
committed request, verifies deletion of only the staged IDs, retains the previous
generation and replays the completed event harmlessly. Provider and publication
authority are fixtures; this is not external-provider or deployed acceptance.
A process killed before the cleanup record commits, or an unavailable primary DB
preventing that commit, is outside this tested recovery guarantee.

An explicit `False` result from `VectorStore.delete()` also counts as cleanup
failure. The official Qdrant integration returns this value for an acknowledged
operation that has not completed. The existing outbox retains the pointer and
backs off; it removes the request only after a successful retry. Providers that
return `None` on success retain their existing behavior.

`test_negative_vector_delete_ack_preserves_cleanup_until_retry` exercises the
official Qdrant integration with acknowledged/completed response fixtures and real
SQLite/PostgreSQL outbox transactions. The cleanup, vector-store and embedding
regression suites pass 70 tests together. These checks cover SDK response handling
and durable retry, not a live external Qdrant server or deployed acceptance.

### Localized splitter choices

The retrieval editor displays splitter types, length units and separator
placement in English, Korean, Japanese and Chinese through existing locale
resources. Canonical submitted values such as `recursive`, `tokens` and `start`
remain unchanged. Tokenizer identifiers retain their official names. No provider,
vector-store migration or automatic reindex is implied by a caption change.

The existing real-component browser fixture covers four-language captions and
canonical submitted values, disabled submission, Qdrant/SQLite modes and desktop/
mobile layout. The localization workflow runs this focused suite. Local source
verification does not replace authenticated acceptance after UI deployment.


### PostgreSQL retrieval default and preserved legacy indexes

New retrieval settings default to `store: postgres`, while retrieval remains
opt-in (`enabled: false`). The adapter reuses the official
`langgraph-checkpoint-postgres==3.1.3` `PostgresStore` and the same LangChain
vector boundary as SQLite. It uses the existing `MAIN_DATABASE_URL`, including
its TLS query settings; there is no additional application database or ORM.
API/worker image profiles already install the `document-retrieval` extra.

Before enabling this store, the database operator must install pgvector in the
`public` schema. The application fails explicitly if it is missing or installed
elsewhere; it never falls back to SQLite. Its database role needs schema creation
and read/write access to the generated model schemas. Explicit attachment
indexing creates a dimension-bound schema per embedding fingerprint through
native LangGraph migrations. Search and cleanup never create or migrate it.
Stored dimensions must match the configured model (1–16,000).

The current adapter uses exact cosine search (`flat`), avoiding an implicit ANN
index with different dimension limits. This scans the relevant stored vectors;
large workloads should select the existing external Qdrant option and measure
latency before enabling retrieval broadly. Model namespaces and source filters
are not authorization: current board/card access and active-generation checks
remain required at the existing retrieval boundary.

Previously stored settings explicitly retain `store: sqlite`; their indexes,
recorded generation identifiers and credential-free cleanup remain supported.
Saving settings does not migrate files, replace embeddings or scan attachments.
Only a new upload or an explicit attachment indexing request uses its selected
settings. Original transcriptions and PostgreSQL keyword search remain available
without embeddings. Elasticsearch selection is still separate unfinished work.

Real integration tests require `LANGBOARD_VECTOR_TEST_DATABASE_URL` matching `MAIN_DATABASE_URL` in an isolated test process and pointing to
an explicitly disposable PostgreSQL database with pgvector already in `public`.
They skip without that dedicated variable and never use the production database
implicitly. Coverage includes persistence, multilingual content, missing-index
reads without schema creation, source-generation filtering, independent model
dimensions and cleanup without embedding credentials. These tests are source
acceptance, not deployed canary or external-user acceptance.


Source acceptance on 2026-10-18: four real PostgreSQL/pgvector cases passed,
59 existing retrieval/store/embedding cases passed (one existing negative-CA
client compatibility warning), and seven real-component settings browser cases
passed at desktop/mobile widths and four locales. TypeScript, targeted product
ESLint, formatting, locale parity and both lock checks passed. The first explicit
opt-in fixture run attempted to assign a read-only Env property and failed;
the fixture now requires matching isolated process configuration without changing
that property. Failed receipts remain preserved. No canary image was deployed
by this verification.


Root package lock correction: the first PostgreSQL change updated shared and
Graph locks but missed the independently owned root `uv.lock`. A fresh root
`uv lock --check` reproduced that mismatch. The root lock now contains the
Postgres adapter and its required checkpoint/pool dependencies, matching the
already-tested Graph environment. Root, shared and Graph lock freshness checks
and the production `document-retrieval` install dry run pass. Existing app and
full Phoenix validation installs use `--locked` to reject stale metadata before
installation; `--frozen` alone would trust an outdated lock. No validation job,
runner or deployment environment was added.

## Optional Elasticsearch keyword search: implementation boundary

A complete Elasticsearch search provider is not yet selectable in settings.
PostgreSQL remains the keyword-search coverage owner, including transcribed
attachment text without embeddings. The operator preflight and the explicitly
enabled supplemental document read path below are available independently.
Do not expose a working-looking settings option backed only by an unused client
module, or redirect a deployment to another application's cluster/index.

The implementation must preserve these boundaries before it replaces a read path:

- Use the official Python client for endpoint, API-key authentication, certificate
  verification/custom CA, request timeout and bounded bulk operations. Select a
  supported client/server version pair from the operator's actual deployment;
  do not disable TLS verification or choose an old client just to reach a legacy
  service. Optional dependencies must remain absent from the default import path.
- Store connection secrets through the existing SecretRef boundary. Instance-owned
  index names and server-enforced permissions are both required; a name prefix
  alone is not isolation. Never reuse another application's existing index.
- Index Langboard-owned text projections with board/card/attachment provenance,
  content generation and revision. Card text, comments and transcriptions need
  their existing authoritative write owners; attachment inference remains optional.
  Multilingual extracted keywords are searchable attributes, not executable DSL.
- Commit a durable indexing/deletion request with the authoritative source change;
  dispatch only after commit. Retry due requests with a bounded lease. Do not put
  remote network calls in the source transaction, reuse execution-ready semantics
  as indexing state, or create a full-file polling reconciler. Setting a provider
  must not enqueue all existing files or regenerate embeddings.
- Query a bounded candidate set for an explicit board. Treat index IDs, text and
  snippets as untrusted: revalidate current primary DB project/card/column access,
  deletion and source generation before materializing any response. Do not expose
  remote snippets before that check. Stale, unavailable or incomplete indexing
  must retain PostgreSQL search coverage, rather than silently omit new content.
- Switching providers keeps old data and connection references available for
  explicit migration/cleanup and rollback. Source deletion needs durable derived
  index cleanup even if credentials or the current endpoint change. Search and
  connection checks must never perform indexing, inference or schema setup.

Acceptance requires a real supported external cluster: scoped credentials, TLS
and permission failures, two instance/board namespaces, source replacement and
revocation, delivery failure/recovery, deletion and restart persistence. Add the
settings UI only when it can report these actual capabilities and failures.
PostgreSQL behavior must pass with Elasticsearch absent. Until then the portability
card's Elasticsearch item remains incomplete; local SQL or vector-store tests do
not substitute for that acceptance.

Official references: [client connections](https://www.elastic.co/docs/reference/elasticsearch/clients/python/connecting),
[TLS/timeouts](https://www.elastic.co/docs/reference/elasticsearch/clients/python/configuration),
[bounded bulk helpers](https://elasticsearch-py.readthedocs.io/en/stable/api_helpers.html).

### Read-only operator connection preflight

Install the optional client explicitly with `uv sync --locked --extra keyword-search`.
The default API/Graph imports and installation do not require Elasticsearch.
The official client is pinned to 9.5.1; it uses Elasticsearch 9 compatibility.
This command is not a way to repurpose an existing Elasticsearch 7 cluster.

Have the deployment's existing secret delivery resolve its API-key SecretRef into
`LANGBOARD_ELASTICSEARCH_API_KEY`, then run:

```sh
langboard search:check \
  --endpoint https://search.example.invalid:9200 \
  --index langboard-instance-keywords-v1 \
  --ca-certs /run/secrets/search-ca.pem \
  --timeout 5 \
  --require-write
```

The key is never a command-line argument. `--api-key-env` can name another injected
environment variable. No key is persisted by the command. HTTPS certificate
verification stays enabled; absent `--ca-certs`, the official client's trust
defaults apply. The timeout is per request, 1..30 seconds, with automatic retries
disabled. Credentials in URLs, wildcard/multiple/system-index names, aliases and
data streams are rejected.

The command checks current `read`/`view_index_metadata` privileges, resolves exactly
one concrete index and performs a `size=0`, `match_none`, no-source search. Optional
`--require-write` checks `index`/`delete` privileges without writing. It does not
create an index, inspect document contents, change configuration or trigger any
indexing. Cluster-wide `monitor` is not required: when absent, the result explicitly
reports `version_verified=false`/`server_version=null`; it must not be interpreted
as verified server-version inventory. When already allowed, the version is read
and must match the installed client's major version.

The JSON receipt has a nonzero exit status for failures and fixed codes separating
configuration, missing optional dependencies, authentication, permissions, TLS,
timeouts, missing endpoints/indices, incompatible servers and incomplete search.
It excludes API keys, remote error bodies and document contents. A successful
connection check does not prove index ownership/isolation, write delivery,
application search, ACL enforcement or deletion/recovery acceptance.

Local tests exercise real official-client HTTPS against a controlled protocol
fixture: trusted/untrusted CA, hostname mismatch, malformed CA, authentication,
permission and version failures, timeout without retries, concrete-index selection,
zero-document reads, secret-free results, native Commander parsing and a fresh
process with the optional imports blocked. They are not a running Elasticsearch
cluster or a deployed remote-service E2E. The existing card-workspace job installs
the optional extra and runs these checks; no new job or runner is introduced.

### Supplemental document candidates

`DOCUMENT_KEYWORD_ELASTICSEARCH_ENABLED=true` enables an optional read stage in
the existing board-scoped card search (including its native MCP consumer). It uses
`DOCUMENT_KEYWORD_ELASTICSEARCH_ENDPOINT`, `DOCUMENT_KEYWORD_ELASTICSEARCH_INDEX`,
`DOCUMENT_KEYWORD_ELASTICSEARCH_CA_CERTS`, `LANGBOARD_ELASTICSEARCH_API_KEY` and a
strict 1..2-second `DOCUMENT_KEYWORD_ELASTICSEARCH_TIMEOUT` (default 1). Provision
the concrete instance-owned index and run the operator preflight separately. The
default is disabled and imports no Elasticsearch package or extra DB query.

This stage supplements PostgreSQL; it does not replace any PostgreSQL match. Its
purpose is to accept additional full-text matches from an explicitly configured
index while preserving native coverage on disabled, missing, unavailable, stale or
incomplete optional indexing. It adds bounded remote IO/source validation when
enabled and is not a demonstrated search-latency optimization.

The indexed attachment projection contract is:

| Field | Value |
| --- | --- |
| `board_uid`, `card_uid`, `attachment_uid` | Current Langboard short-code identities, stored as exact keyword fields |
| `projection_hash` | Lowercase SHA-256 of the exact UTF-8 `CardAttachment.document_text` string |
| `text` | That exact text projection, indexed for full-text search |

`projection_hash` deliberately differs from the original file's existing
`content_hash`. Do not hash the file, normalize the text or substitute a model ID.
No indexing worker, automatic backfill, provider-selection UI or index creation is
implemented by this read stage. Building and maintaining the index remains part
of the unfinished provider work; enabling this flag does not perform those tasks.

The remote request filters one board, asks for at most 20 identifiers/hashes and
no text/highlights, and has a 500ms server search timeout with no transport retry.
After the request, the service resolves current account/membership/audience again.
A single primary SQL query then filters live authorized cards, attachments and
columns before fetching source text. Only matching card/attachment identities and
an exact current projection hash are accepted. No remote snippet is returned.
Results reuse current filenames and at most two 500-character local excerpts per
card; existing date/workflow filters and the normal 20-card limit remain in force.

For bounded validation, projections over 131,072 characters are excluded from this
supplemental stage; their existing PostgreSQL search is unchanged. A full provider
will need durable projection revisions/hashes to remove that fetch restriction
without unbounded reads. Search never triggers inference, file reads or reindexing.
HTTPS protocol fixtures plus SQLite/PostgreSQL integration cover hidden/private/
foreign/deleted/stale sources, Human UI/MCP/API differences, membership revocation
and source replacement during a request, and native fallback. They do not prove
real Elasticsearch matching, delivery/deletion, index isolation or deployed E2E.

### Deleting an extracted document

`delete_document_by_attachment_uid` clears the selected attachment's derived
`document_text` in the same transaction as metadata/artifact deletion and durable
vector-cleanup recording. It uses the same attachment/card predicate for text and
artifact removal, so a foreign card cannot clear another card's projection.
This also removes leftover search text when the metadata row is already absent.
The original attachment row, filename and file reference are retained by this
extraction-only operation. The separate attachment-delete service still controls
deletion of the attachment itself. Late results from the old extraction generation
remain rejected; failed replacement processing continues to retain the prior text.

Previously, extraction deletion left `document_text` behind. Five new assertions
failed across SQLite/PostgreSQL and real HTTPS candidate reads before correction.
The normal attachment-delete path already filters soft-deleted attachments from
search, so this is not by itself proof of an observed public data leak. The fix
closes the underlying deletion mismatch and makes old external candidates fail
current-source validation. Rollback restores metadata, text and cleanup state
together. The optional-feature API test environment passed 83 related tests; two
SQLite-only locking cases were skipped with their PostgreSQL counterparts passing.
The base API environment also passed the seven focused cases (overlapping scope).
These are source/integration checks, not remote-index deletion or canary evidence.
