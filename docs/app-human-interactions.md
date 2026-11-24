# App human interactions v1

## Scope

Apps may request human decisions on their own cards through native reactions, card actions and
structured forms. Read/seen receipts are not consent or approval. App source attribution
identifies the submitting app; it does not prove AI authorship. Availability depends on
installed host and SDK versions and configuration. See the [external app
SDK](external-app-sdk.md) for installation and consent, [native MCP
profiles](native-mcp-profiles.md) for client authority, and [capability
diagnostics](native-capability-diagnostics.md) for unavailable actions.

## Optional card managers

Use **Actions** in a card to open **Signal evidence**, **App resources**,
**App requests** or **App executions**. These managers load when opened instead
of occupying the default card body. Closing a manager preserves saved evidence,
resource selections, request history and execution receipts. It does not cancel
an app request or stop an execution.

Native MCP clients may use the advertised card-management tools under the same
current card, connection and app permissions. Lightweight connectors may expose
a smaller subset; a tool described in documentation is not automatically
available on every connection. Select existing signal evidence and resource IDs
with current revisions. Never synthesize provider evidence from an agent report.
Read request and execution histories separately from card completion and human
approval. An explicit execution stop request revokes the current permit; it does
not prove that a remote process has terminated. Request responses and execution
start or resume continue to require their existing authorized human paths.

Native discovery uses `get_card_signals`, `list_card_signal_resources` and
`list_card_signal_candidates`. Candidates are a bounded page from the current
authorized board inbox; follow `next_cursor`, and inspect already linked evidence
with `get_card_signals`. Attach or unlink only the intended existing signal using
`attach_card_signal` or `unlink_card_signal` and the current card/binding revision.
`list_card_app_requests` and `list_card_app_executions` inspect retained history;
`request_card_app_execution_stop` requires the intended current generation.
Resource selection uses `get_card_app_resources` and
`set_card_app_resource_selection` with its own current revision.

## App cancellation

`POST /apps/v1/boards/{board}/cards/{card}/interactions/{interaction}/cancel` accepts only a
strict positive integer `expected_revision`. Current credentials, card ownership, resource
selection and `interactions.request` scope are rechecked. A pending unexpired request changes to
`cancelled` and increments its revision atomically. The original request remains
unchanged. Repeating the original revision returns `changed: false`; other stale revisions or
terminal states conflict. An expired request stays expired without mutation. Every receipt
retains `execution_resumed: false`. Revoked credentials cannot cancel or read. This endpoint
does not deliver an event or cancel an external runtime.

## Request and response data

A request retains its host UID and revision, board/card and owning app/connection, execution
request UID and native execution generation, app request key, kind, localized prompt, question
schema, presentation, expiry and responder policy. Request kinds are `approval`, `question` and
`action`. Runtime instructions are separate authorized commands; they do not require a prior app
request. Response schemas explicitly support `single_choice`, `multi_choice`, `free_text` and
`choice_or_text`; an array alone does not imply multiple selection. An instruction may also be
submitted by an authorized human to a currently running app without an outstanding app question.
This does not authorize a stopped runtime to resume.

Questions and options use stable IDs independent of localized labels. Multi-choice schemas
declare minimum and maximum selections; free text declares a length limit. Requests and
responses reject secret input. Attachments, if supported, use existing readable attachment
references and current access checks, never arbitrary URLs or copied credentials. Interaction
attachments are unavailable.

A human response contains the interaction UID, expected revision, native execution generation,
idempotency key and values keyed by question ID. Actor identity comes from authentication, never
the submitted body. Reaction presentation binds a specific comment and reaction to an immutable
interaction/choice pair. Only a new current event by an authorized human may respond. Past
reactions, AI reactions, seen events and unrelated comments cannot approve. Removing a reaction
cannot reverse a previously accepted decision. Explicit rejection uses a new authorized request
where needed.

Example request:

