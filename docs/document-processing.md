# Optional document processing

Langboard stores attachments independently of document processing. Supported new
uploads are queued for transcription only when a default Document Vision binding
exists and its `document_processing_enabled` setting is enabled (default `true`
within that binding). Configure the binding before enabling automatic processing.
Changing settings or installing dependencies does not process existing attachments
or retry earlier failures. Use an explicit action on each selected attachment.

Conversion runs in the broker worker. Install its optional dependencies and use
the document worker image target:

```sh
uv sync --locked --no-dev --extra document-processing
docker build --target with-document-processing -t langboard-document-worker .
```

The API does not require Docling. A worker without the converter records a failed
processing state and leaves the original attachment available. Operators must
provide a running worker and monitor its queue. Invalid documents, parser
requirements, size limits, timeouts and resource exhaustion can still prevent
conversion after installation. A queued response confirms a request was accepted;
it does not confirm successful transcription or indexing.

## Remote document vision

In **Settings → Internal AI**, create a **Document Vision · document-vision**
binding, select an OpenAI-compatible provider, set `base_url`, `model_name` and its
API key, and select it as the default for that type. This instance binding is
independent of project chat bot lists. PDF and image conversion requires a model
supporting images through Chat Completions. `model` is accepted as a legacy
alternative to `model_name`. Optional `reasoning_effort` and `top_p` are passed
through to compatible providers. Remote page inference uses an 8192-token output
limit, temperature 1, a 120-second request timeout, one concurrent request and
a 2048 image-size setting.

Docling renders PDF/image pages and uses its official remote VLM pipeline for
vision inference. Office/Web formats use native parsers without a vision request.
The worker requires CPU and memory for parsing/rendering, but the
`document-processing` extra does not install local inference weights, Torch or
CUDA packages. Rebuild an older worker image to remove previously installed
packages.

Approve the provider's exact base URL in `MODEL_PROVIDER_ALLOWED_BASE_URLS` on the
worker. Local Ollama, LM Studio and vLLM endpoints can be configured through the
same boundary. PDF/image content is sent to that provider; choose a trusted
endpoint. Missing configuration fails explicitly without a local-model fallback.
Credentials are resolved at execution and are excluded from queued configuration
snapshots and subprocess arguments.

Queued requests retain their model and keyword settings. Current binding authority
and endpoint are checked before downloading the source and before publication.
Deletion, repurposing or endpoint changes can reject queued results. Credential
rotation uses the current binding. These checks cannot recall an inference request
already sent. Failed or partial conversion is not published as a complete index;
public diagnostics omit provider/storage exception bodies.

`keyword_languages` accepts `ko`, `en`, `ja` and `zh`, defaulting to all four.
An empty list disables extracted keyword attributes. Keywords are separate from
the transcription; each language retains up to eight unique normalized terms,
each at most 80 characters. Interface captions are localized without changing
these language tags.

## Korean/CJK font fallback

The document worker image supplies `fonts-noto-cjk` and `fontconfig` for Korean,
Japanese and Chinese glyphs when source fonts are not embedded. Embedded fonts
remain authoritative. Fallback fonts cannot repair corrupted source mappings or
glyphs. Check rendered page images and transcriptions when diagnosing missing text.

## Explicit processing and progress

The attachment menu supports processing, transcription replacement, embedding
reindexing and **Reindex document keywords**. API integrations use:

```http
POST /board/{project_uid}/card/{card_uid}/attachment/{attachment_uid}/document-processing
Content-Type: application/json

{"mode":"transcription","reprocess":false}
```

| Field | Default | Behavior |
| --- | --- | --- |
| `mode` | `transcription` | `transcription`, `embedding` or `keyword` |
| `reprocess` | `false` | Explicitly request transcription replacement when `true`; applies to transcription mode |

