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
proof. SDK 0.2.14 provides request validation and app-authenticated create/read. Host storage is implemented; human response, cancel, delivery, native UI and runtime continuation remain pending.

## Request and response data

A request retains its host UID and revision, board/card and owning app/connection,
execution request UID and native execution generation, app request key, kind,
localized prompt, question schema, presentation, expiry and responder policy.
Request kinds are `approval`, `question` and `action`. Runtime instructions are separate authorized commands; they do not require a prior app request. Response schemas explicitly
support `single_choice`, `multi_choice` and `free_text`; an array alone does not
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