```json
{
  "schema_version": 1,
  "kind": "question",
  "app_request_key": "decision-17",
  "execution_generation": 3,
  "prompt": {"en": "Choose the next step", "ko": "다음 작업을 선택하세요"},
  "questions": [{
    "id": "next_step",
    "type": "single_choice",
    "choices": [
      {"id": "review", "label": {"en": "Review", "ko": "검토"}},
      {"id": "revise", "label": {"en": "Revise", "ko": "수정"}}
    ]
  }],
  "presentation": {"type": "choice_card"},
  "outcomes": {
    "review": {"workflow_stage": "review"},
    "revise": {"workflow_stage": "active"}
  }
}
```

Approval uses explicit approve/reject option IDs. Comment reactions and card buttons submit the
same validated response. A request without a workflow outcome may receive a response without
moving its card. Apps cannot introduce workflow stage types. Explicit outcome mappings resolve
to the existing board columns and record both stage key and column UID/revision. Duplicate stage
columns must not be resolved by name or by whichever column is found first.

## Authority and state

### Native card compatibility

App ownership is additive integration metadata, not a replacement card model. Authorized users
and user-delegated MCP clients retain the native comment, reaction, relationship,
checklist/checkitem, attachment, label and card editing operations under their existing
visibility and action permissions. They do not need the owning app installed, running or
connected to use these native features. An app panel or interaction request must not hide or
replace those controls. Relationships may connect readable app-owned and ordinary cards through
the existing relationship API; checklists retain native completion and timer semantics.

Owning-app credentials authorize only explicitly granted app operations; they cannot impersonate
a user or acquire general native write authority from an app badge. Unbound automation remains
subject to the existing ownership fence. Ordinary comments, reactions and checklist changes
never implicitly answer an interaction or approve an external action. Only the specifically
bound reaction and currently authorized responder can satisfy a pending interaction. Native card
edits must be reflected by subsequent app reads and current execution checks.

App creation/read/cancel requires its current credential, declared and granted interaction
capability, matching own card, connection ownership/organization, resources and execution
identity. Human response requires current card visibility, responder eligibility and board
action permission. App registration, resources consent and a human answer are not execution
grants. Recheck policy, app/connection state, ownership, resources, dependencies and generations
before side effects.

Response revision checks allow one winner. Identical authorized
retries return the saved receipt; a reused key with changed body, actor or generation conflicts.
Expired/canceled/revoked requests retain history and cannot resume. Expired and cancelled
requests remain inspectable.

Track request state separately from response delivery and runtime state. Human response
acceptance, workflow transition applied, outbox dispatch, app acceptance, and observed execution
continuation have separate receipts. A transport `sent` receipt is never proof of runtime
continuation or completion. Unknown delivery outcome is recorded; an uncertain runtime command
is not automatically replayed.

## Transition and continuation order

1. Persist the validated response and selected transition intent atomically.
2. Recheck authority and apply the existing native column transition, producing a
   durable receipt. If no transition is requested, record that explicitly.
3. Deliver the response to its owning app through the signed app event outbox.
   Dedupe by event UID and retain delivery and app acceptance receipts.
4. The app rechecks its current execution permit and maps stable answer IDs to
   its frozen provider request. It reports observed continuation separately.

A failed or stale column mapping leaves the decision inspectable but blocks continuation; it
must not claim a completed transition. Preserve partial/unknown outcomes and revalidate every retry. If a transition
changes the generation, the old permit cannot authorize continuation; acquire a fresh explicitly
authorized execution identity. A still-authorized waiting turn continuation is distinct from
resuming a stopped or expired runtime. No automatic lease renewal follows human response.

## Non-agent app use cases

The common request can omit execution context. GitHub may request a human review choice on its
own workflow card; GlitchTip may request resolve/ignore/escalate; Dokploy may request a
deployment action choice. These examples require provider-specific support; they are not
built-in actions. All use stable choice IDs and the same typed host widgets. Inbox signals
remain observations, not decisions or action grants.

Provider verbs are not host workflow stage identities. Apps own external API execution and
report its receipt after current permission checks. Only requests that explicitly bind a current
execution identity participate in HITL runtime continuation. Response acceptance does not
establish external action completion, successful deployment or a resolved incident.

