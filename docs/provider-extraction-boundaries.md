# Provider integration surfaces

Built-in GitHub, GlitchTip and Dokploy setup and ingestion are provided by the
host. Installing a separate panel package does not replace those host services
or transfer provider credentials. An external service app uses its own approved
connection and the common signed API; its browser panel never receives provider
credentials. See the [external app SDK guide](external-app-sdk.md).

## Independent surfaces

| Surface | Purpose | Does not imply |
| --- | --- | --- |
| Signal | Record an external occurrence or observation as evidence | Inbox triage, card creation or workflow completion |
| Signal inbox | Group and explicitly triage received signals | Card ownership, approval or archival |
| App-owned card | Track actionable work using native card features and the app's declared lifecycle | Signal publication or inbox acknowledgment |

Apps may provide supported combinations of these surfaces. A native card created
explicitly from an inbox signal retains an evidence link without becoming
app-owned. An app-owned card can exist without an inbox entry. Optional automation
between these surfaces needs its own declared action, current consent and receipt.

## External service publication

`POST /apps/v1/boards/{board}/signals` accepts signed, bounded standard envelopes.
The external service normalizes its provider payload before publication. Current
connection, board, app definition, binding and selected-resource access apply;
publication retains finite event/outcome policies and duplicate identity checks.
The SDK signs exact bytes and does not automatically retry an ambiguous write.

Publishing evidence does not create a card, approve a human request, move workflow
or grant execution. Provider writes need separate consent and provider permission.
Archive retention cannot implicitly merge a pull request or stop a deployment.
See [app card lifecycle](app-card-lifecycle.md) and
[human interactions](app-human-interactions.md) for those distinct operations.

When replacing a configured integration, preserve existing connections, resource
identities and receipt history. Do not copy credentials implicitly or run two
independent automations against the same work. Confirm the replacement's current
permissions and duplicate handling before retiring its previous connection.
