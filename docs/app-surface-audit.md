# App identity and independent surfaces

An app can provide signals, an inbox integration, workflow cards and a panel.
Each surface has its own declared capabilities and consent. Enabling one surface
does not implicitly enable the others.

| Surface | Purpose | Required authority |
| --- | --- | --- |
| Signal | Publish bounded normalized evidence without creating a card | Approved definition, signed connection, selected resource and current publication grant |
| Inbox | Read and triage evidence; explicitly create an ordinary card with an evidence link | Current user and resource access; separate card mutation permission for creation |
| App-owned workflow card | Use native card features with optional app interactions and archive behavior | Declared card capabilities, selected resources and durable app ownership |
| Panel | Show an optional app interface | `panels.render` and separately granted data or action capabilities |

An ordinary card created from inbox evidence does not acquire app ownership.
An app-owned card remains subject to native card permissions and lifecycle rules.
Panel rendering does not grant card mutation or workflow execution.

## Logical apps and execution modules

A logical app can group explicitly registered modules into one catalog entry.
Composition changes presentation; each module retains its exact identity,
version, trust targets, connection ownership, binding revision and grants.

Shared names, icons, key suffixes, panel URLs and signal source declarations do
not establish shared ownership or authority. Capabilities are never combined
into a group grant. Built-in and independently installed modules keep their
original keys and approval requirements.

## Withdraw individual surfaces

Closing a panel removes `panels.render`. Independently approved signal, card and
workflow capabilities remain unchanged. The panel bridge rejects reads without
rendering consent, while a separately authorized native inbox or publisher uses
its own current authority.

Reviewing a panel with its signal-read checkbox cleared explicitly withdraws
`signals.read`. Grant changes update the binding revision. Stale publisher
envelopes are rejected with HTTP 409; changing an old envelope's revision is not
a valid retry. New events use the current revision. Existing receipts remain
stored, and inboxes project current evidence rather than one item per receipt.

Disabling the entire board app clears its board grants and denies publication.
Existing cards, evidence links, event receipts and connection records remain
stored. Disabling a binding does not claim to undo changes already made in an
external service.

## Catalog API compatibility

The default `/apps/catalog` response is a flat list of enabled definitions,
including composition members. Pagination uses raw-key `next_cursor` values.
Existing clients can continue using this form.

Clients can request `include_compositions=true` to discover logical apps and
ungrouped enabled modules. The current AppStore uses this projection. A page
contains at most 50 logical entries, and a composition contains at most 16
explicitly registered members. Clients should cache this projection separately
from flat discovery.

Composition and module keys can coincide. Opt-in cursors therefore include the
identity kind: `key~composition` or `key~module`. Within this projection, legacy
`after=key` remains accepted and skips all identities through that key. Default
flat discovery continues to use raw-key cursors.

Use `key=module` for exact module detail. Use `composition_key=key` for logical
detail; this selector opts into the composition projection even without
`include_compositions`. The two detail selectors are mutually exclusive.
Existing module routes, binding keys and account shortcuts keep their meaning.

Logical detail shows sanitized current member versions, requested capabilities,
definition revisions and administrator availability. Disabled members remain
visible as unavailable and offer no module action. Catalog discovery applies
current instance and organization policy and board read access. It does not
expose credentials, personal connections, storage locators or trust destinations.
Board configuration and approval still use each module's consent surface.
