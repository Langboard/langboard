# Notification destination contract proposal

Status: implementation candidate; these routes are not yet deployed.

A/B minimal vertical contract proposal v1 (not yet deployed)

Ownership: Langboard owns user approval, destination proof state, subscription generation, notification event and receipt records. The consumer owns verified external sign-in, destination resolution, durable proactive outbox and provider message reconciliation. No provider-specific endpoint in Langboard.

A. Personal destination proof
1. User-authenticated POST /settings/apps/connections/{connection_uid}/notification-destination-challenges:
   {request_id, expected_connection_revision, kind:"personal"}
   -> {challenge_uid, challenge_token, expires_at, revision:1}.
   Current connection management/owner, enabled app and current policy must pass. Token lifetime 600s, only hash stored. An app credential cannot create this user consent challenge.
2. App-authenticated POST /apps/v1/notification-destination-proofs:
   {challenge_uid, challenge_token, external_identity:{issuer,subject}, destination:{kind:"personal",external_id,display_name}, request_id}.
   Credential must identify the exact connection/app pinned in the challenge; live credential, connection identity, declaration trust and policy are checked. The adapter must derive issuer/subject and external_id from its verified external session/binding, never from arbitrary user-supplied IDs. No Langboard user UID, email match, board grant or user token accepted in this request.
   -> {proof_uid, challenge_uid, revision:1, state:"awaiting_user_approval"}.
   The app assertion proves the external side only. It does NOT prove that the external identity equals a host SSO identity, nor does it grant user-route access.
3. User-authenticated GET challenge shows the asserted app/external account/destination. Explicit POST .../{challenge_uid}/approve {expected_revision, proof_uid} by the initiating current user commits destination:
   {destination_uid,revision,kind:"personal",state:"verified",connection_uid}.
   Approval pins both authenticated sides. A typed email, existing can_read room bind or existing conversation reference is never approval. Unlink/revoke increments revision and generation, cancels pending work; app disconnection or changed identity invalidates it.
4. Shared destinations require a separate challenge/approval by a current board-authorized actor and explicit selected board/resource scope. Personal approval never authorizes a shared room.

B. Subscription, test event and delivery receipt
User-authenticated POST /settings/apps/notification-subscriptions:
{request_id,destination_uid,expected_destination_revision,scope:{kind:"personal"},event_types:[...]}
-> {subscription_uid,generation:1,enabled:true}.
Shared scope is {kind:"boards",project_uids:[...],visibility:"SHARED"} and requires current authority for every selected board/resource. No wildcard board grant.
User-authenticated POST /settings/apps/notification-subscriptions/{subscription_uid}/test:
{request_id,expected_generation,expected_destination_revision}
-> {event_uid,event_revision:1,state:"pending"}.
The first test is synthetic non-sensitive text; real content stays disabled until proof + consent + current scope pass.

Signed event uses the existing exact-byte webhook verifier/signing headers and existing AppExecutionOutbox, lease and SSRF checks:
{schema_version:1,event_id,event_type:"io.langboard.app.notification.requested.v1",event_revision:1,occurred_at,app_key,connection_uid,subscription:{uid,generation},destination:{destination_uid,revision,kind:"personal",external_id},notification:{uid,kind:"test",title,body},audience:{kind:"personal",user_uid},expires_at}.
event_id, notification uid and revision are immutable. Delivery attempt or re-signing never creates a new event ID. A destination revision change blocks old payload delivery instead of redirecting it. Notification fields and content limits will be encoded in the SDK schema before host activation.

HTTP 2xx records transport delivery only. App-authenticated POST /apps/v1/notification-events/{event_uid}/receipts:
{request_id,event_revision,subscription_generation,destination_revision,status:"adapter_accepted"|"confirmed"|"unknown"|"rejected",provider_message_id?,reason_code?}.
Current credential must match the original app/connection and immutable event snapshot. Receipt updates are durable/idempotent; stale revision/generation and conflicting reuse return409. confirmed requires a real provider message ID. accepted is not sent. unknown is terminal for automatic provider resend pending reconciliation; never convert it to success or blindly resend. Preserve immutable attempt history.
Native GET status is metadata-only to the approving user/current authorized operator. No signing secret, challenge token, credential, external conversation reference or private content in logs/status.

Before each initial send and redelivery: recheck current connection/app/declaration, user activation, destination proof/revision, subscription enabled/generation, selected board/resource grant and current card ACL/visibility. Shared content must still be SHARED. Revoke -> block, do not quietly drop forbidden fields and still send. Consumer atomically admits event UID/revision into its existing durable outbox; repeat signed HTTP delivery reconciles the same entry, not another provider send. Host transport recovery does not solve a provider send followed by lost ACK. Existing provider reconciliation owns unknown resolution.

Delivery scope: personal proof -> explicit approval -> subscription -> signed test event -> accepted/confirmed receipt. No new calendar aggregate, independent scheduler, LLM call, provider implementation or app-credential impersonation of user routes. Real reminders/change/cancel and selected shared boards remain explicit subsequent acceptance, not implied by the test event.

Consumer queue revalidation: POST /apps/v1/notification-events/{event_uid}/delivery-authorization with the original app credential and {event_revision,subscription_generation,destination_revision}. The host checks the immutable event snapshot against the current app/connection, initiating user, destination proof, subscription, selected resource grant, source revision/ACL/visibility and expiry. Success is {authorized:true,event_uid,event_revision,subscription_generation,destination_revision,checked_at}. It returns no content, bearer execution permit or new consumer lease. The consumer calls this without caching after its existing durable queue claim and immediately before provider I/O, including every redelivery. HTTP403/409 forbids the send; HTTP503/timeout retains the same pending entry. This is a decision at checked_at, not a promise that authority cannot subsequently change. It does not resolve a provider send whose ACK was lost.

