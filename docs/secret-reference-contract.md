# Secret references and trusted storage

Use a Secret Reference to connect a credential without placing its value in a card, wiki, app
declaration or model conversation. The canonical form is `secret://ref/<uid>`. The URI
identifies a record; it is not a credential and does not grant its reader permission to use the
stored value.

Availability depends on the instance's configured storage provider, installed host version and
current permissions. See the [external app SDK](external-app-sdk.md) for connection consent and
[native MCP profiles](native-mcp-profiles.md) for transport authority.

## Reference identity and permissions

Canonical URIs survive rename and scope moves. Named aliases are `secret://me/<name>`,
`secret://project/<board-uid>/<name>` and `secret://workspace/<organization-uid>/<name>`.
Aliases reflect current name and scope and can become dangling after a change. Retain the
canonical URI in integrations; a display name is not an identity.

Every metadata read and trusted use rechecks the current active user and scope. Personal
references belong to their user. Project references require current project update authority and
membership. Workspace references require the active organization owner. Moves require authority
over both source and destination. App consumers also require their own current consent;
disabling apps does not prevent native Secret maintenance or revocation.

Missing, revoked, unauthorized, dangling-storage and mismatched-provider references cannot
resolve. Changing a storage provider does not reinterpret an old locator as a path in the new
provider. Internal storage locators are never valid client inputs. No HTTP or MCP endpoint
returns credential values.

## Browser credential input

Enter credential material only on the authenticated secret-input page issued by a supported
native operation. The one-use input is bound to its current actor and context and requires the
expected browser origin and session proof. Cancellation and expiry do not save a credential. Do
not paste values into a card, wiki or chat to complete input.

A successful completion receipt must contain both `state: completed` and a canonical
`secret://ref/<uid>` reference. Empty, pending or malformed HTTP 200 responses do not establish
a save. After an uncertain response, inspect the operation's receipt; never automatically
resubmit credential material. The browser clears sensitive request material before showing the
receipt.

## Native metadata transports

`GET /secret-references/<uid>` returns `{reference: metadata}` under authenticated native user
authority. Missing and unauthorized references return the same not-found response. Metadata
reads do not retrieve credential material from storage.

The read-only MCP command `get_secret_reference_metadata(uri)` accepts canonical or named
logical URIs and returns metadata under current authenticated scope authority. It does not
create, rotate or resolve credential values. Clients may use the existing native HTTP/MCP
transports without a plugin dependency.

Metadata and history responses use `Cache-Control: no-store`.

## History and copies

`GET /secret-references/<uid>/history` returns bounded per-reference history under current
reference authority. `limit` defaults to 25 and is at most 50; use the returned cursor for
another page. History includes actions, revisions and actor attribution, without values, storage
locators or provider configuration. Links to source cards or wikis additionally require current
access to those resources. History is transactional host history, not an independent
tamper-proof ledger.

`POST /secret-references/<uid>/copy` accepts `name` (1–256 characters) and strict nonnegative
`expected_revision`, returning `{reference: metadata}` with HTTP 201. A copy remains in the
source scope and has a separate identity; it does not transfer ownership of the source or expose
its value. Stale revisions or conflicting names return conflict. Missing or unavailable sources
return not found. An ambiguous write requires receipt inspection before another attempt.

Wiki documents may retain a canonical URI and render a reference link. Rendering or copying URI
text does not itself resolve the credential or authorize an app.

## Rotation and provider maintenance

Trusted credential rotation preserves the reference URI and name/scope, advances its revision
and records history. Stale revisions, revoked references or provider mismatches reject the
operation. A failed update preserves the old reference. Successful rotation can leave an
old-storage cleanup obligation if retirement fails. Rotation scheduling and cleanup retries are
operator responsibilities; no general HTTP/MCP credential rotation command is exposed by this
guide.

Trusted provider migration preserves URI, name and scope and advances the revision only after
destination readback succeeds. An operator must supply authenticated source and destination
provider configuration through trusted host facilities; there is no public HTTP/MCP migration
command or automatic provider switch. Write, readback or audit failure preserves the original
reference. Old storage is retired after commit; retirement failure requires explicit cleanup.

OpenBao and HashiCorp Vault use their configured KV storage. Local file storage is for
development, uses restrictive file permissions and is not a production fallback. Provider
failures do not silently select a development backend. AWS KMS/Azure-backed encrypted values can
retain historical ciphertext; reference revocation and cleanup do not promise physical or
cryptographic erasure of copies.
