# App human interactions v1 — agreed contract, implementation pending

## Scope and evidence

Apps optionally request human decisions on their own Langboard
cards. The same server contract backs a comment reaction, a card action, or a
structured card made from host widgets. AI attribution and the required response
are visible. Seen receipts do not count as consent, approval or a response.

Symphony was examined as an independent consumer on 2026-10-11. Its current
adapter supports `answer` and `steer`, generation/thread/turn fences, durable
commands, and claim/receipt delivery. It has no generic Langboard interaction API,
explicit approval kind, native column mapping, or resume/cancel contract. Its
question options currently identify answers by label, not stable choice ID.
These are consumer constraints; they are not host vocabulary or implementation
proof. SDK 0.2.19 provides request/value validation and app-authenticated create/read/cancel. Host human response storage, optional signed response outbox and app reception ACK are implemented; Broker dispatch and bounded cron recovery are wired in source; production scheduling verification, native UI and runtime continuation remain pending.

## Implemented app cancellation

`POST /apps/v1/boards/{board}/cards/{card}/interactions/{interaction}/cancel`
accepts only a strict positive integer `expected_revision`. Current credentials,
card ownership, resource selection and `interactions.request` scope are rechecked.
A pending unexpired request changes to `cancelled` and increments its revision
under the request row lock. The original request remains unchanged. Repeating the
original revision returns `changed: false`; other stale revisions or terminal
states conflict. An expired request stays expired without mutation. Every receipt
retains `execution_resumed: false`. Revoked credentials cannot cancel or read.
This endpoint does not deliver an event or cancel an external runtime.

## Request and response data

A request retains its host UID and revision, board/card and owning app/connection,
execution request UID and native execution generation, app request key, kind,
localized prompt, question schema, presentation, expiry and responder policy.
Request kinds are `approval`, `question` and `action`. Runtime instructions are separate authorized commands; they do not require a prior app request. Response schemas explicitly
support `single_choice`, `multi_choice`, `free_text` and `choice_or_text`; an array alone does not
imply multiple selection. An instruction may also be submitted by an authorized
human to a currently running app without an outstanding app question. This does
not authorize a stopped runtime to resume.

Questions and options use stable IDs independent of localized labels. Multi-choice
schemas declare minimum and maximum selections; free text declares a length
limit. Requests and responses reject secret input. Attachments, if supported, use
existing readable attachment references and current access checks, never arbitrary
URLs or copied credentials. Their implementation remains a separate gate.

A human response contains the interaction UID, expected revision, native execution
generation, idempotency key and values keyed by question ID. Actor identity comes
from authentication, never the submitted body. Reaction presentation binds a
specific comment and reaction to an immutable interaction/choice pair. Only a new
current event by an authorized human may respond. Past reactions, AI reactions,
seen events and unrelated comments cannot approve. Removing a reaction cannot
reverse a previously accepted decision. Explicit rejection uses a new authorized
request where needed.

Illustrative request (wire endpoints are not implemented):

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

Approval uses explicit approve/reject option IDs. Comment reactions and card
buttons submit the same validated response. A request without a workflow outcome
may receive a response without moving its card. Apps cannot introduce workflow
stage types. Explicit outcome mappings resolve to the existing board columns and
record both stage key and column UID/revision. Duplicate stage columns must not be
resolved by name or by whichever column is found first.

## Authority and state

### Native card compatibility

App ownership is additive integration metadata, not a replacement card model.
Authorized users and user-delegated MCP clients retain the native comment,
reaction, relationship, checklist/checkitem, attachment, label and card editing
operations under their existing visibility and action permissions. They do not
need the owning app installed, running or connected to use these native features.
An app panel or interaction request must not hide or replace those controls.
Relationships may connect readable app-owned and ordinary cards through the
existing relationship API; checklists retain native completion and timer semantics.

Owning-app credentials authorize only explicitly granted app operations; they
cannot impersonate a user or acquire general native write authority from an app
badge. Unbound automation remains subject to the existing ownership fence.
Ordinary comments, reactions and checklist changes never implicitly answer an
interaction or approve an external action. Only the specifically bound reaction
and currently authorized responder can satisfy a pending interaction. Native
card edits must be reflected by subsequent app reads and current execution checks.

App creation/read/cancel requires its current credential, declared and granted
interaction capability, matching own card, connection ownership/organization,
resources and execution identity. Human response requires current card visibility,
responder eligibility and board action permission. App registration, resources
consent and a human answer are not execution grants. Recheck policy, app/connection
state, ownership, resources, dependencies and generations before side effects.