## App request storage

POST `/apps/v1/boards/{board}/cards/{card}/interactions` stores a validated request under a
current app credential. GET on `/{interaction}` reads it back. Creation requires separately
declared/granted `interactions.request`, current own-card ownership, selected readable
resources, active connection policy and card access. It uses an immutable app request key and
body for exact duplicate recovery; a changed body conflicts. Expiry is explicit and bounded to
seven days. Readback projects expired requests without a global scan. No request calls a
provider, accepts a human response, moves a card or renews/resumes execution. Optional execution
context is checked against the existing execution authority.

## Choice or text requests

Question requests may preserve predefined stable choice IDs while offering custom non-secret
input through `choice_or_text` with required `max_length` (1–16000). The validated response
shape is exactly one of `choice_id` or `text`; both together are invalid. Predefined choices may
retain existing workflow outcome mappings. Custom text has no implicit outcome or approval
semantics. Approval/action kinds and comment-reaction presentation reject this question type.
Custom text does not authorize external continuation.

## Response value validation

`validate_card_interaction_values` validates immutable JSON values against the stored request.
Every declared question needs exactly one keyed answer. Choices use stable IDs, never labels.
Multiple selection enforces unique IDs and declared count limits. Text preserves content while
enforcing non-empty and declared length bounds. Choice or text is exclusive. Extra/secret fields
and non-finite JSON are rejected; total values are capped at 32 KiB. This is input validation
only. The host response operation additionally checks human authentication, current authority,
revision/generation and expiry. Execution continuation remains a separate gate from value
validation, acceptance, delivery and reception.

## Human responses

`POST /board/{board}/card/{card}/apps/interactions/{interaction}/responses` requires native user
authentication. App credentials and bots cannot impersonate responders. The body includes strict
`expected_revision`, `idempotency_key`, `values`, and optional `execution_generation`; actor
identity is server-derived. The current activated user must read the card and hold native
card-update rights (or own the board). Current app ownership, connection, selected resource and
`interactions.request` declaration/grant are checked even on a retry. Executable requests
additionally pin the current execution request/generation/authority.

Responses and cancellation are serialized. Each interaction retains one response with the actor,
original revision, values, generation and idempotency key. An exact retry returns the same
response; changed actor/key/body/revision/generation conflicts. Expired, cancelled or archived
requests cannot accept new answers. `comment_reaction` requests reject direct submission; use
the native reaction path described below. App readback includes the accepted response under its
current authority.

`answered` is the response projection, not a new card-wide acknowledgment status. The receipt
explicitly reports `execution_resumed: false`, `workflow_transitioned: false`. Delivery is
`not_queued` unless the explicit response-event destination/grants below are enabled. Provider
actions and explicit native column movement require their separate authority and receipts.

## Response outbox and reception acknowledgment

An accepted response queues `io.langboard.app.interaction.answered.v1` in the same transaction
only when the board grants `events.receive` and the approved owning connection has a destination
explicitly subscribing to that event. Missing or disabled delivery configuration leaves
`delivery_state: not_queued`; acceptance never silently installs a webhook or grants a feature.
The event contains stable interaction/response/event IDs, actor, immutable values, optional
execution context and the pinned destination revision. Events use exact-byte signatures, bounded
retries and retained delivery history. Execution request events keep their existing contract and
authority checks.

Claim and post-DNS revalidation check the current native responder, owner app, connection,
selected resource, interaction/event grants, optional execution authority and exact approved
destination. Revocation blocks delivery. Delivery availability also depends on the instance
delivery workers and app receiver.

App-authenticated `POST
/apps/v1/boards/{board}/cards/{card}/interactions/{interaction}/acknowledgments` accepts
`event_uid` and bounded `runtime_reference`. The event must be delivered or have a current live
delivery claim. The same app and current authority are required. One immutable acknowledgment is
retained per response; exact retry is unchanged, a different event/reference conflicts. HTTP
transport delivery and app reception are distinct receipts. Readback exposes both. Neither means
an observed execution continuation, provider action, or workflow transition.

