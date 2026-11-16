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
{schema_version:1,event_id,event_type:"io.langboard.app.notification.requested.v1",event_revision:1,occurred_at,app_key,connection_uid,subscription:{uid,generation},destination:{destination_uid,revision},notification:{uid,kind:"test",title,body},audience:{kind:"personal",user_uid},expires_at}.
event_id, notification uid and revision are immutable. Delivery attempt or re-signing never creates a new event ID. A destination revision change blocks old payload delivery instead of redirecting it. Notification fields and content limits will be encoded in the SDK schema before host activation.

HTTP 2xx records transport delivery only. App-authenticated POST /apps/v1/notification-events/{event_uid}/receipts:
{request_id,event_revision,subscription_generation,destination_revision,status:"adapter_accepted"|"confirmed"|"unknown"|"rejected",provider_message_id?,reason_code?}.
Current credential must match the original app/connection and immutable event snapshot. Receipt updates are durable/idempotent; stale revision/generation and conflicting reuse return409. confirmed requires a real provider message ID. accepted is not sent. unknown is terminal for automatic provider resend pending reconciliation; never convert it to success or blindly resend. Preserve immutable attempt history.
Native GET status is metadata-only to the approving user/current authorized operator. No signing secret, challenge token, credential, external conversation reference or private content in logs/status.

Before each initial send and redelivery: recheck current connection/app/declaration, user activation, destination proof/revision, subscription enabled/generation, selected board/resource grant and current card ACL/visibility. Shared content must still be SHARED. Revoke -> block, do not quietly drop forbidden fields and still send. Consumer atomically admits event UID/revision into its existing durable outbox; repeat signed HTTP delivery reconciles the same entry, not another provider send. Host transport recovery does not solve a provider send followed by lost ACK. Existing provider reconciliation owns unknown resolution.

Delivery scope: personal proof -> explicit approval -> subscription -> signed test event -> accepted/confirmed receipt. No new calendar aggregate, independent scheduler, LLM call, provider implementation or app-credential impersonation of user routes. Real reminders/change/cancel and selected shared boards remain explicit subsequent acceptance, not implied by the test event.

Consumer queue revalidation: POST /apps/v1/notification-events/{event_uid}/delivery-authorization with the original app credential and {event_revision,subscription_generation,destination_revision}. The host checks the immutable event snapshot against the current app/connection, initiating user, destination proof, subscription, selected resource grant, source revision/ACL/visibility and expiry. Success is {authorized:true,event_uid,event_revision,subscription_generation,destination_revision,checked_at}. It returns no content, bearer execution permit or new consumer lease. The consumer calls this without caching after its existing durable queue claim and immediately before provider I/O, including every redelivery. HTTP403/409 forbids the send; HTTP503/timeout retains the same pending entry. This is a decision at checked_at, not a promise that authority cannot subsequently change. It does not resolve a provider send whose ACK was lost.