The signed destination external_id is the opaque consumer reference pinned by user approval. It is not a raw conversation reference or a credential. A different external_id under the same event ID/revision is a conflict. The consumer resolves it through its currently verified local binding and refuses changed or unavailable targets.


App-wide approved service targets
--------------------------------

An instance administrator binds one enabled app service template to an existing
HTTPS webhook and its secret reference. The target must pass the same declaration
trust and event allowlist checks as an explicit connection destination. No
webhook, secret or signing key is copied per user.

A connection owner reads its service template metadata, including only the
service origin, template revision and the connection revision already returned
by native connection discovery. Preparing that approved service destination pins
the exact template revision on that connection; it cannot accept a callback URL
or overwrite an explicit administrator connection binding. Preparing a target
is configuration only and never issues a credential or approves custody.

Credential custody remains a separate explicit user approval. A changed or
disabled template fences old connection pins and credentials; refreshing a pin
does not silently reapprove custody or retarget an already accepted event.
The user reviews and approves the new destination revision before another
handoff. No per-user administrator click is required for an unchanged approved
service template.

This contract is a development candidate. Full notification subscriptions,
notification event/receipt delivery, UI and live acceptance remain incomplete.


Personal test-notification vertical (development candidate)
---------------------------------------------------------

The user-authenticated POST /settings/apps/notification-subscriptions accepts
scope=personal, an already explicitly approved destination_uid, its exact
expected_destination_revision and a stable request_id. Subscription approval is
separate from destination proof and credential custody. This first vertical
accepts only a personal test notification; it does not subscribe to card events
or share card content with rooms. Those broader flows remain unimplemented.

POST /settings/apps/notification-subscriptions/{subscription_uid}/test takes
expected_generation, request_id, title and body. The native host persists one
immutable notification dispatch and one existing AppExecutionOutbox event.
Replaying identical admission returns that event without requeueing it; changed
content under the same request ID conflicts. The event expires after ten minutes.
Revocation increments the subscription generation and fences retained events.

The signed event type is io.langboard.notification.requested.v1. Its body has
schema_version=1, event_uid, event_type, app_key, connection_uid,
notification={kind:test,title,body}, notification_fence={event_revision:1,
subscription_uid,subscription_generation,destination_uid,destination_revision},
external_destination={kind:personal,external_id}, expires_at and the existing
transport destination snapshot. external_id is the approved opaque binding,
never a caller-supplied provider address. Signature verification uses the exact
native webhook bytes and existing signature headers.

The app bearer POST /apps/v1/notification-events/{event_uid}/delivery-authorization
requires event_revision, subscription_generation and destination_revision. The
response contains authorized=true, event_uid, those three exact fence values
and checked_at. It contains no content or transferable execution permit. Call
it immediately before provider I/O after claiming the existing consumer queue;
never cache it. Current app/connection identity, host user, explicit proof and
subscription, target revisions, immutable payload and expiry are rechecked.
Unknown or terminal provider receipts forbid another send authorization.

POST /apps/v1/notification-events/{event_uid}/receipts accepts the same fence,
stable request_id, status, expected_revision, reconciled and optional
provider_message_id/reason_code. It returns {event_uid,receipt:{fence,status,
provider_message_id,reason_code,revision}}. Accepted/unknown/confirmed/rejected
transitions use the existing receipt rules; unknown requires explicit provider
reconciliation, and confirmation requires a provider message ID. Replays return
retained evidence without a send. A conflicting request ID or stale receipt
revision is rejected. Transport delivery and provider receipt remain separate.

The source and isolated tests do not establish schema rollout, a live external
provider result, card/HITL integration or authenticated browser acceptance.


User-started proof context in the signed custody handoff
------------------------------------------------------

A user first reads GET /settings/apps/connections/{connection_uid}/credential-custody
to review owner-scoped metadata, its current connection revision and any existing
custody revision. This returns no credential, callback secret or external subject.
Destination configuration and explicit custody approval remain separate actions.

After the native user creates a destination challenge, the existing user-authenticated
handoff request optionally accepts notification_challenge_uid and
notification_challenge_token together. Before resolving the approved callback,
before issuance and after callback acknowledgement, the host revalidates the exact
initiating native user, app/connection identity, pending challenge, nonce hash,
expiry and existing custody approval. A challenge can bind at most one handoff.
A request ID cannot switch, add or remove this context on replay.

Only the existing fixed HTTPS service receives the signed callback. It includes
an optional destination_challenge object with challenge_uid, challenge_token,
expires_at and initiated_by_user_uid. The nonce exists only in the one bounded
callback's memory body; the database retains its hash in the existing challenge
and the association ID in the handoff. User responses and retained receipts do
not return the nonce or credential. A callback cancelled or expired before
native completion leaves the staged credential revoked. The native status
usable decision remains mandatory before activating any staged credential.

The receiver verifies the exact signed bytes and its fixed origin/app/connection/
custody/credential context. It may retain an expiring nonce hash bound to that
context, never make arbitrary caller-supplied connection UIDs a credential lookup.
An externally authenticated user must present the same challenge UID and nonce
through a body or exact-origin channel. Do not put them in URLs, queries, logs,
browser storage or general-purpose provider requests. This context permits only
the pending proof step. External issuer/subject come from the receiver's actual
verified login; matching email strings is not an identity binding.

The proof still awaits the initiating native user's review and approve action.
No app invokes that approval on the user's behalf. Neither the challenge nor
this handoff grants a subscription, room sharing or provider execution authority.
Legacy handoffs without this optional context remain valid; they do not become
proof-credential selectors merely because an external user supplies a UID.