Native card execution generation and provider runtime lease generation are separate values.
Consumers retain their own request/runtime mapping and reject obsolete context. A human answer
never renews a runtime lease automatically.

## Delivery recovery

Accepted answers remain saved if event scheduling fails. Pending signed events can be recovered
by configured delivery workers; duplicate answers do not create another event. Recovery does not
re-enable blocked, failed or delivered events or choose a new destination. Current authority is
checked on every send attempt. Delivery success does not establish observed runtime
continuation.

## Native comment reaction responses

A newly added native user comment reaction can satisfy exactly one pending interaction mapped to
that comment and reaction. The host loads the real `CardCommentReaction` row, verifies its user
identity, comment/card provenance, creation after the request, current responder/app authority
and stable mapped choice. The response idempotency key retains the reaction UID. Bots, old
reactions, unmapped reactions, seen events and other comments cannot answer. Ordinary comments
without a pending interaction do not constitute approval.

General response submission cannot impersonate a reaction. Accepted responses remain immutable
when a reaction is removed or re-added, and do not replay an outbox event. A comment cannot have
two unexpired pending reaction requests; creation rejects the ambiguity.
Answered/cancelled/expired requests do not block an explicit follow-up request. An old reaction
cannot answer that follow-up. Delivery revalidates the retained authorized response; later
reaction removal is not a revocation of an already accepted decision.

## Native user request listing

`GET /board/{board}/card/{card}/apps/interactions` reads the retained request, response and
delivery/reception projection under current native user/card visibility and board read
permission. App credentials cannot use this endpoint. A disabled or disconnected app does not
erase this history; submitting answers still requires current app authority. Deleted/inactive
users and withdrawn board read permission cannot read the retained data.

Pages contain at most 25 requests, newest first, with an exclusive `cursor` from `next_cursor`.
Related responses, response-event outbox entries and reception acknowledgments are fetched in
bounded batches. Responses use `Cache-Control: no-store`; listing does not mark the card seen,
triage an inbox, approve a request, move a column or resume execution.

## Card detail interaction UI

The native card detail includes an optional, initially collapsed request section. It fetches a
25-request page only while open, with explicit refresh and previous/ next navigation and no
background polling. Query identity includes the current user, board and card. Existing host
buttons, checkboxes and text areas preserve the application theme. English, Korean, Japanese and
Chinese host strings are included; app prompt/choice labels use the user locale and English
fallback.

Approval/card actions, single/multiple choice, free text and choice-or-text submit stable
question/choice IDs, the observed revision, an idempotency key and optional execution
generation. Current native card update permission, expiry and request state gate the controls;
server authority remains decisive. A lost response blocks further submissions until explicit
readback. Page navigation and refresh are disabled during submission. Reaction requests direct
users to the linked native comment rather than allowing a general form to impersonate a
reaction. A successful local reaction refreshes the open request view.

## Frozen workflow outcome destinations

New requests retain a host-owned workflow snapshot separately from the original SDK request.
When transitions are explicitly enabled on the board binding, nonempty outcomes must use the
approved app's declared built-in stages and its explicit stage-to-column mapping. The host
checks each current column's board, archive/deletion status, stage and revision and the built-in
active stage. It never chooses a column by name or by first matching stage.

The snapshot preserves the binding revision and each choice's exact column UID, stage and column
revision. Fresh mapped answers reject changed bindings or columns. Text answers have no mapped
workflow outcome. Requests created while transitions are disabled remain disabled after a later
enable; existing requests migrated with an empty snapshot remain `legacy_unbound` and acquire no
destination.

Answer/list receipts and new signed response events project a `workflow_plan` with `planned`,
`disabled`, `no_outcome` or `legacy_unbound` state. `planned` is an immutable destination
intent, not a movement receipt: `workflow_transitioned` remains false. Older signed events
without this field retain their original body.

Explicit native movement, described below, revalidates the frozen mapping and current authority.
Moving out of Ready invalidates the old execution permit; neither an answer nor a planned target
silently renews that permit.

## Explicit native workflow application

