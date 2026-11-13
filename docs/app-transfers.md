# Human app transfer contract

This candidate defines an opt-in transfer contract. It does not start or authorize
card execution, alter card ownership, select resources, or grant a capability.
The standalone SDK must support this contract before an app can register and use
it. Existing apps receive no new capability automatically.

## Permissions and event identity

The app declaration and enabled board binding must both explicitly include
`transfers.receive`, `cards.read`, `resources.read`, and `events.receive`. A human
must already have native readable-card access and card-update authority. Both the
sender and connection credential principal must retain current card ACL and
connection access. Only selected, granted resources of declared types qualify.
The connection identity, policy generation, app/binding revisions, resource access
revision and approved destination are frozen and revalidated before transport,
reads, app reports and explicit retry. No execution ownership is assigned.

`io.langboard.app.transfer.requested.v1` uses the native signed app-event outbox.
The event contains the transfer `request_uid`, stable `event_uid`, native board,
card, connection and resource identities, card revision, optional attachment
identity/revision/hash, and destination fence. It contains no arbitrary external
file URL or signing credential. Transport remains at least once. An app must
atomically deduplicate the stable event UID in its durable inbox before returning
a successful response. Lost HTTP responses and expired native transport leases
can redeliver the same event. A duplicated delivery must not repeat a provider
business mutation; reconcile its retained operation first.

HTTP delivery, app `received` acknowledgment, and provider outcome are separate
receipts. A reported provider result has source `authenticated_app_report`; the
host does not turn it into independent provider-readback evidence.

## Human routes and forms

All human routes use the current native authenticated user, never an app bearer
credential. Every response uses `Cache-Control: no-store`.

| Method and route | Form or query | Result |
| --- | --- | --- |
| GET `/board/{board}/card/{card}/apps/{app}/transfer-review` | Optional `attachment_uid`, `after` | Current title, optional attachment name, source `intent`, at most 50 approved targets, `next_cursor`. Each target includes its immutable `authority` snapshot. |
| POST `/board/{board}/card/{card}/apps/{app}/transfers` | `client_request_id` canonical UUID; `connection_uid`; `intent`; `expected_authority` | Durable request and event committed together; duplicate identical request returns the same identity. |
| GET `/board/{board}/apps/{app}/transfers` | Optional visible-row `after` cursor, `1 <= limit <= 50` (default 20) | Durable sender/app/board outbox summaries, current native card ACL applied in SQL before pagination; `next_cursor` references only a returned visible request. No totals or hidden-row cursors. |
| GET `/board/{board}/app-transfers/by-request/{client_request_id}` | No body | Sender-only reconciliation: `found: false` or receipt and transport history. Native card ACL still applies after app revocation. |
| POST `/board/{board}/app-transfers/by-request/{client_request_id}/retry` | `expected_retry_revision` nonnegative integer | Explicit exhausted transport retry, retaining request/event/history and monotonically increasing attempt count. |

The exact `intent` fields are `card_revision` (nonnegative integer), `resource_uid`,
`attachment_uid`, `attachment_revision`, and `content_sha256`. All three attachment
fields are null for a card transfer. Attachment transfers require a current
nondeleted native attachment belonging to the same card, with exact immutable
image revision and known SHA256. For legacy attachments with no stored hash, review computes SHA256 through the native bounded storage helper outside database transactions and revalidates source identity and every target before returning it. No attachment row is mutated. The
`expected_authority` object must be copied unchanged from the chosen review target.
The host compares it with current authority rather than trusting caller claims.

A timeout or 5xx on create must first reconcile the same client UUID with GET.
If absent, a human may explicitly resubmit the exact same immutable request and
UUID; it never creates a replacement UUID automatically. An ambiguous retry uses
the same expected retry revision. Repeating it grants no additional attempt batch.
An explicit retry grants one native batch of four attempts only when transport is
failed, authority remains current, and no app acknowledgment has been recorded.
History is never reset. No arbitrary permanent cap on human retry batches exists.

## Authenticated app routes

These routes use an existing native app connection bearer credential. Authentication
is rechecked along with the transfer's current authority. A credential from another
app or connection cannot read or report the transfer.

| Method and route | Form or query | Result |
| --- | --- | --- |
| GET `/apps/v1/boards/{board}/transfers/{transfer}` | `offset >= 0`, `1 <= limit <= 16000` | Bounded current card description, title, receipt, optional next offset. |
| GET `/apps/v1/boards/{board}/transfers/{transfer}/attachment` | No external URL input | Native storage bytes, `application/octet-stream`, `X-Content-SHA256`, no-store. |
| POST `/apps/v1/boards/{board}/transfers/{transfer}/receipt` | `expected_revision`, `acknowledgment: "received"`, nullable `provider_outcome` | Revision-fenced app report; exact replay returns unchanged. |

Provider outcome values are `unknown`, `failed`, `delivered`, and
`already_delivered`, or null when merely received. Successful terminal provider
reports cannot be overwritten. The transport receipt remains independent.

Attachment storage I/O occurs outside database transactions, including legacy
hash review. Borrowed transactions are rejected before I/O. The host captures
immutable object identity/revision/hash and uses the same configured
`MAX_FILE_SIZE_MB` limit as native uploads and protected file downloads. The
arbitrary 16 MiB subset is removed. Downloads use native `Storage.download_file`
with its established configured-limit writer, a 1 MiB memory spool that rolls to
a temporary file, and 64 KiB hash/read chunks. A second transaction reauthenticates
and revalidates every authority and exact attachment identity before releasing the
spool to the HTTP streaming response. Replacement, deletion, revocation or hash
changes discard and close the spool. The response closes it on completion,
iteration failure/cancellation, or background cleanup. No `file.path` URL is
fetched. Existing attachments exceeding a subsequently reduced native configured
file limit follow the same rejection policy as native protected downloads.

The sender status UI can list and reopen durable receipts without knowing a
browser tab's former UUID. The list returns bounded summaries without copying
unbounded event/receipt histories; selecting a visible row performs the existing
sender detail read for that original request. Session storage remains only a
convenience for a create call whose outcome is still unknown, not the outbox's
source of truth.