Accept a response with a transactional revision comparison and one winner.
Identical authorized retries return the saved receipt; a reused key with changed
body, actor or generation conflicts. Expired/canceled/revoked requests retain
history and cannot resume. Persist expiry and cancellation outcomes without a
full-card background scan. Bound read pages and dispatch work.

Track request state separately from response delivery and runtime state. Human
response acceptance, workflow transition applied, outbox dispatch, app acceptance,
and observed execution continuation have separate receipts. A transport `sent`
receipt is never proof of runtime continuation or completion. Unknown delivery
outcome is recorded; an uncertain runtime command is not automatically replayed.

## Transition and continuation order

1. Persist the validated response and selected transition intent atomically.
2. Recheck authority and apply the existing native column transition, producing a
   durable receipt. If no transition is requested, record that explicitly.
3. Deliver the response to its owning app through the signed app event outbox.
   Dedupe by event UID and retain delivery and app acceptance receipts.
4. The app rechecks its current execution permit and maps stable answer IDs to
   its frozen provider request. It reports observed continuation separately.

A failed or stale column mapping leaves the decision inspectable but blocks
continuation; it must not claim a completed transition. Do not hold a database
transaction open for a remote runtime RPC. Preserve partial/unknown outcomes and
revalidate every retry. If a transition changes the generation, the old permit
cannot authorize continuation; acquire a fresh explicitly authorized execution
identity. A still-authorized waiting turn continuation is distinct from resuming
a stopped or expired runtime. No automatic lease renewal follows human response.

## Consumer adapter responsibilities

The host never stores a required Codex RPC method, thread ID, turn ID, profile ID,
or provider token in its generic decision schema. Consumers retain a mapping from
host interaction and execution identity to their own frozen request context.
Native card execution generation and provider runtime lease generation are
separate values; adapters must not assume equality. Symphony maps stable choice
IDs to its frozen question labels and rejects obsolete turn/request context.
Provider-specific timeouts, keepalive and RPC acknowledgment remain in the app.

## Acceptance gates

Use the same immutable SDK package with the Symphony app and a second independent
agent app. Demonstrate request/read/cancel, human response, signed delivery and
receipt readback without core-code monkeypatches. Cover:

- comment reaction, card approval action and structured choice UI;
- single/multiple choice, free text, localization without answer identity changes;
- cross-app/card/connection denial, current human permissions, secrets rejected;
- stale revision/generation, concurrent responders with one winner, exact retry;
- expiry, cancel and revocation, restart-preserved receipts and no unsafe resume;
- workflow mapping failure without continuation; generation change with explicit
  reauthorization; sent versus accepted versus observed continuation;
- actual app question response and live instruction, not only catalog registration.

Deployment and authenticated desktop/mobile readback remain separate gates.

## Non-agent app use cases

The common request can omit execution context. GitHub may request a human review
choice on its own workflow card; GlitchTip may request resolve/ignore/escalate;
Dokploy may request a deployment action choice. These are examples, not currently
implemented provider actions. All use stable choice IDs and the same typed host
widgets. Inbox signals remain observations, not decisions or action grants.

No provider-specific verbs become host workflow stages or SDK methods. Apps own
external API execution and report its receipt after current permission checks.
Only requests that explicitly bind a current execution identity participate in
HITL runtime continuation. Response acceptance must never imply external action
completion, successful deployment or a resolved incident.

## Implemented request storage boundary (0.2.14)

POST `/apps/v1/boards/{board}/cards/{card}/interactions` stores a validated request
under a current app credential. GET on `/{interaction}` reads it back. Creation
requires separately declared/granted `interactions.request`, current own-card
ownership, selected readable resources, active connection policy and card access.
It uses an immutable app request key and body for exact duplicate recovery; a
changed body conflicts. Expiry is explicit and bounded to seven days. Readback
projects expired requests without a global scan. No request calls a provider,
accepts a human response, moves a card or renews/resumes execution. Optional
execution context is checked against the existing execution authority.

## Choice or text request support (SDK 0.2.17)

Question requests may preserve predefined stable choice IDs while offering custom
non-secret input through `choice_or_text` with required `max_length` (1–16000).
The future response shape is exactly one of `choice_id` or `text`; both together
are invalid. Predefined choices may retain existing workflow outcome mappings.
Custom text has no implicit outcome or approval semantics. Approval/action kinds
and comment-reaction presentation reject this question type. Request storage is
implemented; response acceptance, widgets and external continuation are pending.

## Shared response value validation (SDK 0.2.18)