The endpoint requires authentication, current card visibility, card update
permission and an attachment belonging to that card. Application mutation
restrictions also apply. Invalid source/configuration returns 400; missing or
inaccessible attachment scope can return 404. Successful responses contain
`status`; observe the attachment's durable state for completion.

Transcription mode requires a configured vision binding and supported file type.
Embedding mode uses existing complete text and the selected embedding settings.
Keyword mode uses current complete text and requires enabled keyword indexing
and an authorized destination. It requires an indexed transcription with a
generation, content hash and nonempty text within the keyword size limit. It
performs no transcription, embedding inference or original-file read.

Each action applies only to the selected attachment. Settings changes and search
reads never enqueue a global backfill. Reprocessing retains the original file and
previous readable transcription until a replacement succeeds. Embedding reindex
retains the previous searchable vector pointer and its settings while pending or
failed; a successful replacement publishes the new pair before old-vector cleanup.
Disabling retrieval or revoking access still prevents reads of retained vectors.

Attachment details show separate transcription and embedding states. Card progress
also shows queued embeddings after transcription completes; completed or failed
embeddings do not keep the active indicator visible. Duplicate delivery does not
require repeating completed inference. Broker failure can leave durable work
pending for recovery; a queued notification is not worker completion.

## Reading and searching documents

PostgreSQL text search includes transcriptions and multilingual keyword attributes
without embeddings. Board search retains current card visibility, membership,
audience, workflow and date filters. Removed or foreign-board workflow columns
are excluded. Editor body/comment search matches literal content rather than
serialized model keys; legacy NUL-containing values retain encoded matching and
its limitations. Large text searches can be expensive; measure workload latency.

The command palette searches the authorized board after 300 ms of idle typing,
while open, with queries of 2–1000 characters. Results show the current card,
workflow column, attachment filename and bounded excerpts, and open the original
card. Search does not invoke a model or start processing. Transcript excerpts are
limited to two sources of at most 500 characters each per card, with at most 20
cards in the normal result set.

### Page-scoped non-destructive document reads

The MCP tool `read_card_document` reads stored transcription without conversion,
embedding or reindexing:

| Argument | Default | Bounds/meaning |
| --- | --- | --- |
| `project_uid`, `card_uid`, `attachment_uid` | Required | Current authorized source |
| `offset` | `0` | Nonnegative character offset |
| `max_chars` | `4000` | 128–8000 characters |
| `expected_generation` | Unset | Require the previously returned generation |
| `page` | Unset | Positive source-page number; offsets apply within that page |

Responses include source identity, filename, status, generation, content hash and
continuation information. Without `page`, offsets address the stored Markdown.
With `page`, Docling serializes that page from the stored structural artifact.
Keep both `page` and `expected_generation` on continuation. Missing structure or
page returns an unavailable error, rather than guessed page provenance or the
whole document. Incomplete transcription returns no content. Current source,
permission, generation and hash are checked again before content is returned.

Original attachment byte reads also recheck current permission and source after
storage access; the MCP byte-read limit is 8 MiB. Vector reads similarly validate
active generation, pointer settings, current provider authority and retrieval
enablement. Structural chunk provenance describes a section and its pages; it
does not guarantee that every excerpt offset identifies a single source page.
The MCP tool `search_card_document` searches one attachment's active vectors with
a nonblank query of 1–1000 characters and `max_tokens` of 128–4000 (default 2000).
The total excerpt budget is also capped by `retrieval.max_return_tokens`, using
`cl100k_base`. Matches include chunk identity, content, source generation/hash and
available page provenance. At most 2048 chunks may be indexed per attachment.
Older vector pointers without explicit chunk IDs require explicit reindexing
before this tool can retrieve them. Vector search invokes the embedding provider
for the query; stored-text search and document reads do not. Board-wide vector
composition and precise UI source-page navigation are not provided by these
bounded read capabilities.

## Embedding and splitter settings