After an accepted mapped answer, an authorized user may explicitly apply its frozen destination
with `POST /board/{board}/card/{card}/apps/interactions/{id}/transition`. The body contains the
exact `response_uid` and strict `expected_card_revision`. The server checks current native user
membership, card visibility/update rights, owning app/connection/resource authority and the
original responder's current answer authority. A request carrying execution context must still
match the current execution grant before its first movement. Current frozen mapping and
column/stage revisions are checked again. No app credential can impersonate this native user
operation.

The card moves to the target column's start using native ordering, workflow effects and
readiness rules. Movement and its immutable receipt commit together. A failed native operation
rolls both back. An exact repeated actor/source revision returns the saved receipt without
moving again; conflicting repeats fail. A card already at the target produces a distinct no-op
receipt. Current authorization is still required when returning a duplicate receipt.

Native request listing exposes current card revision and batches transition receipts with the
page's answers. The card UI provides an explicit movement button after an answered mapped
request and displays recorded movement after readback. A lost movement response locks replay
until refresh. A movement receipt contains source/target, actor, binding/column revisions and
resulting card change sequence. It never claims external execution resumed. Moving out of Ready
does not renew the previous execution permit.

## Retained app source attribution

App and native request receipts include `source.app_key` and `source.connection_uid` from the
immutable request row, without fetching connection credentials or account metadata. Native card
UI renders the app key with the existing host badge and a localized explanation in English,
Korean, Japanese and Chinese. A disconnected app retains this attribution alongside readable
request history.

An app-authenticated request is not proof of AI authorship. The badge identifies the submitting
app and does not label every app request as AI-generated. The badge does not verify AI
authorship of external requests or comments.

## Isolated panel request history

The panel SDK supports `interactions.list`. The host routes this operation to `POST
/board/{project_uid}/apps/{app_key}/panel/interactions` with the installed app and binding
revisions. Only `card_uid`, optional `cursor`, and optional `limit` come from the panel. Limits
are strict integers from 1 to 25, default 10.

The authenticated human must still be able to read the card. Current panel consent and
`interactions.request` must be granted; the result page is restricted to the current app before
pagination. Foreign app requests cannot enter the page. Stale revisions conflict; revoked panel
consent and inaccessible cards are denied. Responses use `no-store`. The SDK's existing 16 KiB
JSON limit remains in effect; oversized request pages fail instead of bypassing transport
bounds.

## Host-owned response form for isolated panels

The panel SDK supports `interactions.open` with `{card_uid, interaction_uid}`. The operation
returns `{state: "opened", interaction_uid}` immediately. This means only that the host opened
its human form, not that an answer was accepted. No app provided values, actor, revision or
approval are accepted in this operation.

The host rereads the exact interaction via `interactions.list` with the optional
`interaction_uid` filter and current pinned app/binding revisions. It renders the existing
native question form in a lazy host dialog. Queries are scoped to the current
human/board/app/request and discarded on unmount. Only one dialog is open at a time. The
authenticated human chooses or enters values and presses Submit.

The form posts to `/board/{project}/apps/{app}/panel/cards/{card}/interactions/{id}/responses`.
Current consent, app ownership, card ACL, request revision, expiry, responder authority,
idempotency and execution generation are checked through the native service. A removed grant
denies even a repeated accepted response. Foreign app requests cannot be submitted through this
route. Submission does not move a card or resume a runtime. A lost POST response triggers
readback, never an automatic second write. Closing/revoking/hiding the panel removes the host
dialog; external changes already committed are not represented as cancelled or reverted.

## Human stop request

`POST /board/{project_uid}/card/{card_uid}/apps/executions/{request_uid}/stop` accepts only
`{generation}` under authenticated native user authority. The host rechecks the current user,
readable card, board membership and `card_update` permission, then matches the retained
execution request to that board/card and its generation. App credentials and caller-provided
identities are not accepted.

An authorized lease becomes `stop_requested`, `permit_execution: false`, with one
actor-attributed history entry. Repeated requests retain the same receipt; `stopped` never
becomes authorized again. App disconnection does not prevent a human who still has card
authority from requesting termination. A missing lease or mismatched generation returns conflict
instead of creating a runtime.