`validate_card_interaction_values` validates immutable JSON values against the
stored request. Every declared question needs exactly one keyed answer. Choices
use stable IDs, never labels. Multiple selection enforces unique IDs and declared
count limits. Text preserves content while enforcing non-empty and declared
length bounds. Choice or text is exclusive. Extra/secret fields and non-finite
JSON are rejected; total values are capped at 32 KiB. This is input validation
only. The host response operation additionally checks human authentication, current
authority, revision/generation and expiry. Execution continuation remains a
separate gate from value validation, acceptance, delivery and reception.

## Implemented human response storage

`POST /board/{board}/card/{card}/apps/interactions/{interaction}/responses`
requires native user authentication. App credentials and bots cannot impersonate
responders. The body includes strict `expected_revision`, `idempotency_key`,
`values`, and optional `execution_generation`; actor identity is server-derived.
The current activated user must read the card and hold native card-update rights
(or own the board). Current app ownership, connection, selected resource and
`interactions.request` declaration/grant are checked even on a retry. Executable
requests additionally pin the current execution request/generation/authority.

A row lock serializes response and cancellation against the immutable request.
One response row per interaction retains the actor, original revision, values,
generation and idempotency key. An exact retry returns the same response; changed
actor/key/body/revision/generation conflicts. Expired, cancelled or archived
requests cannot accept new answers. `comment_reaction` requests reject direct
submission until a native current-reaction handler is implemented. App readback
includes the accepted response under its current authority. Migration downgrade
refuses to discard retained response history.

`answered` is the response projection, not a new card-wide acknowledgment status.
The receipt explicitly reports `execution_resumed: false`,
`workflow_transitioned: false`. Delivery is `not_queued` unless the explicit
response-event destination/grants below are enabled. No provider action or column
movement follows acceptance yet. Those remaining gates must use the retained
response identity and their own receipts.
SQLite and disposable PostgreSQL HTTP/storage regressions cover authentication,
exact retries, conflicts, expiry, cancellation and current grant revocation.
Four concurrent identical PostgreSQL submissions return one response UID with
exactly one changed receipt. Competing different actors/bodies remain explicit
conflicts; external delivery and continuation are not covered by this test.

## Response outbox and reception acknowledgment

An accepted response queues `io.langboard.app.interaction.answered.v1` in the same
transaction only when the board grants `events.receive` and the approved owning
connection has a destination explicitly subscribing to that event. Missing or
disabled delivery configuration leaves `delivery_state: not_queued`; acceptance
never silently installs a webhook or grants a feature. The event contains stable
interaction/response/event IDs, actor, immutable values, optional execution
context and the pinned destination revision. The existing outbox, exact-byte
signature, bounded retry, delivery history and claim-token fence are reused.
Execution request events keep their existing contract and authority checks.

Claim and post-DNS revalidation check the current native responder, owner app,
connection, selected resource, interaction/event grants, optional execution
authority and exact approved destination. Revocation blocks delivery. This is
an explicit delivery worker primitive; production scheduling and real receiver
integration remain separate gates.

App-authenticated `POST /apps/v1/boards/{board}/cards/{card}/interactions/{interaction}/acknowledgments`
accepts `event_uid` and bounded `runtime_reference`. The event must be delivered
or have a current live delivery claim. The same app and current authority are
required. One immutable acknowledgment is retained per response; exact retry is
unchanged, a different event/reference conflicts. HTTP transport delivery and
app reception are distinct receipts. Readback exposes both. Neither means an
observed execution continuation, provider action, or workflow transition.

HTTP/storage/signature regression in SQLite and disposable PostgreSQL includes
response→outbox→claim→exact signed payload→pre-send revalidation→failed attempt→
same-event retry→app-authenticated ACK→delivery finish→readback. The tests do not
send to a production receiver or prove deployed scheduling/UI/runtime behavior.

## Committed event dispatch and bounded recovery

Response events register an after-commit callback which publishes
`app_event_delivery_task(event_uid, frozen_destination_revision)`. Rollback never
publishes; duplicate responses do not publish again. Broker failures do not undo
accepted answers: the durable event remains pending. The task uses the existing
broker wrapper with three backed-off retries, while durable claim attempts cap
actual HTTP sends at four. Claim revalidation remains mandatory for every attempt.

The API runner installs the existing minute-cron pattern for
`run_app_event_cron.sh`. Recovery selects at most 25 pending or expired-lease app
events with a frozen destination revision, ordered by ID. Unpinned legacy events
cannot consume the bounded page. It never re-enables blocked/failed/delivered
events, changes destinations or scans cards/files. Multiple recoverers are fenced
by the current row claim. The existing worker still resolves a public destination
and rechecks authority before HTTP; real broker/cron/runtime deployment and actual
external receiver integration remain acceptance gates.