Configure a **Document Embedding** binding with `agent_llm` set to `OpenAI`,
`OpenAI Compatible` or `LiteLLM`, plus `base_url`, `model_name`, `api_key` and
`retrieval`. Approve the embedding base URL in
`MODEL_PROVIDER_ALLOWED_BASE_URLS`. The endpoint must return finite vectors of
the configured dimension. Provider and external-store credentials use separate
secret fields; endpoints cannot contain credentials, query strings or fragments.

Queued embedding requests freeze non-secret model, splitter and store settings.
Credential values are refreshed from the authoritative binding. Changing model
or splitter settings does not rebuild existing embeddings. Endpoint replacement
requires a new explicit request. Only split text is embedded; source metadata is
stored for filtering and provenance.

| `retrieval` field | Default | Supported values/bounds |
| --- | --- | --- |
| `enabled` | `false` | Opt-in automatic embedding/retrieval; explicit embedding requests can request indexing while off |
| `store` | `postgres` | `postgres`, retained `sqlite`, or external `qdrant` |
| `dimensions` | `1536` | 1–65536; PostgreSQL maximum 16000 |
| `external_url` | `null` | Required for Qdrant; disallowed for built-in stores |
| `external_api_key` | Unset | Separate secret for the external store; excluded from snapshots |
| `collection_prefix` | `langboard_documents` | Qdrant collection namespace; built-in stores require the default |
| `search_type` | `similarity` | Built-in stores: `similarity`; Qdrant also supports `mmr` |
| `k` | `5` | 1–25 |
| `fetch_k` | `20` | 1–100; at least `k` for MMR |
| `score_threshold` | `null` | Finite −1 to 1; unavailable with MMR |
| `lambda_mult` | `0.5` | Finite 0–1 |
| `max_return_tokens` | `4000` | 128–16000 |
| `timeout_seconds` | `10` | Finite 1–30 seconds |
| `splitter` | Defaults below | Validated chunk configuration |

| `splitter` field | Default | Supported values/bounds |
| --- | --- | --- |
| `type` | `recursive` | `recursive`, `character`, `markdown` |
| `chunk_size` | `1000` | 64–8192, measured in the selected length unit |
| `chunk_overlap` | `150` | 0–2048 and smaller than `chunk_size` |
| `length_unit` | `characters` | `characters` or `tokens` |
| `encoding` | `cl100k_base` | `cl100k_base` or `o200k_base` for token length |
| `separators` | `["\n\n","\n","。","．",".","，","、",","," ",""]` | 1–16 literal separators, each at most 32 characters; final empty separator required except for `character` |
| `separator` | `"\n\n"` | Character-mode literal separator, at most 32 characters |
| `keep_separator` | `start` | `false`, `start` or `end` |
| `strip_whitespace` | `true` | Trim chunk whitespace |
| `markdown_headers` | `[1,2,3]` | 1–6 distinct heading levels from 1 to 6 |
| `strip_headers` | `false` | Remove Markdown headers from section text |

Markdown mode splits headings before applying chunk bounds. Character mode also
bounds unbroken paragraphs through recursive fallback. When a structural artifact
is available, section structure and provenance are retained before applying the
configured splitting limits. Captions are available in English, Korean, Japanese
and Chinese; submitted values remain the canonical values above.

### PostgreSQL retrieval default and preserved legacy indexes

Install the `document-retrieval` extra for vector operations. PostgreSQL vectors
use the existing `MAIN_DATABASE_URL`, including its TLS settings. Before enabling
them, install pgvector in the `public` schema and grant the database role schema
creation and read/write access to generated model schemas. Missing pgvector fails
explicitly. Explicit indexing creates a dimension-bound schema per embedding
fingerprint; search and cleanup do not create or migrate schemas.

PostgreSQL uses exact cosine search (`flat`), which scans relevant vectors. Measure
latency for large workloads and consider external Qdrant. Existing explicit
`store: sqlite` settings and recorded legacy generations remain supported. Saving
settings does not convert indexes or replace stored embeddings. Text search and
original transcriptions remain available independently of vectors.