This revokes authorization immediately. The existing external runtime heartbeat observes the
stop request and must terminate its process. Only its subsequent stop report changes the lease
to `stopped`; the human request does not claim that termination already happened. No automatic
resume, generation change or workflow transition is performed. Actual termination depends on the
external runtime honoring the revoked permit.

The native card also provides a lazy **App execution** section. Its authenticated GET
`/board/{project_uid}/card/{card_uid}/apps/executions` returns at most ten request identities
per page and retained permit states after current native read authority checks. It never exposes
runtime tokens or credential hashes and returns bounded retained permit state.
These are authorization states. These do not establish process health or execution.

The section performs no background polling. Human stop controls require current UI update
permission (the API rechecks it), and are unavailable while sending or while a prior result is
unresolved. A lost stop response never triggers a second POST: the user explicitly refreshes
before another action. Stop requested and runtime-reported stopped have distinct translated
labels in English, Korean, Japanese and Chinese. Queries include the user and card and are
discarded on unmount. Explicit resume requires the newer-generation checks described below.

### Stopped generation fence

Current owner-app execution authority checks retained runtime leases for the same card and
execution generation. `stop_requested` and `stopped` deny that generation at the execution
authority boundary, including replayed request/outbox paths. Restoring connection consent or
issuing another credential cannot reopen it. This is a generation fence, not a deletion of
historical receipts.

A newer readiness generation is evaluated independently under current authority; it never
changes the old stopped lease. A new generation requires an actual readiness false-to-true
transition. No resume endpoint increments it merely because a user answered a question.

Runtime history supports a validated request-ID cursor and previous/next controls. Each page
displays at most ten requests, with a cursor when more exist. All pages retain current card/user
authority checks. Older authorized runtimes remain reachable for explicit stop requests instead
of disappearing behind a latest-ten cutoff. Uncertain writes block navigation until readback
succeeds.

## Explicit resume into a newer Ready generation

`POST /board/{project_uid}/card/{card_uid}/apps/executions/{request_uid}/resume` accepts only
`{generation}` identifying the retained stopped request. It requires current native human update
authority, a runtime-reported `stopped` lease, and a strictly newer producer-issued readiness
generation. Current owner-app consent, connection ownership, selected resources, built-in Ready
mapping, dependencies and approval gates are re-evaluated. The endpoint never increments the
readiness counter, moves the card or reauthorizes the old lease.

Resume saves one new execution request and its signed-delivery event atomically.
Actor-attributed resume provenance is appended to the old stopped lease. Repeating that resume
returns the same new request; revoked current authority still denies replay. A newer generation
already accepted independently conflicts rather than falsely attributing it to this action. A
different subsequent generation also conflicts with that retained resume action. The receipt
says `resume_requested`, `started: false`, `execution_resumed: false`: external delivery, a new
permit and actual process start are later, distinct evidence.

The card shows Request resume only for a stopped request with a newer Ready generation and no
retained resume receipt. A lost response requires explicit readback; no second POST is
automatic. A recorded resume is displayed as awaiting a new runtime. Questions never resume
execution automatically. This supports a fresh authorized execution generation, not in-process
continuation of an old runtime/checkpoint. Providers own any compatible checkpoint restoration.

## Execution request delivery after acceptance

Initial app execution requests and explicit human resume requests share the same acceptance
path. When the app declares `events.receive`, the board grants it, and the connection has a
currently approved event destination, acceptance snapshots the destination UID and revision in
the outbox transaction. Delivery is scheduled after acceptance commits. Duplicate acceptance and
transaction rollback do not schedule another delivery.

If event scheduling is unavailable after acceptance, the pending event retains its frozen
destination for the existing recovery worker. Delivery still rechecks current authority and the
destination revision before sending signed bytes. Acceptance without a valid approved
destination does not infer a network target or retarget the event later. A request receipt
remains `started=false`; neither persistence nor queue submission proves that an external
runtime started.

