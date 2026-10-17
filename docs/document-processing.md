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