## Shared external vector collections

Qdrant collections use `<collection_prefix>_<embedding-fingerprint>`. A prefix is
1–63 ASCII letters, digits, underscores or hyphens, beginning with a letter or
digit. Use a unique prefix per installation when sharing an endpoint. Legacy
pointers without a prefix retain `langboard_documents`.

Snapshots and published pointers retain their original prefix. A reindex to a
new prefix publishes its replacement before old-generation cleanup. Settings
changes perform no background scan, migration or purge. Prefixes prevent name
collisions; server credentials and collection permissions must enforce access.
Approve the external endpoint separately in `DOCUMENT_VECTOR_ALLOWED_BASE_URLS`
on API/worker processes. Current board/card permissions and generation checks
remain required.

### Private certificate authorities

For Qdrant REST connections, mount a read-only CA bundle or OpenSSL hashed CA
directory and set `SSL_CERT_FILE` or `SSL_CERT_DIR`. The standard Docker environment
generator forwards these to API, worker and graph processes. Empty values retain
HTTPX defaults. A custom bundle replaces default trust, so include public CAs
needed by other HTTPX clients in the same process. These settings are process-wide;
`KEY_PROVIDER_VAULT_CA_CERT` remains the separate vault-specific setting.

See [HTTPX environment variables](https://www.python-httpx.org/environment_variables/).
Keep certificate material, private keys, credentials and deployment endpoints out
of public source. Verify the deployed Qdrant server's TLS, permissions and
persistence before relying on a shared endpoint.

## Cleanup and recovery

Replacing vectors or deleting extracted content records cleanup in the same
transaction as the source change. Extraction-only deletion clears derived text
and structural artifacts while preserving the original attachment, filename and
file reference. Separate attachment deletion controls the attachment itself.
Card/board deletion records cleanup only for its scope and rejects late inference
publication. Removing a Wiki reference does not delete the Wiki's resources.

Cleanup uses the recorded generation and original destination. Qdrant cleanup
requires its original current binding and endpoint; deleted, repurposed or
redirected bindings retain failed work without contacting a replacement server.
Credentials refresh from the original binding. Cleanup opens existing collections
and never creates a missing collection. Legacy namespace generations require their
recorded `chunk_count`; missing counts are not automatically reconstructed.

Keep database migrations, worker task registration and recovery scheduling in
step when upgrading. The bundled recovery invocation checks at most 100 due
vector and 100 due keyword requests per run, without scanning attachments. The
legacy `langboard run:document-cleanup:recover` command remains vector-only.
Dispatch follows commit; broker failure leaves durable requests recoverable.
Workers claim 120-second leases. Failed vector deletion retains its recorded
pointer and retries with backoff capped at one hour; only completed deletion
removes the request. An acknowledgement that deletion is incomplete retains work.

Failed unpublished vector cleanup also retains durable work when its request can
be committed; the previously readable generation remains intact. A process killed
before that commit, or an unavailable primary database preventing it, can leave
staged data outside this recovery guarantee. Monitor retained failures and repair
original connection authority. Do not discard pending cleanup to force a database
downgrade. Requests already sent to remote services cannot be recalled.

## Optional Elasticsearch keyword search

PostgreSQL remains the mandatory text-search path. Optional Elasticsearch reads
supplement it with bounded attachment candidates; they do not replace existing
matches. Provision an instance-owned concrete index separately, with verified TLS
and scoped credentials. Index names and namespaces alone do not establish tenant
isolation. This feature indexes attachment text projections, not a replacement
index for all card bodies and comments, and makes no search-latency guarantee.

### Native optional keyword provider settings

An active instance administrator can configure **Settings → Keyword search**.
Read and indexing switches default to off. Create an **instance** secret through
the secure browser input or select an existing instance SecretRef. Personal,
board and workspace secrets cannot be used by entering their URI. Credential
values remain in the configured vault and are supplied out of band; settings
retain only the SecretRef, approved revision and destination. Background access
is audited as **Native keyword service**.

`GET /settings/keyword-search` returns the current revision, retained connections,
instance secret metadata and summaries of pending cleanup/blocked indexing. Its
optional `cursor` returns at most 50 retained connections per page; follow
`next_cursor` for more. Secret metadata is limited to 100 entries, with
`secret_references_truncated` indicating additional entries. The selected connection
is returned separately even when outside the current page. `PUT /settings/keyword-search` accepts:

| Field | Default/bounds |
| --- | --- |
| `expected_revision` | Required nonnegative integer; use the current revision |
| `endpoint` | Empty; maximum 2048 characters; configured destinations require HTTPS |
| `index` | Empty; maximum 128 characters; one concrete index |
| `secret_ref` | Empty; maximum 128 characters; active instance reference |
| `secret_revision` | `0`; nonnegative integer; approved current secret revision |
| `reads_enabled`, `indexing_enabled` | `false`; booleans |

Stale revisions return 409; reload before updating. Invalid settings return 400;
unavailable secret authority returns 403. Responses omit credential values.
`POST /settings/keyword-search/secret-input` with `name` (1–256 characters) starts
secure secret entry; `GET /settings/keyword-search/secret-input/{input_uid}` checks
its status. Submit the actual credential through that secure input flow.

A persisted native settings row supersedes environment switches even when both
switches are off. Without one, the explicit legacy environment configuration below
is used. Existing legacy jobs retain their original destination and never receive
a native connection's key.

Enabling indexing affects future processing and explicit attachment actions.
Switching/disabling providers preserves ownership and does not delete projections,
reindex attachments or trigger a backfill. Pending indexing for a nonselected
connection remains blocked; deletion cleanup can still use its retained original
active connection. Retain those connections until cleanup completes.

`POST /settings/keyword-search/connections/{connection_uid}/revoke` accepts the
current `expected_revision`, blocks new credential resolution and preserves queue
evidence. After rotating a secret, save settings again to approve its new revision;
old grants remain pinned. Requests already sent cannot be recalled. Version fences
reject late acknowledgements of superseded source work.

### Read-only operator connection preflight

Install the optional client with `uv sync --locked --extra keyword-search`.
The supplied client uses Elasticsearch 9 compatibility; select a compatible server
rather than assuming a legacy cluster is supported. Resolve the API-key SecretRef
through deployment secret delivery into `LANGBOARD_ELASTICSEARCH_API_KEY`, then run:

```sh
langboard search:check \
  --endpoint https://search.example.invalid:9200 \
  --index langboard-instance-keywords-v1 \
  --ca-certs /run/secrets/search-ca.pem \
  --timeout 5 \
  --require-write
```

`--api-key-env` selects another injected variable. Keys are never CLI arguments or
persisted by the command. Certificate verification stays enabled; omit `--ca-certs`
to use official-client defaults. Timeouts are per request, 1–30 seconds, with no
automatic retries. Credential-bearing URLs, wildcard/multiple/system indices,
aliases and data streams are rejected.

The command checks `read`/`view_index_metadata` privileges, resolves exactly one
concrete index and performs a zero-document, no-source search. `--require-write`
checks `index`/`delete` privileges without writing. It never creates indices or
triggers indexing. Cluster-wide `monitor` is optional: without it,
`version_verified=false` and `server_version=null`; with it, the server major
version must match the client. Failures return nonzero status and fixed diagnostic
codes without keys, remote error bodies or document content.

Native settings expose the same read-only checks through
`POST /settings/keyword-search/connections/{connection_uid}/preflight`.
A missing optional client reports `keyword_search_extra_required`. A passing
preflight establishes connectivity and checked privileges; operators must also
verify actual indexing, authenticated application search, isolation and cleanup
against their deployed service.

### Legacy environment settings

| Variable | Default/meaning |
| --- | --- |
| `DOCUMENT_KEYWORD_ELASTICSEARCH_ENABLED` | `false`; enable supplemental reads |
| `DOCUMENT_KEYWORD_ELASTICSEARCH_INDEXING_ENABLED` | `false`; capture new processing/explicit keyword requests |
| `DOCUMENT_KEYWORD_ELASTICSEARCH_ENDPOINT` | HTTPS endpoint |
| `DOCUMENT_KEYWORD_ELASTICSEARCH_INDEX` | One instance-owned concrete index |
| `LANGBOARD_ELASTICSEARCH_API_KEY` | Injected secret; never stored with source/job metadata |
| `DOCUMENT_KEYWORD_ELASTICSEARCH_CA_CERTS` | Optional CA path; also used by native connections |
| `DOCUMENT_KEYWORD_ELASTICSEARCH_TIMEOUT` | `1`; read timeout, 1–2 seconds; also used by native reads |
| `DOCUMENT_KEYWORD_ELASTICSEARCH_WRITE_TIMEOUT` | `10`; write timeout, 1–30 seconds; also used by native writes |

Both flags require the literal value `true` to enable their legacy path. Changing
flags never processes existing files. Legacy cleanup requires the current
endpoint/index to match the retained original destination before resolving the
key. Restore that connection to unblock old cleanup; no current key is sent to an
unmatched old destination.

### Supplemental document candidates

The remote projection contains exact keyword identities `board_uid`, `card_uid`
and `attachment_uid`, an `active` boolean, full `text`, and `projection_hash`:
lowercase SHA-256 of the exact UTF-8 transcription string. This differs from the
original file's `content_hash`; do not normalize text or hash the file instead.

Reads request at most 20 identifiers/hashes for one board, without remote text or
highlights, with a 500 ms server timeout and no transport retry. Current primary
source permissions and membership/audience are rechecked afterward. Only matching
live card/attachment identities and exact current projection hashes are accepted;
excerpts come from local source. Projections over 131072 characters are excluded
from this supplemental stage; PostgreSQL search remains available. Missing,
unavailable, stale or incomplete optional indexing preserves PostgreSQL coverage.
Search never reads original files or triggers indexing/inference.

### Durable attachment keyword projections

Processing captures a destination before transcription; publication of that same
generation activates indexing. Explicit keyword reindex activates the selected
attachment's current complete text without inference. Requests survive broker
failure and are recovered through the bounded invocation above. Missing or
oversized text is never silently truncated. Fixed diagnostics retain retryable
work without storing document content, credentials or remote error responses.

Source requests, publication and removal increase external versions. Writes use
strict external versioning. Unknown-outcome retries require matching remote
version, provenance and hash. Deletion writes a higher-version `active=false`
tombstone with provenance, an empty hash and no text. Tombstones and ownership
records remain to prevent delayed older writes from restoring deleted content.
Cleanup uses retained original ownership even when source metadata is absent.
Manually populated remote indices are not automatically adopted.

Migration downgrade refuses retained ownership/native grants, instance secret
scope or workload audit history where discarding them would lose authority or
cleanup records. Preserve them for explicit repair and cleanup. A functioning
optional index still requires live primary-source authorization for every read.

## Cloud replacement publication and retained references

A project attachment keeps its logical UID when its bytes are replaced. Each changed
upload receives an independent immutable logical storage locator. Identical bytes can
share one private physical object. The previous locator and an
internal deleted provenance row remain retained; the row points directly to the same
live owner. Existing protected embed URLs resolve through that exact same-project,
same-owner provenance to the current attachment. Revoked membership, deletion,
foreign ownership, ambiguous object keys, orphan aliases and alias cycles deny access.
Downloads still recheck current authority and object identity after bounded spooling.
There is no public version browser. Private byte reuse does not confer read authority.

The document row separates the requested immutable source/generation/configuration
from its last published text and vector source snapshots. A replacement or reprocessing
request fences earlier workers immediately. Pending conversion, queue failure and
extraction failure preserve readable published text and searchable published vectors.
Successful extraction advances text and its source label independently; vectors keep
their previous source/generation/configuration until a successful compare-and-swap
publication replaces them. Reads and search label the source and generation of the
output they actually return. Vector cleanup is recorded only when a successful vector
publication retires the previous pointer or the logical document is deleted. Extraction
cache references advance with successful extraction publication and release at terminal
deletion. Old physical objects remain retained internally under native storage ownership.

Same-byte replacement leaves storage and processing unchanged. Explicit duplicate add
creates separate native UIDs and logical file ownership while sharing immutable physical
bytes and reusing the existing private
conversion and embedding caches. Removing one logical document releases only its own
cache references. The additive `9460fa18ec32` migration follows `8359ef07db21`, preserves
and labels populated project/wiki published state, and refuses destructive downgrade
while retained provenance or published source snapshots exist. Wiki processing continues
to use the shared lifecycle with nullable source snapshots.

Terminal project deletion retains the existing soft deletion and physical byte retention
policy. Current authority checks deny downloads after deletion; authoritative extraction
references and published vector pointers are retired. Unpublished cache uses retain the
existing bounded lease/expiry recovery. Internal historical objects have no purge or
version-selection UI, and an old native URL follows the current file rather than selecting
the historical bytes.


## Private native attachment byte ownership

`AttachmentStorageObject` identifies bytes by provider, SHA-256 and length;
`AttachmentStorageReference` owns each distinct native FileModel tuple. Card,
project and wiki producers use the same storage facade, including clone/import
calls that allocate distinct native locators. Import staging uses a fresh logical
locator per attempt; stable native lineage still owns committed replay. Retried
compensation retains its previous attempt tombstone and cannot release a later
attempt. The registry contains no
user grant, attachment name projection or canonical public URL. Native source
provenance and current exact-owner authorization remain responsible for all
original and historical URL delivery, including the final check after spooling.
The private `attachment_objects` namespace is rejected by both the facade and
native HTTP route. Registry fields are excluded from API/notification payloads.

Uploads freeze their input through a spool with a 1 MiB memory threshold, read
in 64 KiB chunks, and enforce the configured upload limit. Database allocation
joins the native owner transaction: PostgreSQL locks the content row and SQLite
reserves its writer through the same conflict-safe insert. An allocation token
fences readiness publication. Providers create immutable objects without
replacing existing bytes; local publication links a completed temporary file
and S3 publication uses a conditional object create. The facade verifies bounded
SHA-256 and length before publishing readiness or any logical reference.

Provider success can precede database publication failure. Immutable provider
keys are reproducible from the verified bytes, so retry verifies and adopts the
already completed object without another physical upload. A failed or stale
publisher cannot commit its logical reference after token or transaction loss.
Incomplete or mismatched provider data fails closed and is never silently
replaced. A ready shared object is never overwritten by another acquire.

Release retains a logical reference while any current or historical native row
still owns its exact tuple; otherwise it tombstones only the selected reference.
Repeated release cannot
delete physical data or another active, pending or historical owner's bytes.
Physical objects, including zero-reference and rollback remnants, remain
retained; garbage collection, physical backfill, and provider purge are outside
this change. Legacy files without a registry reference continue through their
native adapter; generated `ref_` keys and private locators never fall back to a
legacy provider lookup. Registry query failures propagate instead of treating
an unknown managed object as legacy.

The additive `a570fb29ed43` migration follows `9460fa18ec32`, creates empty private
ownership tables, performs no provider I/O or existing-file rewrite, and refuses
downgrade while any physical row or logical tombstone remains. Processing cache
fingerprints, model generations, source-authority checks and independent vector
ownership retain their existing contracts. Controlled S3 fixtures demonstrate
adapter behavior; they provide no live-provider deployment evidence.