## App execution result to native Review

The SDK provides `AppExecution.request_review` over `POST
/apps/v1/boards/{board}/cards/{card}/execution-requests/{request}/review`. The caller supplies
the producer generation, current card revision, current binding revision and a bounded result
reference. App-owned card reads now also return `execution_generation`. Keep the original write
inputs for an ambiguous retry; the SDK does not retry automatically.

This explicit result action requires current owning-app `execution.run` and `resources.read`
consent, organization/connection policy, native card access, unchanged ownership and
selected-resource revisions, the matching current generation, an app start attestation, and a
retained stopped lease. The stopped permit supplies termination evidence only. Review must be
declared in the app's workflow requirements and explicitly mapped with stage transitions enabled
to an active built-in Review stage that does not count as completed. Done, Archive and
app-invented stages are not targets of this action; human acceptance remains separate.

Native ordering and readiness rules apply. Movement and an immutable request-keyed receipt
commit together. Current authority is rechecked before returning a duplicate receipt; a changed
request fingerprint or mapping conflicts. A result reference remains app attestation, not
verified PR or business acceptance.

### Request-scoped Review reconciliation

The SDK exposes `AppExecution.read_review` via GET on the same
`/apps/v1/boards/{board}/cards/{card}/execution-requests/{request}/review` route. This read
checks current owning app, `cards.read`, `resources.read`, selected resources and their frozen
authority revisions. It does not require a fresh execution grant or Review transition mapping to
inspect a historical receipt. Revocation and ownership/resource changes block access.

A response has `state: recorded` and an immutable `receipt`, or `state: not_recorded` with null
receipt. New receipts include the original POST `inputs` (generation, expected card/binding
revisions and evidence reference); older receipts may lack these fields. The separate `current`
object reports current generation and revisions without rewriting historical inputs. `changed`,
`started` and `execution_resumed` remain false. A missing receipt is a point-in-time
observation, not permission to resend. After an ambiguous POST, retain the original attempt and
explicitly reconcile its scope and every input against readback; never synthesize a new
generation or automatically repeat POST.

### Optional external MCP authority boundary

The SDK separates `mcp.read`, `mcp.write` and `mcp.automation` consent from native
card/resource/execution capabilities. Updating the tool names or authority classification
withdraws the affected MCP scope; description/order-only updates preserve consent. Current trust
identity changes still revoke affected connections.

GET `/apps/v1/boards/{board}/cards/{card}/mcp-tools` returns card-bound declarations for the
authenticated owning app, with current card/resource consent, native visibility, connection
policy and selected resource checks. Qualified names include app and connection identity. This
endpoint never contacts remote servers, grants authority or forwards credentials. It reports
`execution_supported: false` and `callable: false`. Remote invocation/OAuth and per-user
connection consent are still required before enabling tools in native FastMCP.

FastMCP's official Proxy Provider supports remote composition and session isolation. Its
incoming Authorization forwarding default must be disabled for app backends; host/user
credentials must not be silently forwarded. See https://gofastmcp.com/servers/providers/proxy .

### Connection-owner MCP consent

Board consent is insufficient for external MCP. A connection owner explicitly approves scopes
via GET/PUT `/settings/apps/connections/{connection}/mcp-consent` using native user
authentication. An app Bearer cannot approve itself. Personal connections remain exclusive to
their owner even when another user is an instance administrator; organization-owned connections
use current organization management authority. PUT pins consent revision, connection identity
revision and current app revision to reject stale approvals. Empty scopes revoke consent,
including cleanup after disable.

Approval retains each scope's reviewed tool-name set and identity fingerprint independently from
the connection's token. Current declaration, enabled policy, resource/card authority and board
consent intersect with owner consent for discovery. Tool-set/scope updates durably withdraw
affected grants; reverting to an older set does not resurrect them. Trust changes and full app
disable revoke all connection MCP grants. Ordinary descriptions/version changes preserve
unaffected grants. Remote OAuth/invocation and FastMCP exposure are unavailable; catalog tools
report `callable: false`.