SQLite and PostgreSQL storage tests capture one enqueue after four duplicate
responses and verify recovery excludes delivered/unpinned events and selects a
pending pinned event. Queue publishing and the recovery HTTP function are stubbed
in this test to avoid external sends. That test is not production dispatch proof.

## Native comment reaction responses

A newly added native user comment reaction can satisfy exactly one pending
interaction mapped to that comment and reaction. The host loads the real
`CardCommentReaction` row, verifies its user identity, comment/card provenance,
creation after the request, current responder/app authority and stable mapped
choice. The response idempotency key retains the reaction UID. Bots, old reactions,
unmapped reactions, seen events and other comments cannot answer. Ordinary
comments without a pending interaction take the bounded lookup path without
locking the project/card for an approval check.

General response submission cannot impersonate a reaction. Accepted responses
remain immutable when a reaction is removed or re-added, and do not replay an
outbox event. A comment cannot have two unexpired pending reaction requests;
creation rejects the ambiguity. Answered/cancelled/expired requests do not block
an explicit follow-up request. An old reaction cannot answer that follow-up.
Delivery revalidates the retained authorized response; later reaction removal is
not a revocation of an already accepted decision.

The native toggle/store/response path is tested on SQLite and disposable
PostgreSQL, with publisher/activity/bot notifications suppressed in the test.
These tests include old reaction rejection, remove/add acceptance, direct API
bypass denial, repeat stability, follow-up freshness and signed outbox claim.
Production UI guidance and observed external continuation remain pending.

## Native user request listing

`GET /board/{board}/card/{card}/apps/interactions` reads the retained request,
response and delivery/reception projection under current native user/card
visibility and board read permission. App credentials cannot use this endpoint.
A disabled or disconnected app does not erase this history; submitting answers
still requires current app authority. Deleted/inactive users and withdrawn board
read permission cannot read the retained data.

Pages contain at most 25 requests, newest first, with an exclusive `cursor` from
`next_cursor`. Related responses, response-event outbox entries and reception
acknowledgments are fetched in bounded batches. Responses use `Cache-Control:
no-store`; listing does not mark the card seen, triage an inbox, approve a request,
move a column or resume execution. UI rendering remains a separate gate.

## Card detail interaction UI

The native card detail includes an optional, initially collapsed request section.
It fetches a 25-request page only while open, with explicit refresh and previous/
next navigation and no background polling. Query identity includes the current
user, board and card. Existing host buttons, checkboxes and text areas preserve
the application theme. English, Korean, Japanese and Chinese host strings are
included; app prompt/choice labels use the user locale and English fallback.

Approval/card actions, single/multiple choice, free text and choice-or-text
submit stable question/choice IDs, the observed revision, an idempotency key and
optional execution generation. Current native card update permission, expiry
and request state gate the controls; server authority remains decisive. A lost
response blocks further submissions until explicit readback. Page navigation
and refresh are disabled during submission. Reaction requests direct users to
the linked native comment rather than allowing a general form to impersonate a
reaction. Successful local reaction requests invalidate the open request query.

Browser fixture tests exercise mobile/desktop layout, deferred reads, accepted
response readback, uncertain results, read-only/expired/reaction restrictions,
multi-choice bounds, localized approval and literal free text. The fixture mocks
HTTP. These are UI behavior proofs, not deployed API/runtime integration evidence.
Workflow outcomes and observed external continuation are still separate gates.

## Frozen workflow outcome destinations

New requests retain a host-owned workflow snapshot separately from the original
SDK request. When transitions are explicitly enabled on the board binding,
nonempty outcomes must use the approved app's declared built-in stages and its
explicit stage-to-column mapping. The host checks each current column's board,
archive/deletion status, stage and revision and the built-in active stage. It
never chooses a column by name or by first matching stage.

The snapshot preserves the binding revision and each choice's exact column UID,
stage and column revision. Fresh mapped answers reject changed bindings or
columns. Text answers have no mapped workflow outcome. Requests created while
transitions are disabled remain disabled after a later enable; existing requests
migrated with an empty snapshot remain `legacy_unbound` and acquire no destination.

Answer/list receipts and new signed response events project a `workflow_plan`
with `planned`, `disabled`, `no_outcome` or `legacy_unbound` state. `planned` is an
immutable destination intent, not a movement receipt: `workflow_transitioned`
remains false. Pre-migration signed events without the field retain their wire
body. The migration refuses to discard nonempty frozen history on downgrade.

Actual native card movement is still pending. It must revalidate the frozen
mapping and current authority and use native ordering, workflow effects,
readiness and history paths. Moving out of Ready invalidates the old execution
permit; neither an answer nor a planned target silently renews that permit.
